"""账号管理页。

表格用 QTableView + 自定义模型而非 QTableWidget——账号可能上万行，
QTableWidget 逐格创建 item 会明显卡顿。

覆盖原 Web 面板 8 个账号路由：list / create / import / delete /
batch_delete / batch_reset / reset / export。

密码永不显示：模型只读 ``to_dict()``（默认 mask_password=True）。
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
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from account import describe
from database import AccountStatus

from ..bridge.tasks import run_async
from ..theme import account_status_color, apply_row_height, fit_input
from .widgets import (
    button,
    confirm,
    error,
    hint_label,
    human_time,
    info,
    notify,
    title_label,
    toolbar,
)

#: 状态筛选下拉项：(显示文本, 状态值)
_STATUS_FILTERS = [("全部", "")] + [
    (describe(s.value), s.value) for s in AccountStatus
]


class AccountTableModel(QAbstractTableModel):
    """账号表格模型。数据来自 ``AccountManager.list()``。"""

    COLUMNS = [
        ("账号", "account"),
        ("状态", "status"),
        ("备注", "note"),
        ("运行次数", "run_count"),
        ("失败次数", "fail_count"),
        ("最近运行", "last_run"),
        ("Profile", "profile_id"),
    ]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    # ---------- Qt 接口 ----------
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
            value = row.get(key)
            if key == "status":
                return describe(str(value))
            if key == "last_run":
                return human_time(value)
            if value in (None, ""):
                return "-"
            return str(value)

        if role == Qt.ForegroundRole and key == "status":
            return QColor(account_status_color(str(row.get("status"))))

        if role == Qt.ToolTipRole:
            note = row.get("note") or ""
            return note or None

        if role == Qt.TextAlignmentRole and key in ("run_count", "fail_count"):
            return int(Qt.AlignCenter)

        return None

    # ---------- 数据 ----------
    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def account_at(self, row: int) -> str:
        if 0 <= row < len(self._rows):
            return str(self._rows[row].get("account") or "")
        return ""

    def row_at(self, row: int) -> Dict[str, Any]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return {}


class ImportDialog(QDialog):
    """粘贴导入。格式与 accounts.txt 一致：``邮箱----密码``。"""

    def __init__(self, separator: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("导入账号")
        self.resize(520, 380)
        layout = QVBoxLayout(self)
        layout.addWidget(
            hint_label(
                f"每行一条，分隔符 <code>{separator}</code>："
                f"<br><code>account@example.com{separator}password</code>"
                "<br>已存在的账号会更新密码，不会重复创建。"
            )
        )
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(f"a@example.com{separator}pass1")
        layout.addWidget(self.editor, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("导入")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.editor.toPlainText()


class AddAccountDialog(QDialog):
    """单个添加。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("添加账号")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.account = QLineEdit()
        self.account.setPlaceholderText("account@example.com")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.note = QLineEdit()
        form.addRow("账号", self.account)
        form.addRow("密码", self.password)
        form.addRow("备注", self.note)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("添加")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> Dict[str, str]:
        return {
            "account": self.account.text().strip(),
            "password": self.password.text(),
            "note": self.note.text().strip(),
        }


