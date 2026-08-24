"""GUI 后台任务：把阻塞操作赶出主线程。

GUI 进程不开浏览器，但仍有两类阻塞操作会卡住窗口：

- SQLite 大批量读写（导入上万账号、导出、批量删除）
- 网络与文件 IO（代理测试、ipinfo 查询、Profile 目录递归删除、内核下载）

``database/sqlite.py`` 是共享单连接 + RLock，写入互斥串行，所以后台任务里的
大事务要分批提交，避免把锁抽住影响另一进程的 Worker。

**生命周期陷阱（曾导致所有回调静默丢失）**

``QThreadPool.start()`` 默认接管 QRunnable 并在 ``run()`` 返回后删除它。
若 Python 侧不保留引用，任务对象会被 GC，它持有的 ``WorkerSignals``
一起销毁 —— 已排队但尚未投递到主线程的信号全部被丢弃。
表现是：业务函数确实执行了（配置真的保存了），但 ``on_result`` /
``on_done`` 一次都不触发，于是没有成功提示、按钮永久停在禁用状态。

所以这里做两件事：关掉 autoDelete，并把在飞任务登记进模块级集合，
收到 ``done`` 后再移除。
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, Optional, Set

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class WorkerSignals(QObject):
    """QRunnable 不能直接带信号，单独拆一个 QObject 承载。"""

    finished = Signal(object)          # 成功：返回值
    failed = Signal(str)               # 失败：错误摘要
    progress = Signal(int, int, str)   # 当前 / 总数 / 描述
    done = Signal()                    # 无论成败都触发，用于恢复按钮状态


#: 在飞任务登记表。防止任务对象被 GC 导致信号投递丢失。
_inflight: Set["BackgroundTask"] = set()
_inflight_lock = threading.Lock()


def _register(task: "BackgroundTask") -> None:
    with _inflight_lock:
        _inflight.add(task)


def _unregister(task: "BackgroundTask") -> None:
    with _inflight_lock:
        _inflight.discard(task)


def inflight_count() -> int:
    """在飞任务数。供测试与状态栏使用。"""
    with _inflight_lock:
        return len(_inflight)


class BackgroundTask(QRunnable):
    """在线程池中执行一个可调用对象。

    ``fn`` 可选地接受 ``progress`` 关键字参数（一个 ``callable(cur, total, text)``），
    用于回报进度。
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        # 不让 Qt 删除本对象：Python 侧仍需它活到信号投递完成
        self.setAutoDelete(False)
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
    """提交后台任务并连接回调。回调都在主线程执行。

    调用方无需保留返回值 —— 任务在完成前由模块内部持有引用。
    """
    task = BackgroundTask(fn, *args, **kwargs)
    if on_result is not None:
        task.signals.finished.connect(on_result)
    if on_error is not None:
        task.signals.failed.connect(on_error)
    if on_progress is not None:
        task.signals.progress.connect(on_progress)
    if on_done is not None:
        task.signals.done.connect(on_done)

    # 登记必须在 start 之前：任务可能瞬间完成
    _register(task)
    # 用默认参数绑定 task，避免闭包捕获导致的循环引用歧义
    task.signals.done.connect(lambda t=task: _unregister(t))

    (pool or QThreadPool.globalInstance()).start(task)
    return task


def wait_for_idle(timeout_ms: int = 5000, pool: Optional[QThreadPool] = None) -> bool:
    """等待线程池空闲。仅供测试与退出前收尾使用，不要在 UI 线程常规调用。"""
    return (pool or QThreadPool.globalInstance()).waitForDone(timeout_ms)
