"""主窗口：左侧导航 + 页面栈 + 状态栏。

刷新策略：**只有主窗口有定时器**，统一取一次 ``stats_snapshot()`` 后分发给
各页面。各页自起定时器会导致同一秒内重复查库（SQLite 是共享单连接 + 写锁，
并发查询会互相排队）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from .theme import COLOR_OK, COLOR_RUNNING, COLOR_WARN, TEXT_DIM
from .views import (
    AccountsView,
    BrowserView,
    DashboardView,
    LogsView,
    ProfilesView,
    ProxyView,
    SettingsView,
    TasksView,
)
from .views.widgets import confirm

#: 导航项：(标题, 页面类)
_PAGES: List[Tuple[str, Any]] = [
    ("仪表盘", DashboardView),
    ("账号管理", AccountsView),
    ("任务管理", TasksView),
    ("运行日志", LogsView),
    ("浏览器", BrowserView),
    ("Profile", ProfilesView),
    ("代理", ProxyView),
    ("设置", SettingsView),
]


class MainWindow(QMainWindow):
    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self.setWindowTitle("OutlookAutomation")
        self.resize(1280, 820)
        self.setMinimumSize(1024, 680)

        self._pages: Dict[str, QWidget] = {}
        self._build()
        self._start_timers()
        self._refresh_state()

    # ---------- 构建 ----------
    def _build(self) -> None:
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setProperty("role", "nav")
        self.nav.setFixedWidth(160)
        self.nav.setFocusPolicy(Qt.NoFocus)

        self.stack = QStackedWidget()

        for title, page_class in _PAGES:
            page = page_class(self.ctx)
            self._pages[title] = page
            self.stack.addWidget(page)
            self.nav.addItem(QListWidgetItem(title))
            # 数据变更后立即刷新总览，不用等下一个定时周期
            if hasattr(page, "data_changed"):
                page.data_changed.connect(self._refresh_state)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)

        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._build_status_bar()

    def _build_status_bar(self) -> None:
        bar = self.statusBar()

        self.status_message = QLabel("就绪")
        self.status_worker = QLabel("执行进程：未运行")
        self.status_ipc = QLabel("推送：-")
        self.status_data = QLabel(str(self.ctx.cfg.data_root))
        self.status_data.setToolTip("用户数据目录")

        for widget in (self.status_worker, self.status_ipc, self.status_data):
            widget.setStyleSheet(f"color: {TEXT_DIM};")

        bar.addWidget(self.status_message, 1)
        bar.addPermanentWidget(self.status_worker)
        bar.addPermanentWidget(self.status_ipc)
        bar.addPermanentWidget(self.status_data)

    # ---------- 定时刷新 ----------
    def _start_timers(self) -> None:
        interval = int(self.ctx.cfg.get("desktop.refresh_interval", 2000) or 2000)
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(max(500, interval))
        self._state_timer.timeout.connect(self._refresh_state)
        self._state_timer.start()

        # GUI 自身日志的兜底轮询（执行进程日志走 IPC 推送）
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(1000)
        self._log_timer.timeout.connect(self._poll_logs)
        self._log_timer.start()

    def _refresh_state(self) -> None:
        snapshot = self.ctx.stats_snapshot()
        for page in self._pages.values():
            if hasattr(page, "update_state"):
                page.update_state(snapshot)
        self._update_status_bar(snapshot)

    def _poll_logs(self) -> None:
        page = self._pages.get("运行日志")
        if page is not None:
            page.poll_gui_logs()

    def _update_status_bar(self, snapshot: Dict[str, Any]) -> None:
        worker = snapshot.get("worker") or {}
        if worker.get("running"):
            text = f"执行进程：运行中 (PID {worker.get('pid')})"
            color = COLOR_RUNNING
        else:
            text = "执行进程：未运行"
            color = TEXT_DIM
        self.status_worker.setText(text)
        self.status_worker.setStyleSheet(f"color: {color};")

        if snapshot.get("ipc_fresh"):
            self.status_ipc.setText("推送：已连接")
            self.status_ipc.setStyleSheet(f"color: {COLOR_OK};")
        elif worker.get("running"):
            self.status_ipc.setText("推送：未连接")
            self.status_ipc.setStyleSheet(f"color: {COLOR_WARN};")
        else:
            self.status_ipc.setText("推送：-")
            self.status_ipc.setStyleSheet(f"color: {TEXT_DIM};")

    # ---------- 交互 ----------
    def _on_nav_changed(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        title = _PAGES[row][0]
        page = self._pages.get(title)
        # 切页时主动刷一次，列表页数据不至于是上次离开时的
        if page is not None and hasattr(page, "refresh"):
            page.refresh()

    def show_status(self, message: str) -> None:
        """页面回报操作结果。"""
        self.status_message.setText(message)

    def reload_runtime_settings(self) -> None:
        """设置页保存后调用：应用影响 GUI 自身的配置。"""
        interval = int(self.ctx.cfg.get("desktop.refresh_interval", 2000) or 2000)
        self._state_timer.setInterval(max(500, interval))

    # ---------- 关闭 ----------
    def closeEvent(self, event: QCloseEvent) -> None:
        """关窗不停任务（现有行为，也是设计目标），但要让用户知道。"""
        running = bool((self.ctx.stats_snapshot().get("worker") or {}).get("running"))
        if running:
            if not confirm(
                self,
                "关闭窗口",
                "执行进程仍在运行，关闭窗口不会中断任务。\n\n"
                "任务会在后台继续，重新打开程序可恢复查看。\n"
                "要停止任务请先到「任务管理」点「停止执行」。\n\n"
                "确认关闭窗口？",
            ):
                event.ignore()
                return
        self._state_timer.stop()
        self._log_timer.stop()
        event.accept()