class AccountsView(QWidget):
    """账号列表 + 导入/删除/重置/导出。

    两个曾导致「导入后仪表盘有数、列表却空」的坑，都在这里处理：

    1. **过时响应**：刷新是异步的，多个查询可能同时在飞（切页、定时刷新、
       导入后刷新）。旧查询后到会把新结果覆盖掉。用世代号丢弃过时响应。
    2. **筛选残留**：用户之前选了状态筛选或输了搜索词，导入的新账号是 NEW
       且账号名不匹配，于是被挡在视图外 —— 数据在库里，列表就是不显示。
       导入类操作会重置筛选与分页，并明确告知用户。
    """

    #: 请求主窗口刷新仪表盘（账号数变化会影响那边的指标）
    data_changed = Signal()

    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self._page_size = int(context.cfg.get("desktop.table_page_size", 200) or 200)
        self._offset = 0
        self._total = 0
        self._request_seq = 0
        self._build()
        self.refresh()

    # ---------- 构建 ----------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(title_label("账号管理"))

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索账号（回车）")
        self.search.setClearButtonEnabled(True)
        # 宽度按占位符文字算，不写死像素 —— 换字体/放大字号后固定值会装不下
        fit_input(self.search, "搜索账号（回车）xxxxxx", extra=48)
        self.search.returnPressed.connect(self._on_search)

        self.status_filter = QComboBox()
        for text, value in _STATUS_FILTERS:
            self.status_filter.addItem(text, value)
        self.status_filter.currentIndexChanged.connect(self._on_search)

        self.btn_import = button("批量导入", "primary")
        self.btn_import_file = button("从文件导入", tooltip="读取配置中的 accounts.txt")
        self.btn_add = button("添加")
        self.btn_reset = button("重置状态", tooltip="打回 NEW，可重新执行")
        self.btn_delete = button("删除", "danger")
        self.btn_export = button("导出 CSV")
        self.btn_refresh = button("刷新")

        self.btn_import.clicked.connect(self._on_import)
        self.btn_import_file.clicked.connect(self._on_import_file)
        self.btn_add.clicked.connect(self._on_add)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_refresh.clicked.connect(self.refresh)

        layout.addWidget(
            toolbar(
                self.search,
                self.status_filter,
                self.btn_import,
                self.btn_import_file,
                self.btn_add,
                self.btn_reset,
                self.btn_delete,
                self.btn_export,
                self.btn_refresh,
                stretch_at=1,
            )
        )

        self.model = AccountTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        # 行高按运行时字体实测，不写死——中文字体 + 高 DPI 缩放下 28px 会裁字
        apply_row_height(self.table)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(AccountTableModel.COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.btn_prev = button("上一页")
        self.btn_next = button("下一页")
        self.page_label = QLabel("-")
        self.page_label.setProperty("role", "hint")
        self.btn_prev.clicked.connect(lambda: self._page(-1))
        self.btn_next.clicked.connect(lambda: self._page(1))
        layout.addWidget(toolbar(self.page_label, self.btn_prev, self.btn_next, stretch_at=0))

    # ---------- 查询 ----------
    def _current_status(self) -> str:
        return str(self.status_filter.currentData() or "")

    def _on_search(self) -> None:
        self._offset = 0
        self.refresh()

    def _page(self, delta: int) -> None:
        new_offset = self._offset + delta * self._page_size
        if new_offset < 0 or new_offset >= max(self._total, 1):
            return
        self._offset = new_offset
        self.refresh()

    def refresh(self) -> None:
        keyword = self.search.text().strip().lower()
        status = self._current_status()
        page_size = self._page_size
        offset = self._offset

        self._request_seq += 1
        seq = self._request_seq

        def work():
            # 关键字搜索没有 SQL 索引支持，拉一批再内存过滤；
            # 无关键字时走分页，避免一次拉全表
            if keyword:
                rows = self.ctx.am.list(status=status or None, limit=20000, offset=0)
                filtered = [a for a in rows if keyword in a.account.lower()]
                total = len(filtered)
                page = filtered[offset:offset + page_size]
            else:
                stats = self.ctx.am.stats()
                if status:
                    total = int(stats["by_status"].get(status, 0))
                else:
                    total = int(stats["total"])
                page = self.ctx.am.list(
                    status=status or None, limit=page_size, offset=offset
                )
            return seq, total, [a.to_dict() for a in page]

        run_async(
            work,
            on_result=self._on_rows,
            on_error=lambda msg: error(self, "加载账号失败", msg),
        )

    def _on_rows(self, payload) -> None:
        seq, total, rows = payload
        # 丢弃过时响应：慢的旧查询后到会覆盖掉刚刚拿到的新结果
        if seq != self._request_seq:
            return

        self._total = total

        # 页码越界（删了很多行、或换了筛选）时回到第一页重查，
        # 否则用户会看到一个空页而误以为数据没了
        if rows == [] and total > 0 and self._offset > 0:
            self._offset = 0
            self.refresh()
            return

        self.model.set_rows(rows)
        start = self._offset + 1 if rows else 0
        end = self._offset + len(rows)
        self.page_label.setText(f"{start}-{end} / 共 {total}")
        self.btn_prev.setEnabled(self._offset > 0)
        self.btn_next.setEnabled(end < total)

    # ---------- 选中项 ----------
    def selected_accounts(self) -> List[str]:
        rows = {i.row() for i in self.table.selectionModel().selectedRows()}
        return [self.model.account_at(r) for r in sorted(rows) if self.model.account_at(r)]

    # ---------- 操作 ----------
    def _on_import(self) -> None:
        separator = str(self.ctx.cfg.get("system.account_separator", "----"))
        dialog = ImportDialog(separator, self)
        if not dialog.exec():
            return
        text = dialog.text().strip()
        if not text:
            return

        def work():
            return self.ctx.am.import_text(text)

        run_async(
            work,
            on_result=lambda r: self._after_import(
                f"已导入 {r.get('imported', 0)} 条，跳过 {r.get('skipped', 0)} 条"
            ),
            on_error=lambda msg: error(self, "导入失败", msg),
        )

    def _on_import_file(self) -> None:
        path = self.ctx.cfg.path_of("system.accounts_file", "accounts.txt")
        if not path.is_file():
            picked, _ = QFileDialog.getOpenFileName(
                self, "选择账号文件", str(self.ctx.cfg.data_root), "文本文件 (*.txt);;所有文件 (*)"
            )
            if not picked:
                return
            target = picked
        else:
            target = str(path)

        run_async(
            lambda: self.ctx.am.import_file(target),
            on_result=lambda r: self._after_import(
                f"已导入 {r.get('imported', 0)} 条，跳过 {r.get('skipped', 0)} 条"
            ),
            on_error=lambda msg: error(self, "导入失败", msg),
        )

    def _on_add(self) -> None:
        dialog = AddAccountDialog(self)
        if not dialog.exec():
            return
        values = dialog.values()
        if not values["account"]:
            error(self, "添加失败", "账号不能为空")
            return

        run_async(
            lambda: self.ctx.am.add(
                values["account"], values["password"], values["note"]
            ),
            on_result=lambda _: self._after_import(f"已添加 {values['account']}"),
            on_error=lambda msg: error(self, "添加失败", msg),
        )

    def _on_reset(self) -> None:
        accounts = self.selected_accounts()
        if not accounts:
            info(self, "重置状态", "请先选中要重置的账号")
            return
        if not confirm(
            self,
            "重置状态",
            f"将 {len(accounts)} 个账号状态打回「未处理」，"
            "已有的失败备注会被清除。继续？",
        ):
            return

        def work():
            for account in accounts:
                self.ctx.am.reset_status(account)
            return len(accounts)

        run_async(
            work,
            on_result=lambda n: self._after_change(f"已重置 {n} 个账号"),
            on_error=lambda msg: error(self, "重置失败", msg),
        )

    def _on_delete(self) -> None:
        accounts = self.selected_accounts()
        if not accounts:
            info(self, "删除账号", "请先选中要删除的账号")
            return
        if not confirm(
            self,
            "删除账号",
            f"确认删除 {len(accounts)} 个账号？\n\n"
            "此操作不可恢复，账号的历史任务记录会保留但失去关联。",
            danger=True,
        ):
            return

        def work():
            for account in accounts:
                self.ctx.am.remove(account)
            return len(accounts)

        run_async(
            work,
            on_result=lambda n: self._after_change(f"已删除 {n} 个账号"),
            on_error=lambda msg: error(self, "删除失败", msg),
        )

    def _on_export(self) -> None:
        default = self.ctx.cfg.resolve("data/accounts_export.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出账号结果", str(default), "CSV 文件 (*.csv)"
        )
        if not path:
            return

        run_async(
            lambda: self.ctx.am.export_csv(path),
            on_result=lambda p: info(self, "导出完成", f"已导出到：\n{p}"),
            on_error=lambda msg: error(self, "导出失败", msg),
        )

    def _reset_view_filters(self) -> str:
        """清空搜索与状态筛选、回到第一页。

        导入的新账号状态是 NEW、账号名任意，残留的筛选会把它们全挡在视图外，
        用户看到的就是「仪表盘涨了但列表没变化」。返回被清掉了什么，
        以便在提示里说明——静默改动用户的筛选条件同样会让人困惑。
        """
        cleared = []
        if self.search.text().strip():
            self.search.clear()
            cleared.append("搜索")
        if self.status_filter.currentIndex() != 0:
            self.status_filter.blockSignals(True)
            self.status_filter.setCurrentIndex(0)
            self.status_filter.blockSignals(False)
            cleared.append("状态筛选")
        if self._offset:
            self._offset = 0
            cleared.append("翻页")
        return "、".join(cleared)

    def _after_import(self, message: str) -> None:
        """导入类操作的收尾：重置筛选，确保新账号立刻可见。"""
        cleared = self._reset_view_filters()
        if cleared:
            message = f"{message}（已清除{cleared}以显示新账号）"
        self.refresh()
        self.data_changed.emit()
        notify(self, message)

    def _after_change(self, message: str) -> None:
        self.refresh()
        self.data_changed.emit()
        notify(self, message)
