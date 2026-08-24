"""日志页。

三层数据源（见规划文档 §12）：

1. **实时流** — 执行进程通过 IPC 推送，本页直接追加。这修复了旧 Web 面板
   的缺陷：日志缓冲在执行进程内，面板读的是自己的空缓冲，根本看不到任务日志。
2. **归档** — ``logs/*.log`` 文件，供事后排查（本页提供"打开日志目录"）。
3. **结构化事件** — SQLite ``events`` 表，按 task_id 回溯，在任务详情里看。

密码不入日志：``FlowLogger`` 的调用点从不传密码，本页也只做纯展示。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import deque
from typing import Any, Deque, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..theme import TEXT_DIM, log_level_color
from .widgets import button, human_time, title_label, toolbar

_LEVELS = ["全部", "DEBUG", "INFO", "OK", "WARN", "ERROR"]

#: 级别过滤时的排序权重，选 WARN 就同时显示 ERROR
_LEVEL_RANK = {
    "DEBUG": 0,
    "INFO": 1,
    "OK": 1,
    "WARN": 2,
    "WARNING": 2,
    "ERROR": 3,
    "FAIL": 3,
    "CRITICAL": 4,
}


class LogsView(QWidget):
    """实时日志查看器。"""

    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self._limit = int(context.cfg.get("desktop.log_view_limit", 500) or 500)
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=max(self._limit * 4, 2000))
        self._gui_cursor = 0
        self._build()
        context.log_received.connect(self.append_record)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(title_label("运行日志"))

        self.level_filter = QComboBox()
        self.level_filter.addItems(_LEVELS)
        self.level_filter.currentIndexChanged.connect(self._rerender)

        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("过滤关键字")
        self.keyword.setClearButtonEnabled(True)
        self.keyword.setMaximumWidth(220)
        self.keyword.textChanged.connect(self._rerender)

        self.autoscroll = QCheckBox("自动滚动")
        self.autoscroll.setChecked(True)

        self.btn_clear = button("清空显示")
        self.btn_open_dir = button("打开日志目录")
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_open_dir.clicked.connect(self._on_open_dir)

        self.source_label = QLabel("等待日志…")
        self.source_label.setStyleSheet(f"color: {TEXT_DIM};")

        layout.addWidget(
            toolbar(
                self.level_filter,
                self.keyword,
                self.autoscroll,
                self.btn_clear,
                self.btn_open_dir,
                self.source_label,
                stretch_at=4,
            )
        )

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(self._limit * 2)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")
        layout.addWidget(self.output, 1)

    # ---------- 数据入口 ----------
    def append_record(self, record: Dict[str, Any]) -> None:
        """IPC 推送或 GUI 自身日志。已在主线程（信号跨线程投递）。"""
        self._buffer.append(record)
        if self._passes(record):
            self._write_line(record)
        self._update_source_label()

    def poll_gui_logs(self) -> None:
        """兜底：拉取 GUI 进程自身的日志缓冲。

        执行进程日志走 IPC；IPC 不通时那部分看不到，但至少 GUI 自己的
        操作日志（导入、派发、停止）不会丢。
        """
        records = self.ctx.recent_logs(after_seq=self._gui_cursor, limit=200)
        for record in records:
            self._gui_cursor = max(self._gui_cursor, int(record.get("seq", 0)))
            self.append_record(record)

    # ---------- 渲染 ----------
    def _passes(self, record: Dict[str, Any]) -> bool:
        want = self.level_filter.currentText()
        if want != "全部":
            rank = _LEVEL_RANK.get(str(record.get("level") or "INFO").upper(), 1)
            if rank < _LEVEL_RANK.get(want, 0):
                return False
        keyword = self.keyword.text().strip().lower()
        if keyword:
            haystack = " ".join(
                str(record.get(k) or "")
                for k in ("message", "stage", "flow", "account")
            ).lower()
            if keyword not in haystack:
                return False
        return True

    def _format_line(self, record: Dict[str, Any]) -> str:
        ts = record.get("time") or human_time(record.get("ts"))
        level = str(record.get("level") or "INFO")
        flow = str(record.get("flow") or "")
        stage = str(record.get("stage") or "")
        account = str(record.get("account") or "")
        task_id = record.get("task_id")
        bits = [f"[{ts}]", f"[{level:<5}]", f"[{flow}]", f"[{stage}]"]
        if task_id is not None:
            bits.append(f"task={task_id}")
        if account:
            bits.append(f"acc={account}")
        bits.append(str(record.get("message") or ""))
        return " ".join(bits)

    def _write_line(self, record: Dict[str, Any]) -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(log_level_color(str(record.get("level") or "INFO"))))
        cursor.insertText(self._format_line(record) + "\n", fmt)
        if self.autoscroll.isChecked():
            self.output.verticalScrollBar().setValue(
                self.output.verticalScrollBar().maximum()
            )

    def _rerender(self) -> None:
        self.output.clear()
        records = [r for r in self._buffer if self._passes(r)]
        for record in records[-self._limit:]:
            self._write_line(record)

    def _update_source_label(self) -> None:
        if self.ctx.ipc_fresh:
            self.source_label.setText("实时推送 · 已连接")
        else:
            self.source_label.setText("实时推送未连接（仅显示本进程日志）")

    # ---------- 操作 ----------
    def _on_clear(self) -> None:
        self._buffer.clear()
        self.output.clear()

    def _on_open_dir(self) -> None:
        path = self.ctx.cfg.path_of("logger.dir", "logs")
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606 - 打开用户自己的日志目录
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError:
            from .widgets import info

            info(self, "日志目录", str(path))
