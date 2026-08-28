"""代理页。

现有代码有两套并存机制，必须让用户看清哪套在生效：

- ``proxy.*``  — 本地代理池（single / pool），带加权选择、IP 表现追踪、失败惩罚
- ``resin.*``  — 外部粘性代理池，**启用后优先于** proxy 段（见 browser/browser.py）

旧 Web 面板没把这个优先级讲清楚，容易误以为两个都在生效。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..bridge.tasks import run_async
from ..theme import (
    COLOR_FAIL,
    COLOR_OK,
    COLOR_WARN,
    TEXT_DIM,
    apply_row_height,
    fit_all_checkboxes,
    fit_input,
)
from .widgets import (
    KeyValueRow,
    align_form_labels,
    attach_overflow_tooltip,
    button,
    confirm,
    error,
    form_label,
    hint_label,
    info,
    notify,
    spinbox,
    title_label,
    toolbar,
)


class ProxyTableModel(QAbstractTableModel):
    """IP 表现追踪表（来自 ProxyManager.snapshot()['tracker']）。"""

    COLUMNS = [
        ("代理", "proxy"),
        ("成功", "win"),
        ("总计", "total"),
        ("成功率", "rate"),
        ("状态", "blacklisted"),
    ]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return self.COLUMNS[section][0]

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        key = self.COLUMNS[index.column()][1]

        if role == Qt.DisplayRole:
            if key == "rate":
                rate = row.get("rate")
                return "-" if rate is None else f"{rate}%"
            if key == "blacklisted":
                return "已拉黑" if row.get("blacklisted") else "正常"
            value = row.get(key)
            return "-" if value in (None, "") else str(value)

        if role == Qt.ForegroundRole and key == "blacklisted":
            return QColor(COLOR_FAIL if row.get("blacklisted") else COLOR_OK)

        if role == Qt.TextAlignmentRole and key in ("win", "total", "rate"):
            return int(Qt.AlignCenter)

        return None

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


class ProxyView(QWidget):
    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(title_label("代理"))

        self.active_label = QLabel("-")
        self.active_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.active_label)

        layout.addWidget(self._build_local_box())
        layout.addWidget(self._build_resin_box())
        layout.addWidget(self._build_tracker_box(), 1)

        fit_all_checkboxes(self)
        align_form_labels(self)

    # ---------- 本地代理池 ----------
    def _build_local_box(self) -> QWidget:
        box = QGroupBox("本地代理池（proxy）")
        layout = QVBoxLayout(box)

        self.local_enabled = QCheckBox("启用")
        self.local_enabled.stateChanged.connect(self._update_active_label)
        self.local_mode = QComboBox()
        self.local_mode.addItem("单代理 single", "single")
        self.local_mode.addItem("端口池 pool", "pool")
        self.local_type = QComboBox()
        self.local_type.addItem("http", "http")
        self.local_type.addItem("socks5", "socks5")
        self.local_host = QLineEdit()
        # 可能填域名而非 IP，给足余量
        fit_input(self.local_host, "proxy.example.com", extra=32)
        self.local_single_port = spinbox(1, 65535)
        self.local_port_start = spinbox(1, 65535)
        self.local_port_end = spinbox(1, 65535)

        layout.addWidget(
            toolbar(
                self.local_enabled,
                form_label("模式"),
                self.local_mode,
                form_label("协议"),
                self.local_type,
                form_label("主机"),
                self.local_host,
                stretch_at=6,
            )
        )
        layout.addWidget(
            toolbar(
                form_label("单端口"),
                self.local_single_port,
                form_label("池起始"),
                self.local_port_start,
                form_label("池结束"),
                self.local_port_end,
                stretch_at=5,
            )
        )

        self.btn_save_local = button("保存", "primary")
        self.btn_reset_stats = button("重置表现统计", tooltip="清空 IP 成功率与黑名单")
        self.btn_pick = button("试取一个代理", "outline")
        self.btn_save_local.clicked.connect(self._on_save_local)
        self.btn_reset_stats.clicked.connect(self._on_reset_stats)
        self.btn_pick.clicked.connect(self._on_pick)
        layout.addWidget(
            toolbar(self.btn_save_local, self.btn_reset_stats, self.btn_pick, stretch_at=2)
        )
        return box

    # ---------- Resin ----------
    def _build_resin_box(self) -> QWidget:
        box = QGroupBox("Resin 外部粘性代理池（resin）")
        layout = QVBoxLayout(box)

        self.resin_enabled = QCheckBox("启用（优先于本地代理池）")
        self.resin_enabled.stateChanged.connect(self._update_active_label)

        self.resin_url = QLineEdit()
        self.resin_url.setPlaceholderText("http://127.0.0.1:2260/your-token")
        # 真实地址带长 token，比占位符长得多（实测 700px vs 480px）。
        # grow=True 去掉宽度上限，配合 expanding 吃满整行剩余空间。
        fit_input(
            self.resin_url,
            "http://127.0.0.1:2260/your-token",
            extra=32,
            grow=True,
        )
        attach_overflow_tooltip(
            self.resin_url,
            "格式 http://host:port/token\n"
            "Token 是路径最后一段，不会出现在任何日志里。",
        )

        self.resin_platform = QLineEdit()
        fit_input(self.resin_platform, "DefaultPlatform", extra=32)
        self.resin_identity = QComboBox()
        self.resin_identity.addItem("邮箱前缀 email_prefix", "email_prefix")
        self.resin_identity.addItem("完整邮箱 email", "email")

        layout.addWidget(self.resin_enabled)
        # 地址单独一行并吃满宽度：expanding 指向输入框（索引 1）
        layout.addWidget(
            toolbar(form_label("地址"), self.resin_url, expanding=1)
        )
        layout.addWidget(
            toolbar(
                form_label("平台名"),
                self.resin_platform,
                form_label("账号标识"),
                self.resin_identity,
            )
        )

        self.btn_save_resin = button("保存", "primary")
        self.btn_test_resin = button(
            "测试连接", "outline", tooltip="连续两次查出口 IP，验证连通与粘性"
        )
        self.btn_save_resin.clicked.connect(self._on_save_resin)
        self.btn_test_resin.clicked.connect(self._on_test_resin)
        layout.addWidget(toolbar(self.btn_save_resin, self.btn_test_resin, stretch_at=1))

        self.resin_result = QPlainTextEdit()
        self.resin_result.setReadOnly(True)
        self.resin_result.setMaximumHeight(90)
        self.resin_result.setPlaceholderText("测试结果显示在此")
        layout.addWidget(self.resin_result)

        layout.addWidget(
            hint_label(
                "地址格式 <code>http://host:port/token</code>，token 是路径最后一段。"
                "Token 不会显示在任何日志或状态里。"
            )
        )
        return box

    # ---------- 表现追踪 ----------
    def _build_tracker_box(self) -> QWidget:
        box = QGroupBox("IP 表现追踪")
        layout = QVBoxLayout(box)
        self.model = ProxyTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        apply_row_height(self.table, padding=10)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(ProxyTableModel.COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        layout.addWidget(
            hint_label(
                "统计在执行进程内累积，GUI 展示的是本进程视角，"
                "重启执行进程后归零。"
            )
        )
        return box

    # ---------- 刷新 ----------
    def refresh(self) -> None:
        cfg = self.ctx.cfg
        proxy = cfg.section("proxy")
        resin = cfg.section("resin")

        self.local_enabled.setChecked(bool(proxy.get("enabled", False)))
        self._select_data(self.local_mode, str(proxy.get("mode") or "single"))
        self._select_data(self.local_type, str(proxy.get("type") or "http"))
        self.local_host.setText(str(proxy.get("host") or "127.0.0.1"))
        self.local_single_port.setValue(int(proxy.get("single_port") or 7890))
        self.local_port_start.setValue(int(proxy.get("port_start") or 24000))
        self.local_port_end.setValue(int(proxy.get("port_end") or 24064))

        self.resin_enabled.setChecked(bool(resin.get("enabled", False)))
        self.resin_url.setText(str(resin.get("url") or ""))
        self.resin_platform.setText(str(resin.get("platform") or "Default"))
        self._select_data(self.resin_identity, str(resin.get("identity_mode") or "email_prefix"))

        self._update_active_label()
        run_async(
            lambda: self.ctx.proxy.snapshot().get("tracker", []),
            on_result=self.model.set_rows,
        )

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _update_active_label(self) -> None:
        if self.resin_enabled.isChecked():
            self.active_label.setText("当前生效：Resin 外部粘性代理池（本地代理池被忽略）")
            self.active_label.setStyleSheet(f"color: {COLOR_WARN}; font-weight: 600;")
        elif self.local_enabled.isChecked():
            self.active_label.setText("当前生效：本地代理池")
            self.active_label.setStyleSheet(f"color: {COLOR_OK}; font-weight: 600;")
        else:
            self.active_label.setText("当前生效：直连（未启用任何代理）")
            self.active_label.setStyleSheet(f"color: {TEXT_DIM}; font-weight: 600;")

    # ---------- 保存 ----------
    def _on_save_local(self) -> None:
        start = self.local_port_start.value()
        end = self.local_port_end.value()
        if self.local_mode.currentData() == "pool" and start > end:
            error(self, "保存失败", "端口池起始值不能大于结束值")
            return

        payload = {
            "proxy": {
                "enabled": self.local_enabled.isChecked(),
                "mode": str(self.local_mode.currentData()),
                "type": str(self.local_type.currentData()),
                "host": self.local_host.text().strip() or "127.0.0.1",
                "single_port": self.local_single_port.value(),
                "port_start": start,
                "port_end": end,
            }
        }
        self._save(payload, "本地代理池配置已保存")

    def _on_save_resin(self) -> None:
        url = self.resin_url.text().strip().rstrip("/")
        if self.resin_enabled.isChecked() and not url:
            error(self, "保存失败", "启用 Resin 时必须填写地址")
            return
        payload = {
            "resin": {
                "enabled": self.resin_enabled.isChecked(),
                "url": url,
                "platform": self.resin_platform.text().strip() or "Default",
                "identity_mode": str(self.resin_identity.currentData()),
            }
        }
        self._save(payload, "Resin 配置已保存")

    def _save(self, payload: Dict[str, Any], message: str) -> None:
        def work():
            self.ctx.cfg.update(payload, save=True)
            # 让本进程的单例感知变更；执行进程下次启动时重新读文件
            from proxy import reset_proxy_manager
            from proxy.resin import reset_resin

            reset_proxy_manager()
            reset_resin()
            return True

        def done(_):
            # 重置单例后 context 持有的引用已失效，重新取一次
            from proxy import get_proxy_manager

            self.ctx.proxy = get_proxy_manager(
                self.ctx.cfg.section("proxy"), logger=self.ctx.log
            )
            self.refresh()
            running = bool(
                (self.ctx.stats_snapshot().get("worker") or {}).get("running")
            )
            if running:
                info(
                    self,
                    "已保存",
                    f"{message}。\n\n执行进程正在运行，需重启后生效。",
                )
                notify(self, f"{message}（需重启执行进程生效）", "warn")
            else:
                info(self, "已保存", message)
                notify(self, message)

        run_async(work, on_result=done, on_error=lambda msg: error(self, "保存失败", msg))

    # ---------- 操作 ----------
    def _on_reset_stats(self) -> None:
        if not confirm(self, "重置统计", "清空 IP 成功率统计与黑名单。继续？"):
            return
        def done(_):
            self.refresh()
            info(self, "已重置", "IP 成功率统计与黑名单已清空。")
            notify(self, "代理表现统计已重置")

        run_async(
            self.ctx.proxy.reset,
            on_result=done,
            on_error=lambda msg: error(self, "重置失败", msg),
        )

    def _on_pick(self) -> None:
        def work():
            url = self.ctx.proxy.pick()
            if not url:
                return "当前配置为直连，未取到代理"
            return f"取到代理：{url}"

        def done(text):
            info(self, "试取代理", str(text))
            notify(self, str(text))

        run_async(
            work,
            on_result=done,
            on_error=lambda msg: error(self, "试取失败", msg),
        )

    def _on_test_resin(self) -> None:
        url = self.resin_url.text().strip().rstrip("/")
        if not url:
            error(self, "测试失败", "请先填写 Resin 地址")
            return

        self.btn_test_resin.setEnabled(False)
        self.btn_test_resin.setText("测试中…")
        self.resin_result.setPlainText(
            "正在测试（最长约 60 秒）…\n"
            "依次尝试 ipinfo.io / ipify.org / ip-api.com 三个探测端点，"
            "并进行两次查询验证粘性。"
        )

        config = {
            "enabled": True,
            "url": url,
            "platform": self.resin_platform.text().strip() or "Default",
            "identity_mode": str(self.resin_identity.currentData()),
        }

        def work():
            from proxy.resin import Resin

            # 用界面上的临时配置测试，不写盘——避免测失败还把配置存进去
            return Resin(config).test_connection()

        def done(result: Dict[str, Any]) -> None:
            # test_connection 失败时是正常返回 {"ok": False}，不抛异常，
            # 所以这里必须自己判 ok；只靠 on_error 会让失败静默掉。
            ok = bool(result.get("ok"))
            detail = str(result.get("detail") or "")
            lines = [f"[{'成功' if ok else '失败'}] {detail}"]
            if ok:
                lines.append(
                    f"出口 IP: {result.get('ip')} · 端点: {result.get('endpoint')} · "
                    f"粘性: {'正常' if result.get('sticky') else '异常'}"
                )
            self.resin_result.setPlainText("\n".join(lines))

            notify(
                self,
                f"Resin 测试{'成功' if ok else '失败'}",
                "ok" if ok else "error",
            )
            if ok:
                sticky = result.get("sticky")
                info(
                    self,
                    "测试成功",
                    f"出口 IP：{result.get('ip')}\n"
                    f"探测端点：{result.get('endpoint')}\n"
                    f"粘性：{'正常' if sticky else '异常 —— 两次查询 IP 不一致'}",
                )
            else:
                error(
                    self,
                    "测试失败",
                    f"{detail}\n\n"
                    "常见原因：Resin 未启动、地址或 Token 错误、本机防火墙拦截。",
                )

        def failed(message: str) -> None:
            # 这条路径是代码层异常（如缺依赖），不是连接失败
            self.resin_result.setPlainText(f"[异常] {message}")
            error(
                self,
                "测试出错",
                f"{message}\n\n这是程序异常而非连接失败，请反馈此信息。",
            )

        def restore() -> None:
            self.btn_test_resin.setEnabled(True)
            self.btn_test_resin.setText("测试连接")

        run_async(
            work,
            on_result=done,
            on_error=failed,
            on_done=restore,
        )
