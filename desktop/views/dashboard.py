"""仪表盘。

数据源与原 ``/api/stats`` 一致：SQLite 计数是真相源，实时数据
（浏览器实例数、验证码通过率）来自执行进程的落盘快照或 IPC 推送。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from account import describe
from database import AccountStatus

from ..theme import (
    COLOR_FAIL,
    COLOR_IDLE,
    COLOR_OK,
    COLOR_RUNNING,
    COLOR_WARN,
    TEXT,
    TEXT_DIM,
)
from .widgets import (
    KeyValueRow,
    MetricCard,
    button,
    hint_label,
    human_duration,
    title_label,
    toolbar,
)


class DashboardView(QWidget):
    """总览页：关键指标 + 执行状态 + 环境摘要。"""

    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.btn_refresh = button("刷新")
        self.btn_refresh.clicked.connect(
            lambda: self.update_state(self.ctx.stats_snapshot())
        )
        layout.addWidget(toolbar(title_label("仪表盘"), self.btn_refresh, stretch_at=0))

        # ---- 指标卡 ----
        self.cards: Dict[str, MetricCard] = {
            "total": MetricCard("账号总数", color=TEXT),
            "pending": MetricCard("待处理", color=COLOR_IDLE),
            "running": MetricCard("运行中", color=COLOR_RUNNING),
            "ok": MetricCard("成功", color=COLOR_OK),
            "wait_verify": MetricCard("等待验证", color=COLOR_WARN),
            "failed": MetricCard("失败", color=COLOR_FAIL),
        }
        grid = QGridLayout()
        grid.setSpacing(10)
        for index, key in enumerate(
            ["total", "pending", "running", "ok", "wait_verify", "failed"]
        ):
            grid.addWidget(self.cards[key], 0, index)
        layout.addLayout(grid)

        # ---- 执行状态 ----
        exec_box = QGroupBox("执行状态")
        exec_layout = QVBoxLayout(exec_box)
        self.rows: Dict[str, KeyValueRow] = {
            "worker": KeyValueRow("执行进程"),
            "queue": KeyValueRow("队列长度"),
            "browsers": KeyValueRow("浏览器实例"),
            "captcha": KeyValueRow("验证码通过率"),
            "ipc": KeyValueRow("实时推送"),
        }
        for row in self.rows.values():
            exec_layout.addWidget(row)
        layout.addWidget(exec_box)

        # ---- 环境 ----
        env_box = QGroupBox("环境")
        env_layout = QVBoxLayout(env_box)
        self.env_rows: Dict[str, KeyValueRow] = {
            "kernel": KeyValueRow("浏览器内核"),
            "proxy": KeyValueRow("代理"),
            "profiles": KeyValueRow("Profile 数量"),
            "db": KeyValueRow("数据库"),
            "data_dir": KeyValueRow("数据目录"),
        }
        for row in self.env_rows.values():
            env_layout.addWidget(row)
        layout.addWidget(env_box)

        layout.addWidget(
            hint_label(
                "账号状态明细见「账号管理」，执行控制见「任务管理」，"
                "实时日志见「运行日志」。"
            )
        )
        layout.addStretch(1)

    # ---------- 刷新 ----------
    def update_state(self, snapshot: Dict[str, Any]) -> None:
        accounts = snapshot.get("accounts") or {}
        by_status = accounts.get("by_status") or {}

        self.cards["total"].set_value(accounts.get("total", 0))
        self.cards["pending"].set_value(
            by_status.get(AccountStatus.NEW.value, 0)
            + by_status.get(AccountStatus.PENDING.value, 0)
        )
        self.cards["running"].set_value(by_status.get(AccountStatus.RUNNING.value, 0))
        self.cards["ok"].set_value(by_status.get(AccountStatus.OK.value, 0))
        self.cards["wait_verify"].set_value(
            by_status.get(AccountStatus.WAIT_VERIFY.value, 0)
        )
        failed = (
            by_status.get(AccountStatus.FAILED.value, 0)
            + by_status.get(AccountStatus.PASSWORD_WRONG.value, 0)
            + by_status.get(AccountStatus.LOCKED.value, 0)
            + by_status.get(AccountStatus.NOT_FOUND.value, 0)
        )
        self.cards["failed"].set_value(failed)

        worker = snapshot.get("worker") or {}
        if worker.get("running"):
            desc = f"运行中 · PID {worker.get('pid')} · {human_duration(worker.get('uptime'))}"
            if worker.get("external"):
                desc += " · 外部启动"
            self.rows["worker"].set_value(desc, COLOR_RUNNING)
        else:
            self.rows["worker"].set_value("未运行", TEXT_DIM)

        queue = snapshot.get("queue") or {}
        self.rows["queue"].set_value(str(queue.get("size", 0)))
        self.rows["browsers"].set_value(str(snapshot.get("browsers", 0)))

        captcha = snapshot.get("captcha") or {}
        self.rows["captcha"].set_value(self._format_captcha(captcha))

        if snapshot.get("ipc_fresh"):
            self.rows["ipc"].set_value("已连接", COLOR_OK)
        elif worker.get("running"):
            self.rows["ipc"].set_value("未连接（退回轮询）", COLOR_WARN)
        else:
            self.rows["ipc"].set_value("-", TEXT_DIM)

        kernel = snapshot.get("kernel") or {}
        if kernel.get("error"):
            self.env_rows["kernel"].set_value(str(kernel["error"]), COLOR_FAIL)
        else:
            label = {
                "fingerprint": "指纹内核 (fingerprint-chromium)",
                "patchright": "patchright 自带内核",
                "custom": "自定义路径",
            }.get(str(kernel.get("active_kernel")), "-")
            self.env_rows["kernel"].set_value(
                f"{label}  ·  {kernel.get('active_path') or '默认查找'}"
            )

        proxy = snapshot.get("proxy") or {}
        if proxy.get("direct"):
            self.env_rows["proxy"].set_value("直连", TEXT_DIM)
        else:
            self.env_rows["proxy"].set_value(
                f"{proxy.get('type', '-')} {proxy.get('host', '')}  ·  "
                f"{proxy.get('ports', 0)} 个端口"
            )

        self.env_rows["profiles"].set_value(
            str((snapshot.get("profiles") or {}).get("count", 0))
        )
        db = snapshot.get("db") or {}
        self.env_rows["db"].set_value(str(db.get("db_path") or "-"))
        self.env_rows["data_dir"].set_value(str(self.ctx.cfg.data_root))

    @staticmethod
    def _format_captcha(captcha: Dict[str, Any]) -> str:
        """对接 flow.captcha.stats_snapshot() 的字段。"""
        if not captcha:
            return "-"
        attempts = int(captcha.get("attempts") or 0)
        success = int(captcha.get("success") or 0)
        if not attempts:
            return "暂无数据"
        rate = captcha.get("rate")
        if rate is None:
            rate = success / attempts * 100
        return f"{success}/{attempts}  ({rate:.0f}%)"
