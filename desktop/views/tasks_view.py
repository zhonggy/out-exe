"""任务管理页。

覆盖原 Web 面板的 tasks / workers / queue 路由。

关于「暂停」：现有 TaskManager 只有 start/stop，没有 pause 语义
（Worker 主循环看的是 stop_event）。这里不放暂停按钮，中断后靠
flow/checkpoint.py 的断点恢复继续，避免做出一个假的暂停。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from database import AccountStatus, TaskStatus

from ..bridge.tasks import run_async
from ..theme import COLOR_FAIL, COLOR_OK, COLOR_RUNNING, TEXT_DIM, task_status_color
from .widgets import (
    KeyValueRow,
    button,
    card,
    confirm,
    error,
    hint_label,
    human_duration,
    human_time,
    info,
    notify,
    title_label,
    toolbar,
)

_TASK_STATUS_FILTERS = [("全部", "")] + [(s.value, s.value) for s in TaskStatus]


class TaskTableModel(QAbstractTableModel):
    COLUMNS = [
        ("ID", "id"),
        ("账号", "account"),
        ("类型", "type"),
        ("状态", "status"),
        ("阶段", "stage"),
        ("尝试", "attempt"),
        ("耗时", "duration"),
        ("结果 / 错误", "result"),
        ("创建时间", "created_at"),
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
            if key == "duration":
                return human_duration(row.get("duration"))
            if key == "created_at":
                return human_time(row.get("created_at"))
            if key == "attempt":
                return f"{row.get('attempt', 0)}/{row.get('max_attempt', 1)}"
            if key == "result":
                return str(row.get("error") or row.get("result") or "-")
            value = row.get(key)
            return "-" if value in (None, "") else str(value)

        if role == Qt.ForegroundRole:
            if key == "status":
                return QColor(task_status_color(str(row.get("status"))))
            if key == "result" and row.get("error"):
                return QColor(COLOR_FAIL)

        if role == Qt.ToolTipRole:
            return str(row.get("error") or row.get("result") or "") or None

        if role == Qt.TextAlignmentRole and key in ("id", "attempt"):
            return int(Qt.AlignCenter)

        return None

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def task_id_at(self, row: int) -> Optional[int]:
        if 0 <= row < len(self._rows):
            return self._rows[row].get("id")
        return None

    def row_at(self, row: int) -> Dict[str, Any]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return {}


class DispatchDialog(QDialog):
    """派发任务：从待处理账号中取 N 个，或指定账号列表。"""

    def __init__(self, flows: List[str], default_limit: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("派发任务")
        self.resize(460, 340)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.flow = QComboBox()
        for name in flows or ["login"]:
            self.flow.addItem(name)
        self.limit = QSpinBox()
        self.limit.setRange(1, 100000)
        self.limit.setValue(default_limit)
        self.priority = QSpinBox()
        self.priority.setRange(-100, 100)
        self.priority.setValue(0)
        self.priority.setToolTip("数值越大越先执行")
        form.addRow("流程", self.flow)
        form.addRow("数量上限", self.limit)
        form.addRow("优先级", self.priority)
        layout.addLayout(form)

        layout.addWidget(
            hint_label(
                "留空账号列表 = 自动从「未处理」账号中取上述数量。<br>"
                "填写账号列表（每行一个）= 只派发这些账号，忽略数量上限。"
            )
        )
        self.accounts = QPlainTextEdit()
        self.accounts.setPlaceholderText("a@example.com\nb@example.com")
        layout.addWidget(self.accounts, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("派发")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> Dict[str, Any]:
        raw = self.accounts.toPlainText().strip()
        accounts = [line.strip() for line in raw.splitlines() if line.strip()]
        return {
            "flow": self.flow.currentText(),
            "limit": self.limit.value(),
            "priority": self.priority.value(),
            "accounts": accounts,
        }


class TasksView(QWidget):
    """执行控制 + 任务列表。"""

    data_changed = Signal()

    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self._build()
        self.refresh()

    # ---------- 构建 ----------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(title_label("任务管理"))
        layout.addWidget(self._build_control_card())

        self.status_filter = QComboBox()
        for text, value in _TASK_STATUS_FILTERS:
            self.status_filter.addItem(text, value)
        self.status_filter.currentIndexChanged.connect(self.refresh)

        self.btn_dispatch = button("派发任务", "primary")
        self.btn_cancel = button("取消选中")
        self.btn_delete = button("删除选中", "danger")
        self.btn_clear = button("清空队列", "danger", tooltip="把队列中未开始的任务标记为已取消")
        self.btn_refresh = button("刷新")

        self.btn_dispatch.clicked.connect(self._on_dispatch)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_clear.clicked.connect(self._on_clear_queue)
        self.btn_refresh.clicked.connect(self.refresh)

        layout.addWidget(
            toolbar(
                self.status_filter,
                self.btn_dispatch,
                self.btn_cancel,
                self.btn_delete,
                self.btn_clear,
                self.btn_refresh,
                stretch_at=0,
            )
        )

        self.model = TaskTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.doubleClicked.connect(self._on_row_activated)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

    def _build_control_card(self) -> QWidget:
        holder = card()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        self.state_label = QLabel("执行进程：未运行")
        self.state_label.setStyleSheet(f"color: {TEXT_DIM}; font-weight: 600;")

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(
            int(self.ctx.cfg.get("system.max_workers", 3) or 3)
        )
        self.workers_spin.setToolTip(
            "并发 Worker 数。每个 Worker 独占一个浏览器实例，"
            "数量越大内存和 CPU 占用越高。"
        )

        self.btn_start = button("开始执行", "primary")
        self.btn_stop = button("停止执行", "danger")
        self.btn_restart = button("重启执行进程")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_restart.clicked.connect(self._on_restart)

        outer.addWidget(self.state_label)
        outer.addWidget(
            toolbar(
                QLabel("Worker 数"),
                self.workers_spin,
                self.btn_start,
                self.btn_stop,
                self.btn_restart,
                stretch_at=4,
            )
        )

        self.row_pid = KeyValueRow("进程 PID")
        self.row_queue = KeyValueRow("队列 / 浏览器")
        self.row_progress = KeyValueRow("完成 / 失败")
        outer.addWidget(self.row_pid)
        outer.addWidget(self.row_queue)
        outer.addWidget(self.row_progress)

        outer.addWidget(
            hint_label(
                "任务在独立进程中执行，关闭本窗口不会中断任务；"
                "需要停止请点「停止执行」。"
            )
        )
        return holder

    # ---------- 刷新 ----------
    def refresh(self) -> None:
        status = str(self.status_filter.currentData() or "")

        def work():
            tasks = self.ctx.db.list_tasks(status=status or None, limit=500)
            return [t.to_dict() for t in tasks]

        run_async(
            work,
            on_result=self.model.set_rows,
            on_error=lambda msg: error(self, "加载任务失败", msg),
        )
        self.update_state(self.ctx.stats_snapshot())

    def update_state(self, snapshot: Dict[str, Any]) -> None:
        """由主窗口定时器统一驱动，避免各页各起一个定时器。"""
        worker = snapshot.get("worker") or {}
        running = bool(worker.get("running"))
        pid = worker.get("pid") or 0

        if running:
            color = COLOR_RUNNING
            text = f"执行进程：运行中（{human_duration(worker.get('uptime'))}）"
            if worker.get("external"):
                text += "  · 外部启动，已接管"
        else:
            color = TEXT_DIM
            text = "执行进程：未运行"
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color: {color}; font-weight: 600;")

        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_restart.setEnabled(running)
        self.workers_spin.setEnabled(not running)

        self.row_pid.set_value(str(pid) if pid else "-")
        queue = snapshot.get("queue") or {}
        self.row_queue.set_value(
            f"{queue.get('size', 0)} 待执行 / {snapshot.get('browsers', 0)} 个浏览器"
        )
        self.row_progress.set_value(
            f"{snapshot.get('succeeded', 0)} 成功 / {snapshot.get('failed', 0)} 失败",
            COLOR_OK if not snapshot.get("failed") else None,
        )

    # ---------- 执行控制 ----------
    def _on_start(self) -> None:
        workers = self.workers_spin.value()

        def work():
            # 派发前确保库里有任务，否则 Worker 起来后空转
            pending = self.ctx.db.pending_tasks(limit=1)
            if not pending:
                stats = self.ctx.am.stats()
                if not stats["by_status"].get(AccountStatus.NEW.value):
                    return {"ok": False, "error": "没有待执行任务，也没有未处理账号"}
            return self.ctx.wpm.start(workers)

        run_async(
            work,
            on_result=self._on_control_result,
            on_error=lambda msg: error(self, "启动失败", msg),
        )

    def _on_stop(self) -> None:
        if not confirm(
            self,
            "停止执行",
            "将结束执行进程并关闭所有浏览器。\n\n"
            "正在执行的任务会保留断点，下次开始可从断点继续。",
        ):
            return
        run_async(
            self.ctx.wpm.stop,
            on_result=self._on_control_result,
            on_error=lambda msg: error(self, "停止失败", msg),
        )

    def _on_restart(self) -> None:
        workers = self.workers_spin.value()
        if not confirm(self, "重启执行进程", "将先停止再重新启动执行进程。继续？"):
            return
        run_async(
            lambda: self.ctx.wpm.restart(workers),
            on_result=self._on_control_result,
            on_error=lambda msg: error(self, "重启失败", msg),
        )

    def _on_control_result(self, result: Dict[str, Any]) -> None:
        """开始/停止/重启的统一反馈。

        wpm.start/stop 失败时是返回 {"ok": False} 而不抛异常，
        所以必须在这里判 ok，否则失败会完全静默。
        """
        if not isinstance(result, dict):
            self.refresh()
            self.data_changed.emit()
            return

        if not result.get("ok", True):
            reason = str(result.get("error") or "未知原因")
            error(self, "操作未完成", reason)
            notify(self, f"操作失败：{reason}", "error")
        elif result.get("already"):
            notify(self, "执行进程已在运行", "warn")
        elif "stopped" in result:
            stopped = result.get("stopped") or []
            if stopped:
                how = "优雅停止" if result.get("graceful") else "强制结束"
                notify(self, f"执行进程已{how}（PID {', '.join(map(str, stopped))}）")
            else:
                notify(self, "没有正在运行的执行进程", "warn")
        elif result.get("pid"):
            notify(
                self,
                f"执行进程已启动（PID {result['pid']}，"
                f"{result.get('workers', '?')} 个 Worker）",
            )

        self.refresh()
        self.data_changed.emit()

    # ---------- 任务操作 ----------
    def _selected_ids(self) -> List[int]:
        rows = {i.row() for i in self.table.selectionModel().selectedRows()}
        ids = [self.model.task_id_at(r) for r in sorted(rows)]
        return [i for i in ids if i is not None]

    def _on_dispatch(self) -> None:
        from flow import list_flows

        flows = sorted(list_flows())
        dialog = DispatchDialog(flows, int(self.ctx.cfg.get("system.max_workers", 3) or 3) * 10, self)
        if not dialog.exec():
            return
        values = dialog.values()

        def work():
            from task import get_task_manager

            tm = get_task_manager(self.ctx.cfg, logger=self.ctx.log)
            if values["accounts"]:
                tasks = [
                    tm.submit(
                        account,
                        task_type=values["flow"],
                        priority=values["priority"],
                    )
                    for account in values["accounts"]
                ]
            else:
                tasks = tm.submit_batch(
                    task_type=values["flow"],
                    limit=values["limit"],
                    priority=values["priority"],
                )
            return len(tasks)

        run_async(
            work,
            on_result=self._on_dispatched,
            on_error=lambda msg: error(self, "派发失败", msg),
        )

    def _on_dispatched(self, count: int) -> None:
        if not count:
            info(
                self,
                "派发任务",
                "没有可派发的账号。\n\n"
                "可能原因：账号都已处理完（在账号页选中后点「重置状态」可重跑），"
                "或还没导入账号。",
            )
            notify(self, "派发任务：无可派发的账号", "warn")
        else:
            info(self, "派发成功", f"已派发 {count} 个任务。\n\n点「开始执行」启动 Worker。")
            notify(self, f"已派发 {count} 个任务")
        self.refresh()
        self.data_changed.emit()

    def _on_cancel(self) -> None:
        ids = self._selected_ids()
        if not ids:
            info(self, "取消任务", "请先选中任务")
            return

        def work():
            for task_id in ids:
                self.ctx.db.update_task(task_id, status=TaskStatus.CANCELLED.value)
            return len(ids)

        run_async(
            work,
            on_result=lambda n: self._after_change(f"已取消 {n} 个任务"),
            on_error=lambda msg: error(self, "取消失败", msg),
        )

    def _on_delete(self) -> None:
        ids = self._selected_ids()
        if not ids:
            info(self, "删除任务", "请先选中任务")
            return
        if not confirm(
            self, "删除任务", f"确认删除 {len(ids)} 条任务记录？此操作不可恢复。", danger=True
        ):
            return

        def work():
            for task_id in ids:
                self.ctx.db.delete_task(task_id)
            return len(ids)

        run_async(
            work,
            on_result=lambda n: self._after_change(f"已删除 {n} 条任务"),
            on_error=lambda msg: error(self, "删除失败", msg),
        )

    def _on_clear_queue(self) -> None:
        if not confirm(
            self,
            "清空队列",
            "把所有未开始的任务标记为「已取消」。\n\n"
            "正在执行的任务不受影响。",
            danger=True,
        ):
            return

        def work():
            return self.ctx.db.clear_tasks(
                statuses=[TaskStatus.QUEUED.value, TaskStatus.CREATED.value]
            )

        run_async(
            work,
            on_result=lambda n: self._after_change(f"已取消 {n} 个排队任务"),
            on_error=lambda msg: error(self, "清空失败", msg),
        )

    # ---------- 详情 ----------
    def _on_row_activated(self, index: QModelIndex) -> None:
        row = self.model.row_at(index.row())
        task_id = row.get("id")
        if task_id is None:
            return
        from .task_detail import TaskDetailDialog

        TaskDetailDialog(self.ctx, int(task_id), self).exec()

    def _after_change(self, message: str) -> None:
        self.refresh()
        self.data_changed.emit()
        notify(self, message)
