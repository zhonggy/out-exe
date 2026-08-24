"""任务详情弹窗。

事件历史查 SQLite 的 ``events`` 表而不是日志窗口缓冲——deque 只保留有限条数，
且 GUI 重启即丢，而 events 表是持久的、按 task_id 可回溯的。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..bridge.tasks import run_async
from ..theme import log_level_color, task_status_color
from .widgets import KeyValueRow, human_duration, human_time, title_label


class TaskDetailDialog(QDialog):
    def __init__(self, context, task_id: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self.task_id = task_id
        self.setWindowTitle(f"任务 #{task_id}")
        self.resize(680, 560)
        self._build()
        self._load()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(title_label(f"任务 #{self.task_id}"))

        info_box = QGroupBox("基本信息")
        info_layout = QVBoxLayout(info_box)
        self.rows: Dict[str, KeyValueRow] = {}
        for key, label in [
            ("account", "账号"),
            ("type", "流程类型"),
            ("status", "任务状态"),
            ("stage", "流程阶段"),
            ("attempt", "尝试次数"),
            ("profile_id", "Profile"),
            ("proxy", "代理"),
            ("duration", "耗时"),
            ("start_time", "开始时间"),
            ("end_time", "结束时间"),
            ("result", "结果"),
            ("error", "错误"),
        ]:
            row = KeyValueRow(label)
            self.rows[key] = row
            info_layout.addWidget(row)
        layout.addWidget(info_box)

        events_box = QGroupBox("事件历史（events 表）")
        events_layout = QVBoxLayout(events_box)
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setLineWrapMode(QPlainTextEdit.NoWrap)
        events_layout.addWidget(self.events)
        layout.addWidget(events_box, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _load(self) -> None:
        def work():
            task = self.ctx.db.get_task(self.task_id)
            events = self.ctx.db.list_events(task_id=self.task_id, limit=500)
            checkpoints = self.ctx.db.list_checkpoints(self.task_id)
            return (
                task.to_dict() if task else {},
                events,
                [c.to_dict() for c in checkpoints],
            )

        run_async(work, on_result=self._render)

    def _render(self, payload) -> None:
        task, events, checkpoints = payload
        for key, row in self.rows.items():
            if key == "duration":
                row.set_value(human_duration(task.get("duration")))
            elif key in ("start_time", "end_time"):
                row.set_value(human_time(task.get(key)))
            elif key == "attempt":
                row.set_value(f"{task.get('attempt', 0)} / {task.get('max_attempt', 1)}")
            elif key == "status":
                value = str(task.get("status") or "-")
                row.set_value(value, task_status_color(value))
            else:
                value = task.get(key)
                row.set_value("-" if value in (None, "") else str(value))

        lines: List[str] = []
        # list_events 按 id DESC 返回，展示时改成时间正序更好读
        for event in reversed(events):
            ts = human_time(event.get("ts"))
            level = str(event.get("level") or "INFO")
            stage = str(event.get("stage") or "")
            message = str(event.get("message") or "")
            lines.append(f"[{ts}] [{level:<5}] [{stage}] {message}")
        if checkpoints:
            lines.append("")
            lines.append("--- 断点 ---")
            for cp in checkpoints:
                lines.append(
                    f"[{human_time(cp.get('created_at'))}] {cp.get('stage')} {cp.get('data')}"
                )
        self.events.setPlainText("\n".join(lines) if lines else "（无事件记录）")
