"""Profile 管理页。

体积必须显示：每个 Chromium profile 含缓存，单个可达几十 MB，
跑几百个账号后 ``profiles/`` 会占满磁盘。``cleanup_on_exit`` 只在正常
收尾时清理，进程被强杀会残留。

注意 ``in_use`` 标记只反映**本进程**内存中的占用登记（``ProfileManager._in_use``）。
浏览器实际由执行进程持有，所以这里还要交叉查 SQLite 的 profile 状态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from database import ProfileStatus

from ..bridge.tasks import run_async
from ..theme import COLOR_IDLE, COLOR_RUNNING, COLOR_WARN, TEXT_DIM
from .widgets import (
    button,
    confirm,
    error,
    hint_label,
    human_bytes,
    human_time,
    info,
    notify,
    title_label,
    toolbar,
)


class ProfileTableModel(QAbstractTableModel):
    COLUMNS = [
        ("Profile ID", "profile_id"),
        ("绑定账号", "account"),
        ("占用状态", "state"),
        ("体积", "size_bytes"),
        ("类型", "kind"),
        ("最后使用", "mtime"),
        ("目录", "path"),
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
            if key == "size_bytes":
                return human_bytes(row.get("size_bytes", 0))
            if key == "mtime":
                return human_time(row.get("mtime"))
            if key == "kind":
                return "临时" if row.get("temporary") else "持久"
            value = row.get(key)
            return "-" if value in (None, "") else str(value)

        if role == Qt.ForegroundRole and key == "state":
            state = str(row.get("state") or "")
            if state == "使用中":
                return QColor(COLOR_RUNNING)
            if state == "异常":
                return QColor(COLOR_WARN)
            return QColor(COLOR_IDLE)

        if role == Qt.TextAlignmentRole and key == "size_bytes":
            return int(Qt.AlignRight | Qt.AlignVCenter)

        return None

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def row_at(self, row: int) -> Dict[str, Any]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return {}


class ProfilesView(QWidget):
    data_changed = Signal()

    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(title_label("Profile 管理"))

        self.summary = QLabel("-")
        self.summary.setStyleSheet(f"color: {TEXT_DIM};")

        self.only_temp = QCheckBox("只看临时")
        self.only_temp.stateChanged.connect(self._rerender)

        self.btn_delete = button("删除选中", "danger")
        self.btn_clear_temp = button("清理临时", tooltip="删除所有 tmp_ 前缀且未占用的 profile")
        self.btn_prune = button("清理 30 天未用")
        self.btn_refresh = button("刷新")
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_clear_temp.clicked.connect(self._on_clear_temp)
        self.btn_prune.clicked.connect(self._on_prune)
        self.btn_refresh.clicked.connect(self.refresh)

        layout.addWidget(
            toolbar(
                self.summary,
                self.only_temp,
                self.btn_delete,
                self.btn_clear_temp,
                self.btn_prune,
                self.btn_refresh,
                stretch_at=0,
            )
        )

        self.model = ProfileTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        layout.addWidget(
            hint_label(
                "Profile 保存 Cookie 与登录态。删除后对应账号下次执行需重新登录，"
                "可能触发额外验证。<br>"
                "「使用中」的 profile 正被执行进程占用，删除会导致该任务失败。"
            )
        )

    # ---------- 刷新 ----------
    def refresh(self) -> None:
        def work():
            dirs = self.ctx.pm.list_dirs()
            # 交叉查库：磁盘扫描看不出是哪个账号、是否被别的进程占用
            db_map = {p.profile_id: p for p in self.ctx.db.list_profiles()}
            rows: List[Dict[str, Any]] = []
            for item in dirs:
                pid = item["profile_id"]
                record = db_map.get(pid)
                if record and record.status == ProfileStatus.IN_USE.value:
                    state = "使用中"
                elif record and record.status == ProfileStatus.BROKEN.value:
                    state = "异常"
                elif item.get("in_use"):
                    state = "使用中"
                else:
                    state = "空闲"
                rows.append(
                    {
                        **item,
                        "size_bytes": float(item.get("size_mb", 0)) * 1024 * 1024,
                        "account": record.account if record else "",
                        "state": state,
                    }
                )
            return rows

        run_async(
            work,
            on_result=self._on_rows,
            on_error=lambda msg: error(self, "加载 Profile 失败", msg),
        )

    def _on_rows(self, rows: List[Dict[str, Any]]) -> None:
        self._all_rows = rows
        total_bytes = sum(r.get("size_bytes", 0) for r in rows)
        temp_count = sum(1 for r in rows if r.get("temporary"))
        self.summary.setText(
            f"共 {len(rows)} 个 · 占用 {human_bytes(total_bytes)} · 临时 {temp_count} 个"
        )
        self._rerender()

    def _rerender(self) -> None:
        rows = getattr(self, "_all_rows", [])
        if self.only_temp.isChecked():
            rows = [r for r in rows if r.get("temporary")]
        self.model.set_rows(rows)

    # ---------- 操作 ----------
    def _selected(self) -> List[Dict[str, Any]]:
        indexes = {i.row() for i in self.table.selectionModel().selectedRows()}
        return [self.model.row_at(r) for r in sorted(indexes) if self.model.row_at(r)]

    def _on_delete(self) -> None:
        rows = self._selected()
        if not rows:
            info(self, "删除 Profile", "请先选中要删除的 Profile")
            return

        in_use = [r for r in rows if r.get("state") == "使用中"]
        message = f"确认删除 {len(rows)} 个 Profile？对应账号的登录态会丢失。"
        if in_use:
            message = (
                f"其中 {len(in_use)} 个正被执行进程占用！\n"
                "删除会导致这些任务失败并可能留下损坏目录。\n\n" + message
            )
        if not confirm(self, "删除 Profile", message, danger=True):
            return

        def work():
            removed = 0
            for row in rows:
                if self.ctx.pm.delete(row["profile_id"], Path(row["path"])):
                    removed += 1
            return removed

        run_async(
            work,
            on_result=lambda n: self._after_change(f"已删除 {n} 个 Profile"),
            on_error=lambda msg: error(self, "删除失败", msg),
        )

    def _on_clear_temp(self) -> None:
        if not confirm(
            self, "清理临时 Profile", "删除所有 tmp_ 前缀且未被占用的 Profile。继续？"
        ):
            return
        run_async(
            self.ctx.pm.clear_temporary,
            on_result=lambda n: self._after_change(f"已清理 {n} 个临时 Profile"),
            on_error=lambda msg: error(self, "清理失败", msg),
        )

    def _on_prune(self) -> None:
        if not confirm(
            self, "清理旧 Profile", "删除超过 30 天未使用的临时 Profile。继续？"
        ):
            return
        run_async(
            lambda: self.ctx.pm.prune_older_than(30.0),
            on_result=lambda n: self._after_change(f"已清理 {n} 个旧 Profile"),
            on_error=lambda msg: error(self, "清理失败", msg),
        )

    def _after_change(self, message: str) -> None:
        self.refresh()
        self.data_changed.emit()
        notify(self, message)
