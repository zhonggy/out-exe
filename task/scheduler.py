"""任务调度器：Worker 池管理 + 批量派发 + 定时任务（APScheduler 可选）。

TaskManager 是运行时的中枢，API 层与 CLI 都通过它操作：
    start() 起 Worker 池 → submit_*() 派发任务 → stats() 查状态 → stop() 收尾
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from account import AccountManager, get_account_manager
from browser import BrowserManager, get_browser_manager
from database import Database, Task, TaskStatus, get_db
from flow import list_flows, stats_snapshot as captcha_stats
from logger import get_logger
from proxy import ProxyManager, get_proxy_manager

from .queue import TaskQueue
from .worker import Worker


class TaskManager:
    """任务生命周期总控。"""

    def __init__(
        self,
        cfg,
        db: Optional[Database] = None,
        logger=None,
        browser_manager: Optional[BrowserManager] = None,
        account_manager: Optional[AccountManager] = None,
        proxy_manager: Optional[ProxyManager] = None,
    ):
        self.cfg = cfg
        self.log = logger or get_logger(name="task", flow="TASK")
        self.db = db or get_db(cfg.path_of("database.path", "data/app.db"))
        self.pm_proxy = proxy_manager or get_proxy_manager(cfg.section("proxy"), logger=self.log)
        self.bm = browser_manager or get_browser_manager(cfg, logger=self.log)
        self.am = account_manager or get_account_manager(cfg, self.db, logger=self.log)

        self.queue = TaskQueue(self.db, logger=self.log)
        self.workers: List[Worker] = []
        self.stop_event = threading.Event()
        self._lock = threading.RLock()
        self._scheduler = None
        self.started_at: Optional[float] = None

    # ---------- Worker 池 ----------
    def start(self, workers: Optional[int] = None, restore: bool = True) -> int:
        """启动 Worker 池。restore=True 时恢复上次未完成任务。"""
        with self._lock:
            if self.workers:
                return len(self.workers)
            count = int(workers or self.cfg.get("system.max_workers", 1))
            count = max(1, count)
            # 上次 stop() 已把队列关闭：重建队列，否则新 Worker 会立即退出
            if self.queue.closed:
                self.queue = TaskQueue(self.db, logger=self.log)
            self.stop_event.clear()
            self.started_at = time.time()

            if restore:
                self.am.reset_non_terminal()
                self.db.release_all_profiles()
                self.db.reset_stale_running()
                self.queue.restore()

            for i in range(count):
                worker = Worker(
                    index=i + 1,
                    cfg=self.cfg,
                    db=self.db,
                    queue=self.queue,
                    browser_manager=self.bm,
                    account_manager=self.am,
                    proxy_manager=self.pm_proxy,
                    stop_event=self.stop_event,
                )
                worker.start()
                self.workers.append(worker)
            self.log.ok("manager_start", f"已启动 {count} 个并发线程")
            return count

    def stop(self, wait: bool = True, timeout: float = 30.0) -> None:
        """停止 Worker 池并关闭所有浏览器。"""
        with self._lock:
            if not self.workers and not self.stop_event.is_set():
                self.stop_event.set()
                self.queue.close()
                return
            self.log.info("manager_stop", "正在停止 Worker...")
            self.stop_event.set()
            self.queue.close()
            workers = list(self.workers)
            self.workers.clear()

        if wait:
            deadline = time.time() + timeout
            for worker in workers:
                remain = max(0.5, deadline - time.time())
                worker.join(timeout=remain)
        closed = self.bm.close_all()
        self.log.ok("manager_stop", f"Worker 已停止，关闭 {closed} 个浏览器")

    def restart(self, workers: Optional[int] = None) -> int:
        self.stop()
        self.queue = TaskQueue(self.db, logger=self.log)
        return self.start(workers=workers)

    @property
    def running(self) -> bool:
        return bool(self.workers) and not self.stop_event.is_set()

    # ---------- 派发 ----------
    def submit(
        self,
        account: str,
        task_type: str = "login",
        priority: int = 0,
        password: str = "",
        max_attempt: Optional[int] = None,
    ) -> Task:
        payload: Dict[str, Any] = {}
        if password:
            payload["password"] = password
        acc = self.am.get(account)
        return self.queue.create(
            account=account,
            task_type=task_type,
            priority=priority,
            max_attempt=int(max_attempt if max_attempt is not None else self.cfg.get("system.task_retry", 1) + 1),
            payload=payload,
            account_id=acc.id if acc else None,
        )

    def submit_batch(
        self,
        accounts: Optional[List[str]] = None,
        task_type: str = "login",
        limit: int = 100,
        priority: int = 0,
    ) -> List[Task]:
        """批量派发。accounts 为空时自动从待处理账号中取 limit 个。"""
        if accounts:
            targets = accounts[:limit]
        else:
            targets = [a.account for a in self.am.claim_batch(limit=limit)]
        tasks = [self.submit(acc, task_type=task_type, priority=priority) for acc in targets]
        self.log.ok("submit_batch", f"派发 {len(tasks)} 个 {task_type} 任务")
        return tasks

    def cancel(self, task_id: int) -> bool:
        task = self.db.get_task(task_id)
        if not task or task.status in [s.value for s in TaskStatus.terminal()]:
            return False
        self.db.update_task(task_id, status=TaskStatus.CANCELLED.value, end_time=time.time())
        if task.account:
            self.am.reset_status(task.account)
        return True

    def clear_queue(self) -> int:
        return self.queue.clear()

    # ---------- 定时任务 ----------
    def start_scheduler(self) -> bool:
        """启动 APScheduler，按 config.scheduler.jobs 注册 cron 任务。"""
        if not bool(self.cfg.get("scheduler.enabled", False)):
            return False
        jobs = self.cfg.get("scheduler.jobs", []) or []
        if not jobs:
            self.log.info("scheduler", "scheduler.enabled=true 但未配置 jobs")
            return False
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            self.log.warn("scheduler", "未安装 APScheduler，跳过定时任务")
            return False

        scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        registered = 0
        for job in jobs:
            cron = str(job.get("cron") or "").strip()
            if not cron:
                continue
            flow_name = str(job.get("flow") or "login")
            limit = int(job.get("limit", 100))
            job_id = str(job.get("id") or f"{flow_name}_{registered}")
            try:
                scheduler.add_job(
                    self.submit_batch,
                    CronTrigger.from_crontab(cron),
                    id=job_id,
                    kwargs={"task_type": flow_name, "limit": limit},
                    replace_existing=True,
                )
                registered += 1
            except Exception as exc:
                self.log.warn("scheduler", f"注册定时任务 {job_id} 失败: {exc}")
        if registered:
            scheduler.start()
            self._scheduler = scheduler
            self.log.ok("scheduler", f"已注册 {registered} 个定时任务")
        return registered > 0

    def stop_scheduler(self) -> None:
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    def scheduler_jobs(self) -> List[Dict[str, Any]]:
        if self._scheduler is None:
            return []
        out = []
        for job in self._scheduler.get_jobs():
            out.append(
                {
                    "id": job.id,
                    "next_run": str(getattr(job, "next_run_time", "")),
                    "trigger": str(job.trigger),
                }
            )
        return out

    # ---------- 状态 ----------
    def stats(self) -> Dict[str, Any]:
        worker_stats = [w.snapshot() for w in self.workers]
        return {
            "running": self.running,
            "uptime": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "workers": worker_stats,
            "worker_count": len(self.workers),
            "queue": self.queue.snapshot(),
            "tasks": self.db.count_tasks(),
            "accounts": self.am.stats(),
            "browsers": self.bm.active_count(),
            "captcha": captcha_stats(),
            "processed": sum(w["processed"] for w in worker_stats),
            "succeeded": sum(w["succeeded"] for w in worker_stats),
            "failed": sum(w["failed"] for w in worker_stats),
            "flows": sorted(list_flows()),
            "scheduler": self.scheduler_jobs(),
        }

    def wait_idle(self, timeout: Optional[float] = None, poll: float = 1.0) -> bool:
        """等待队列清空且所有 Worker 空闲。返回是否成功等到空闲。"""
        deadline = None if timeout is None else time.time() + timeout
        while True:
            busy = any(w.current_task is not None for w in self.workers)
            if self.queue.size() == 0 and not busy:
                return True
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(poll)


_manager: Optional[TaskManager] = None
_manager_lock = threading.Lock()


def get_task_manager(cfg=None, logger=None) -> TaskManager:
    """进程级单例。API 层与 CLI 共用同一实例。"""
    global _manager
    with _manager_lock:
        if _manager is None:
            if cfg is None:
                from config import load_config

                cfg = load_config()
            _manager = TaskManager(cfg, logger=logger)
        return _manager


def reset_task_manager() -> None:
    global _manager
    with _manager_lock:
        if _manager is not None:
            try:
                _manager.stop(wait=False)
                _manager.stop_scheduler()
            except Exception:
                pass
        _manager = None
