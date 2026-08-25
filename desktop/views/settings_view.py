"""设置页。

只暴露对使用有实际影响的配置项，映射到现有 config.yaml 的键。
不新造配置——原规划文档里的"浏览器并发数"在代码中不存在，
浏览器实例数就等于并发线程数（``system.max_workers``）。

配置生效时机：执行进程启动时读一次配置。所以改完配置若执行进程在跑，
必须明确提示"重启后生效"，不能让用户以为立即生效了。
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..bridge.tasks import run_async
from ..theme import COLOR_WARN, TEXT_DIM, fit_all_checkboxes, fit_input
from .widgets import (
    KeyValueRow,
    button,
    confirm,
    error,
    hint_label,
    info,
    notify,
    spinbox,
    title_label,
    toolbar,
)

_LOG_LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]

_CAPTCHA_STRATEGIES = [
    ("全自动按压", 0),
    ("半自动（暂停等人工）", 1),
]


class SettingsView(QWidget):
    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)
        outer.addWidget(title_label("设置"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addWidget(self._build_run_box())
        layout.addWidget(self._build_browser_box())
        layout.addWidget(self._build_flow_box())
        layout.addWidget(self._build_log_box())
        layout.addWidget(self._build_paths_box())
        layout.addStretch(1)

        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        self.btn_save = button("保存设置", "primary")
        self.btn_reload = button("放弃修改")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_reload.clicked.connect(self.refresh)
        self.hint = QLabel("")
        self.hint.setStyleSheet(f"color: {COLOR_WARN};")
        outer.addWidget(toolbar(self.btn_save, self.btn_reload, self.hint, stretch_at=2))

        # 本页有十来个勾选框，统一按文字宽度设最小宽度
        fit_all_checkboxes(self)

    # ---------- 分组 ----------
    def _build_run_box(self) -> QWidget:
        box = QGroupBox("运行")
        layout = QVBoxLayout(box)

        self.max_workers = spinbox(
            1,
            16,
            tooltip=(
                "同时并行处理的账号数。每个线程独占一个浏览器实例，\n"
                "也就是浏览器实例上限。\n\n"
                "默认 1（逐个处理）。调高能提速，但内存与 CPU 占用同比上升，\n"
                "且同时打开多个浏览器更容易被目标站点识别为异常流量。"
            ),
        )
        self.task_retry = spinbox(0, 10, tooltip="单任务失败后的重试次数")
        self.account_separator = QLineEdit()
        fit_input(self.account_separator, "--------", extra=32)

        layout.addWidget(
            toolbar(
                QLabel("并发线程"),
                self.max_workers,
                QLabel("失败重试"),
                self.task_retry,
                QLabel("账号分隔符"),
                self.account_separator,
                stretch_at=5,
            )
        )
        return box

    def _build_browser_box(self) -> QWidget:
        box = QGroupBox("浏览器")
        layout = QVBoxLayout(box)

        self.headless = QCheckBox("无头模式")
        self.headless.setToolTip(
            "无头模式下无法人工处理验证码，半自动策略会直接超时失败。"
        )
        self.fingerprint_enabled = QCheckBox("指纹伪装")
        self.fingerprint_enabled.setToolTip(
            "仅 fingerprint-chromium 内核支持；备用内核下无效。"
        )
        self.persistent = QCheckBox("持久化 Profile")
        self.reuse_profile = QCheckBox("复用同账号 Profile")
        self.cleanup_on_exit = QCheckBox("任务结束清理临时 Profile")

        layout.addWidget(
            toolbar(self.headless, self.fingerprint_enabled, stretch_at=1)
        )
        layout.addWidget(
            toolbar(
                self.persistent, self.reuse_profile, self.cleanup_on_exit, stretch_at=2
            )
        )

        self.timeout = spinbox(1000, 600000, suffix=" ms", step=5000)
        self.nav_timeout = spinbox(1000, 600000, suffix=" ms", step=5000)
        layout.addWidget(
            toolbar(
                QLabel("操作超时"),
                self.timeout,
                QLabel("导航超时"),
                self.nav_timeout,
                stretch_at=3,
            )
        )
        layout.addWidget(
            hint_label("内核选择在「浏览器」页。关闭复用会让每次任务都用新身份。")
        )
        return box

    def _build_flow_box(self) -> QWidget:
        box = QGroupBox("登录流程")
        layout = QVBoxLayout(box)

        self.captcha_strategy = QComboBox()
        for text, value in _CAPTCHA_STRATEGIES:
            self.captcha_strategy.addItem(text, value)
        self.max_captcha_retries = spinbox(0, 20)
        self.wait_verify_timeout = spinbox(10, 3600, suffix=" 秒")
        self.captcha_screenshot = QCheckBox("验证码失败截图")
        self.checkpoint_enabled = QCheckBox("保存流程断点")
        self.checkpoint_enabled.setToolTip("中断后可从断点恢复，建议保持开启")

        layout.addWidget(
            toolbar(
                QLabel("验证码策略"),
                self.captcha_strategy,
                QLabel("重试次数"),
                self.max_captcha_retries,
                stretch_at=3,
            )
        )
        layout.addWidget(
            toolbar(
                QLabel("等待验证超时"),
                self.wait_verify_timeout,
                self.captcha_screenshot,
                self.checkpoint_enabled,
                stretch_at=3,
            )
        )
        layout.addWidget(
            hint_label(
                "半自动策略下流程会暂停等人工按验证码，超时未处理即失败；"
                "此时账号状态为「等待验证」，仪表盘会单独统计。"
            )
        )
        return box

    def _build_log_box(self) -> QWidget:
        box = QGroupBox("日志")
        layout = QVBoxLayout(box)

        self.log_level = QComboBox()
        self.log_level.addItems(_LOG_LEVELS)
        self.log_to_file = QCheckBox("写入文件")
        self.log_view_limit = spinbox(
            100, 5000, step=100, tooltip="日志页最多显示的行数"
        )

        layout.addWidget(
            toolbar(
                QLabel("级别"),
                self.log_level,
                self.log_to_file,
                QLabel("界面显示行数"),
                self.log_view_limit,
                stretch_at=4,
            )
        )
        return box

    def _build_paths_box(self) -> QWidget:
        box = QGroupBox("路径")
        layout = QVBoxLayout(box)
        self.rows: Dict[str, KeyValueRow] = {
            "app": KeyValueRow("程序目录", elide=True),
            "data": KeyValueRow("数据目录", elide=True),
            "config": KeyValueRow("配置文件", elide=True),
            "db": KeyValueRow("数据库", elide=True),
            "logs": KeyValueRow("日志目录", elide=True),
            "profiles": KeyValueRow("Profile 目录", elide=True),
            "accounts": KeyValueRow("账号文件", elide=True),
        }
        for row in self.rows.values():
            layout.addWidget(row)

        self.btn_open_data = button("打开数据目录")
        self.btn_open_data.clicked.connect(self._on_open_data)
        layout.addWidget(toolbar(self.btn_open_data, stretch_at=0))
        layout.addWidget(
            hint_label(
                "程序目录只读（升级会覆盖），用户数据全部在数据目录下，"
                "卸载默认不删除。"
            )
        )
        return box

    # ---------- 载入 ----------
    def refresh(self) -> None:
        cfg = self.ctx.cfg

        self.max_workers.setValue(int(cfg.get("system.max_workers", 1) or 1))
        self.task_retry.setValue(int(cfg.get("system.task_retry", 1) or 0))
        self.account_separator.setText(str(cfg.get("system.account_separator", "----")))

        self.headless.setChecked(bool(cfg.get("browser.headless", False)))
        self.fingerprint_enabled.setChecked(
            bool(cfg.get("browser.fingerprint_enabled", False))
        )
        self.timeout.setValue(int(cfg.get("browser.timeout", 60000) or 60000))
        self.nav_timeout.setValue(int(cfg.get("browser.nav_timeout", 45000) or 45000))

        self.persistent.setChecked(bool(cfg.get("profile.persistent", True)))
        self.reuse_profile.setChecked(bool(cfg.get("profile.reuse", False)))
        self.cleanup_on_exit.setChecked(bool(cfg.get("profile.cleanup_on_exit", True)))

        strategy = int(cfg.get("flow.captcha_strategy", 0) or 0)
        index = self.captcha_strategy.findData(strategy)
        if index >= 0:
            self.captcha_strategy.setCurrentIndex(index)
        self.max_captcha_retries.setValue(int(cfg.get("flow.max_captcha_retries", 3) or 3))
        self.wait_verify_timeout.setValue(int(cfg.get("flow.wait_verify_timeout", 300) or 300))
        self.captcha_screenshot.setChecked(bool(cfg.get("flow.captcha_screenshot", True)))
        self.checkpoint_enabled.setChecked(bool(cfg.get("flow.checkpoint_enabled", True)))

        level = str(cfg.get("logger.level", "INFO")).upper()
        if level in _LOG_LEVELS:
            self.log_level.setCurrentText(level)
        self.log_to_file.setChecked(bool(cfg.get("logger.file", True)))
        self.log_view_limit.setValue(int(cfg.get("desktop.log_view_limit", 500) or 500))

        self.rows["app"].set_value(str(cfg.root))
        self.rows["data"].set_value(str(cfg.data_root))
        self.rows["config"].set_value(str(cfg.source_path or "（内置默认值）"))
        self.rows["db"].set_value(str(cfg.path_of("database.path", "data/app.db")))
        self.rows["logs"].set_value(str(cfg.path_of("logger.dir", "logs")))
        self.rows["profiles"].set_value(str(cfg.path_of("profile.root", "profiles")))
        accounts = cfg.path_of("system.accounts_file", "accounts.txt")
        self.rows["accounts"].set_value(
            str(accounts) if accounts.is_file() else f"{accounts}（不存在）"
        )
        self.hint.setText("")

    # ---------- 保存 ----------
    def _collect(self) -> Dict[str, Any]:
        return {
            "system": {
                "max_workers": self.max_workers.value(),
                "task_retry": self.task_retry.value(),
                "account_separator": self.account_separator.text() or "----",
            },
            "browser": {
                "headless": self.headless.isChecked(),
                "fingerprint_enabled": self.fingerprint_enabled.isChecked(),
                "timeout": self.timeout.value(),
                "nav_timeout": self.nav_timeout.value(),
            },
            "profile": {
                "persistent": self.persistent.isChecked(),
                "reuse": self.reuse_profile.isChecked(),
                "cleanup_on_exit": self.cleanup_on_exit.isChecked(),
            },
            "flow": {
                "captcha_strategy": int(self.captcha_strategy.currentData()),
                "max_captcha_retries": self.max_captcha_retries.value(),
                "wait_verify_timeout": self.wait_verify_timeout.value(),
                "captcha_screenshot": self.captcha_screenshot.isChecked(),
                "checkpoint_enabled": self.checkpoint_enabled.isChecked(),
            },
            "logger": {
                "level": self.log_level.currentText(),
                "file": self.log_to_file.isChecked(),
            },
            "desktop": {
                "log_view_limit": self.log_view_limit.value(),
            },
        }

    def _on_save(self) -> None:
        payload = self._collect()

        # 无头 + 半自动 = 必然超时失败，先拦一下
        if payload["browser"]["headless"] and payload["flow"]["captcha_strategy"] == 1:
            if not confirm(
                self,
                "配置冲突",
                "无头模式下无法人工处理验证码，半自动策略会等到超时后失败。\n\n"
                "仍要保存？",
                danger=True,
            ):
                return

        def work():
            path = self.ctx.cfg.update(payload, save=True)
            from logger import setup_from_config

            setup_from_config(self.ctx.cfg, force=True)
            return path

        run_async(
            work,
            on_result=self._on_saved,
            on_error=lambda msg: error(self, "保存失败", msg),
        )

    def _on_saved(self, path) -> None:
        self.refresh()
        running = bool((self.ctx.stats_snapshot().get("worker") or {}).get("running"))
        if running:
            self.hint.setText("执行进程正在运行，部分设置需重启后生效")
            info(
                self,
                "已保存",
                f"配置已写入：\n{path}\n\n"
                "执行进程启动时读取配置，并发线程数、浏览器与流程相关设置"
                "需重启执行进程才生效。",
            )
            notify(self, "设置已保存（需重启执行进程生效）", "warn")
        else:
            info(self, "已保存", f"配置已写入：\n{path}")
            notify(self, "设置已保存")
        window = self.window()
        if hasattr(window, "reload_runtime_settings"):
            window.reload_runtime_settings()

    # ---------- 工具 ----------
    def _on_open_data(self) -> None:
        path = self.ctx.cfg.data_root
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606 - 打开用户自己的数据目录
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError:
            info(self, "数据目录", str(path))
