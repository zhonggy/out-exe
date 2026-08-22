"""Worker：从队列取任务 → 起浏览器 → 跑流程 → 写状态 → 重试/收尾。

每个 Worker 是独立线程，独立浏览器实例（Patchright 同步 API 要求每线程各自
sync_playwright()，BrowserSession 内部已按线程创建，互不干扰）。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from account import AccountManager, StatusVerdict, verdict_from_exception
from browser import BrowserLaunchError, BrowserManager
from database import AccountStatus, Database, FlowStage, Task, TaskStatus
from flow import CheckpointManager, FlowResult, get_flow
from logger import get_logger

from .queue import TaskQueue


class Worker(threading.Thread):
    """任务执行线程。"""

    def __init__(
        self,
        index: int,
        cfg,
        db: Database,
        queue: TaskQueue,
        browser_manager: BrowserManager,
        account_manager: AccountManager,
        proxy_manager=None,
        stop_event: Optional[threading.Event] = None,
        on_finish=None,
    ):
        super().__init__(name=f"worker-{index}", daemon=True)
        self.index = index
        self.cfg = cfg
        self.db = db
        self.queue = queue
        self.bm = browser_manager
        self.am = account_manager
        self.proxy_manager = proxy_manager
        self.stop_event = stop_event or threading.Event()
        self.on_finish = on_finish

        self.log = get_logger(name=f"worker{index}", flow="WORKER")
        self.current_task: Optional[Task] = None
        self.processed = 0
        self.succeeded = 0
        self.failed = 0
        self.started_at: Optional[float] = None

    # ---------- 线程主体 ----------
    def run(self) -> None:
        self.started_at = time.time()
        self.log.info("worker_start", f"Worker {self.index} 启动")
        while not self.stop_event.is_set():
            task = self.queue.get(timeout=1.0)
            if task is None:
                if self.queue.closed:
                    break
                continue
            if self.stop_event.is_set():
                self.db.update_task(task.id, status=TaskStatus.QUEUED.value)
                break
            self.current_task = task
            try:
                self._execute(task)
            except Exception as exc:  # 兜底，Worker 不能因单任务异常退出
                self.log.exception("worker_loop", f"任务 {task.id} 未捕获异常", exc)
            finally:
                self.current_task = None
        self.log.info("worker_stop", f"Worker {self.index} 退出，处理 {self.processed} 个任务")

    # ---------- 单任务 ----------
    def _execute(self, task: Task) -> None:
        # 跨进程取消保护：面板可能已把任务标为 CANCELLED（独立执行进程模式）
        fresh = self.db.get_task(task.id)
        if fresh is not None and fresh.status == TaskStatus.CANCELLED.value:
            self.log.info("task_skip", f"任务 {task.id} 已被取消，跳过")
            return

        task.attempt += 1
        task.start_time = task.start_time or time.time()
        self.db.update_task(
            task.id,
            status=TaskStatus.RUNNING.value,
            attempt=task.attempt,
            start_time=task.start_time,
            stage=FlowStage.CREATED.value,
        )
        log = get_logger(
            name=f"worker{self.index}", flow=task.type.upper(), task_id=task.id, account=task.account
        )
        log.info("task_start", f"开始执行 第{task.attempt}/{task.max_attempt}次尝试")

        if task.account:
            self.am.mark_running(task.account)
        self.db.add_event(task.type.upper(), "INFO", "task_start", "任务开始", task.id, task.account)

        ckpt = CheckpointManager(
            self.db,
            task_id=task.id,
            account=task.account,
            enabled=bool(self.cfg.get("flow.checkpoint_enabled", True)),
            logger=log,
        )

        flow_cls = get_flow(task.type)
        if flow_cls is None:
            self._finalize(
                task,
                FlowResult(
                    False,
                    FlowStage.FAILED.value,
                    verdict=StatusVerdict(AccountStatus.FAILED.value, f"未知流程类型: {task.type}"),
                    message=f"未知流程类型: {task.type}",
                ),
                log,
            )
            return

        password = task.payload.get("password") or self.am.password_of(task.account)
        session = None
        result: FlowResult
        try:
            session = self.bm.create(account=task.account, key=f"task{task.id}")
            task.profile_id = session.profile.profile_id if session.profile else ""
            task.proxy = session.proxy_url
            self.db.update_task(task.id, profile_id=task.profile_id, proxy=task.proxy)

            flow = flow_cls(
                session,
                self.cfg,
                logger=log,
                checkpoint=ckpt,
                proxy_manager=self.proxy_manager,
            )
            result = flow.run(account=task.account, password=password, **task.payload)
        except BrowserLaunchError as exc:
            log.fail("browser_launch", f"浏览器启动失败: {exc}")
            result = FlowResult(
                False,
                FlowStage.BROWSER_STARTED.value,
                verdict=StatusVerdict(AccountStatus.FAILED.value, f"浏览器启动失败: {exc}", retryable=True),
                message=str(exc),
                retryable=True,
            )
        except Exception as exc:
            log.exception("task_run", "任务执行异常", exc)
            verdict = verdict_from_exception(exc)
            result = FlowResult(
                False,
                FlowStage.FAILED.value,
                verdict=verdict,
                message=verdict.reason,
                retryable=verdict.retryable,
            )
        finally:
            if session is not None:
                try:
                    self.bm.release(session, broken=False)
                except Exception:
                    pass

        self._finalize(task, result, log)

    # ---------- 收尾 ----------
    def _finalize(self, task: Task, result: FlowResult, log) -> None:
        self.processed += 1
        verdict = result.verdict or StatusVerdict(
            AccountStatus.OK.value if result.success else AccountStatus.FAILED.value, result.message
        )

        can_retry = (
            not result.success
            and result.retryable
            and task.attempt < task.max_attempt
            and not self.stop_event.is_set()
        )

        if result.success:
            self.succeeded += 1
            status = TaskStatus.COMPLETED.value
        elif can_retry:
            status = TaskStatus.QUEUED.value
        else:
            self.failed += 1
            status = TaskStatus.FAILED.value

        self.db.update_task(
            task.id,
            status=status,
            stage=result.stage,
            result=verdict.status,
            error="" if result.success else verdict.reason,
            end_time=None if can_retry else time.time(),
        )
        self.db.add_event(
            task.type.upper(),
            "OK" if result.success else "FAIL",
            "task_end",
            f"{verdict.status}: {verdict.reason}",
            task.id,
            task.account,
        )

        if task.account:
            if can_retry:
                # 保持 NEW，等重试；避免账号停留在 RUNNING
                self.am.reset_status(task.account, AccountStatus.NEW.value)
            else:
                self.am.apply_verdict(task.account, verdict)

        log.event(
            "OK" if result.success else "FAIL",
            "task_end",
            f"{verdict.status} | {verdict.reason}"
            + (f" | 将重试({task.attempt}/{task.max_attempt})" if can_retry else ""),
        )

        if can_retry:
            self.queue.requeue(task)
        elif self.on_finish:
            try:
                self.on_finish(task, result)
            except Exception:
                pass

    # ---------- 状态 ----------
    def snapshot(self) -> Dict[str, Any]:
        task = self.current_task
        return {
            "index": self.index,
            "name": self.name,
            "alive": self.is_alive(),
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "current_task": task.id if task else None,
            "current_account": task.account if task else "",
            "uptime": round(time.time() - self.started_at, 1) if self.started_at else 0,
        }
