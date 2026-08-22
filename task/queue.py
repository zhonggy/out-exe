"""任务队列：内存优先队列 + SQLite 持久化。

- 创建任务时写库，同时入内存队列
- Worker 从队列取任务；进程重启后可用 restore() 把库里未完成任务重新入队
- 支持优先级（priority 越大越先执行）
"""

from __future__ import annotations

import heapq
import itertools
import threading
from typing import Any, Dict, List, Optional

from database import Database, Task, TaskStatus


class TaskQueue:
    """线程安全优先队列。put/get 均为原子操作。"""

    def __init__(self, db: Database, logger=None):
        self.db = db
        self.log = logger
        self._heap: List[tuple] = []
        self._counter = itertools.count()
        self._ids: set = set()   # 堆内任务 id 去重（支持周期性 restore 补拉新任务）
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._closed = False

    # ---------- 入队 ----------
    def create(
        self,
        account: str,
        task_type: str = "login",
        priority: int = 0,
        max_attempt: int = 1,
        payload: Optional[Dict[str, Any]] = None,
        account_id: Optional[int] = None,
    ) -> Task:
        """创建任务并入队。"""
        task = Task(
            type=task_type,
            account=account,
            account_id=account_id,
            status=TaskStatus.QUEUED.value,
            priority=priority,
            max_attempt=max(1, max_attempt),
            payload=payload or {},
        )
        self.db.create_task(task)
        self.put(task)
        if self.log:
            self.log.info("task_create", f"task={task.id} type={task_type} account={account}")
        return task

    def put(self, task: Task) -> None:
        """已存在的任务入队。同一任务已在堆内时忽略（幂等）。"""
        with self._not_empty:
            if self._closed:
                return
            if task.id is not None and task.id in self._ids:
                return
            if task.id is not None:
                self._ids.add(task.id)
            heapq.heappush(self._heap, (-task.priority, next(self._counter), task))
            self._not_empty.notify()
        if task.status != TaskStatus.QUEUED.value:
            self.db.update_task(task.id, status=TaskStatus.QUEUED.value)
            task.status = TaskStatus.QUEUED.value

    def requeue(self, task: Task) -> None:
        """重试：attempt 已在 Worker 里累加，这里只放回队列。"""
        self.put(task)
        if self.log:
            self.log.info("task_requeue", f"task={task.id} attempt={task.attempt}")

    # ---------- 出队 ----------
    def get(self, timeout: Optional[float] = 1.0) -> Optional[Task]:
        """取一个任务。超时或队列已关闭返回 None。

        关闭后立即停止派发（即使堆里还有任务），保证 stop() 可预期收敛；
        未派发任务仍是 QUEUED 状态，下次 restore() 会重新入队。
        """
        with self._not_empty:
            while not self._heap:
                if self._closed:
                    return None
                if not self._not_empty.wait(timeout=timeout):
                    return None
            if self._closed:
                return None
            _prio, _seq, task = heapq.heappop(self._heap)
            if task.id is not None:
                self._ids.discard(task.id)
            return task

    # ---------- 维护 ----------
    def restore(self, reset_running: bool = False) -> int:
        """从数据库补拉未完成任务入队（幂等，可周期调用）。

        reset_running=True 仅用于启动时：把上次异常退出的 RUNNING 任务打回 QUEUED。
        周期性补拉不能重置 RUNNING，否则会把正在执行的任务重复入队。
        """
        if reset_running:
            self.db.reset_stale_running()
        pending = self.db.pending_tasks(limit=10000)
        added = 0
        for task in pending:
            if task.id is None or task.id not in self._ids:
                self.put(task)
                added += 1
        if self.log and added:
            self.log.info("task_restore", f"补拉 {added} 个未完成任务")
        return added

    def size(self) -> int:
        with self._lock:
            return len(self._heap)

    def clear(self) -> int:
        """清空内存队列，并把这些任务标记为 CANCELLED。"""
        with self._lock:
            items = [t for _p, _s, t in self._heap]
            self._heap.clear()
            self._ids.clear()
        for task in items:
            self.db.update_task(task.id, status=TaskStatus.CANCELLED.value)
        return len(items)

    def close(self) -> None:
        """关闭队列，唤醒所有等待的 Worker。"""
        with self._not_empty:
            self._closed = True
            self._not_empty.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            queued = [
                {
                    "id": t.id,
                    "type": t.type,
                    "account": t.account,
                    "priority": t.priority,
                    "attempt": t.attempt,
                }
                for _p, _s, t in sorted(self._heap)
            ]
        return {"size": len(queued), "closed": self._closed, "items": queued[:50]}
