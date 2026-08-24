"""GUI 后台任务：把阻塞操作赶出主线程。

GUI 进程不开浏览器，但仍有两类阻塞操作会卡住窗口：

- SQLite 大批量读写（导入上万账号、导出、批量删除）
- 网络与文件 IO（代理测试、ipinfo 查询、Profile 目录递归删除、内核下载）

``database/sqlite.py`` 是共享单连接 + RLock，写入互斥串行，所以后台任务里的
大事务要分批提交，避免把锁抽住影响另一进程的 Worker。
"""

from __future__ import annotations

import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class WorkerSignals(QObject):
    """QRunnable 不能直接带信号，单独拆一个 QObject 承载。"""

    finished = Signal(object)          # 成功：返回值
    failed = Signal(str)               # 失败：错误摘要
    progress = Signal(int, int, str)   # 当前 / 总数 / 描述
    done = Signal()                    # 无论成败都触发，用于恢复按钮状态


class BackgroundTask(QRunnable):
    """在线程池中执行一个可调用对象。

    ``fn`` 可选地接受 ``progress`` 关键字参数（一个 ``callable(cur, total, text)``），
    用于回报进度。
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.signals = WorkerSignals()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @Slot()
    def run(self) -> None:  # pragma: no cover - 线程池执行
        try:
            if self._accepts_progress():
                self._kwargs["progress"] = self._emit_progress
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            detail = f"{exc.__class__.__name__}: {exc}"
            traceback.print_exc()
            self.signals.failed.emit(detail)
        else:
            self.signals.finished.emit(result)
        finally:
            self.signals.done.emit()

    def _accepts_progress(self) -> bool:
        try:
            import inspect

            sig = inspect.signature(self._fn)
        except (TypeError, ValueError):
            return False
        return "progress" in sig.parameters

    def _emit_progress(self, current: int, total: int, text: str = "") -> None:
        self.signals.progress.emit(int(current), int(total), text)


def run_async(
    fn: Callable[..., Any],
    *args: Any,
    on_result: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    on_done: Optional[Callable[[], None]] = None,
    pool: Optional[QThreadPool] = None,
    **kwargs: Any,
) -> BackgroundTask:
    """提交后台任务并连接回调。回调都在主线程执行。"""
    task = BackgroundTask(fn, *args, **kwargs)
    if on_result is not None:
        task.signals.finished.connect(on_result)
    if on_error is not None:
        task.signals.failed.connect(on_error)
    if on_progress is not None:
        task.signals.progress.connect(on_progress)
    if on_done is not None:
        task.signals.done.connect(on_done)
    (pool or QThreadPool.globalInstance()).start(task)
    return task
