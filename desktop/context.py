"""应用运行时：把 GUI 需要的单例组装到一起。

GUI 的所有页面共享同一个 ``AppContext``，避免各页各自 ``load_config()`` /
``get_db()`` 造成状态不一致。

线程约定：
- ``context`` 的属性只在主线程读写
- 涉及 IO 的方法由页面通过 ``bridge.tasks.run_async`` 丢到线程池
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from account import get_account_manager
from browser import describe_kernel, get_profile_manager
from config import load_config
from database import get_db
from logger import get_buffer, get_logger, setup_from_config
from proxy import get_proxy_manager

from .bridge.ipc import IpcServer, ipc_server_name
from .bridge.worker_proc import WorkerProcessManager


class AppContext(QObject):
    """GUI 侧运行时上下文。

    信号都在主线程发出（IPC 回调经 ``_relay`` 转投），页面可直接连 UI 槽。
    """

    log_received = Signal(dict)
    stats_received = Signal(dict)
    worker_state_changed = Signal(dict)
    ipc_connected = Signal(bool)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.cfg = load_config()
        self.cfg.ensure_dirs()
        setup_from_config(self.cfg)
        self.log = get_logger(name="desktop", flow="GUI")

        self.db = get_db(self.cfg.path_of("database.path", "data/app.db"))
        self.am = get_account_manager(self.cfg, self.db, logger=self.log)
        self.pm = get_profile_manager(self.cfg, self.db, logger=self.log)
        self.proxy = get_proxy_manager(self.cfg.section("proxy"), logger=self.log)

        self._ipc = IpcServer(self._on_ipc_message, address=ipc_server_name())
        self._ipc_started = False
        self._last_ipc_at = 0.0
        self.wpm = WorkerProcessManager(self.cfg, log=self.log)

    # ---------- 生命周期 ----------
    def start_ipc(self) -> str:
        """启动 IPC 监听。失败不致命——GUI 退回轮询模式。"""
        if self._ipc_started:
            return self._ipc.address
        try:
            address = self._ipc.start()
        except OSError as exc:
            self.log.warn("ipc", f"IPC 监听启动失败，降级为轮询: {exc}")
            self.wpm.ipc_address = ""
            return ""
        self._ipc_started = True
        self.wpm.ipc_address = address
        self.log.info("ipc", f"IPC 监听: {address}")
        return address

    def shutdown(self) -> None:
        """GUI 退出清理。注意：**不停止执行进程**（独立性是设计目标）。"""
        if self._ipc_started:
            self._ipc.stop()
            self._ipc_started = False

    # ---------- IPC ----------
    def _on_ipc_message(self, message: Dict[str, Any]) -> None:
        """IPC 后台线程回调。只能发信号，不能碰 UI。"""
        self._last_ipc_at = time.time()
        kind = message.get("kind")
        if kind == "log":
            self.log_received.emit(message)
        elif kind == "stats":
            self.stats_received.emit(message.get("snapshot") or {})
        elif kind in ("hello", "bye"):
            self.worker_state_changed.emit(message)
            self.ipc_connected.emit(kind == "hello")

    @property
    def ipc_fresh(self) -> bool:
        """最近 10 秒内收到过 IPC 消息，说明推送链路活着。"""
        return (time.time() - self._last_ipc_at) < 10.0

    # ---------- 数据聚合 ----------
    def stats_snapshot(self) -> Dict[str, Any]:
        """仪表盘数据。合并 DB 计数与执行进程落盘快照。

        逻辑与原 ``/api/stats`` 一致：DB 是真相源，实时统计（验证码通过率、
        浏览器实例数）来自执行进程快照。
        """
        proc = self.wpm.snapshot()
        live = proc.get("live") or {}
        counts = self.db.count_tasks()
        completed = counts.get("COMPLETED", 0)
        failed = counts.get("FAILED", 0)

        from flow import stats_snapshot as captcha_stats

        return {
            "ts": time.time(),
            "worker": proc,
            "ipc_fresh": self.ipc_fresh,
            "tasks": counts,
            "accounts": self.am.stats(),
            "queue": live.get("queue", {}),
            "browsers": live.get("browsers", 0),
            "captcha": live.get("captcha") or captcha_stats(),
            "processed": live.get("processed", completed + failed),
            "succeeded": live.get("succeeded", completed),
            "failed": live.get("failed", failed),
            "db": self.db.stats(),
            "proxy": self.proxy.snapshot(),
            "profiles": {"count": len(self.pm.list_dirs())},
            "kernel": describe_kernel(self.cfg),
        }

    def recent_logs(self, after_seq: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        """GUI 自身进程的日志缓冲（执行进程日志走 IPC 推送）。"""
        return get_buffer(after_seq=after_seq, limit=limit)
