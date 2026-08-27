"""账号管理页。

表格用 QTableView + 自定义模型而非 QTableWidget——账号可能上万行，
QTableWidget 逐格创建 item 会明显卡顿。

覆盖原 Web 面板 8 个账号路由：list / create / import / delete /
batch_delete / batch_reset / reset / export。

密码永不显示：模型只读 ``to_dict()``（默认 mask_password=True）。

第一列是勾选框：勾选状态按「账号名」记住，因此翻页、刷新都不会丢，
「全选」也能跨页勾选当前筛选下的全部账号（典型用法：筛出「登录成功」
→ 全选 → 删除）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
from ..theme import account_status_color, apply_row_height, fit_checkbox, fit_input
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


#: 勾选列的内部 key
_CHECK_KEY = "__check__"

#: 勾选列宽度（指示器 15px + 两侧留白）
_CHECK_COL_WIDTH = 38


class AccountTableModel(QAbstractTableModel):
    """账号表格模型。数据来自 ``AccountManager.list()``。

    第 0 列是勾选框。勾选集合存的是账号名而不是行号 —— 行号会随翻页、
    筛选、刷新失效，账号名不会。
    """

    #: 勾选数量变化（视图据此更新按钮文字与提示）
    checked_changed = Signal()

    COLUMNS = [
        ("", _CHECK_KEY),
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
        self._checked: set = set()
        #: 刚由 setData 处理过勾选的行号，用于整单元格点击去重
        self._just_toggled_row: Optional[int] = None

    # ---------- Qt 接口 ----------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        key = self.COLUMNS[section][1]
        if role == Qt.DisplayRole:
            return self.COLUMNS[section][0]
        if role == Qt.ToolTipRole and key == _CHECK_KEY:
            return "点击表头可全选/取消本页"
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self.COLUMNS[index.column()][1] == _CHECK_KEY:
            return base | Qt.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        key = self.COLUMNS[index.column()][1]

        if key == _CHECK_KEY:
            if role == Qt.CheckStateRole:
                account = str(row.get("account") or "")
                return Qt.Checked if account in self._checked else Qt.Unchecked
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignCenter)
            return None

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

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if (
            not index.isValid()
            or role != Qt.CheckStateRole
            or self.COLUMNS[index.column()][1] != _CHECK_KEY
        ):
            return False
        account = self.account_at(index.row())
        if not account:
            return False
        # Qt 可能传 int 或 Qt.CheckState，统一比较
        checked = Qt.CheckState(value) == Qt.Checked if value is not None else False
        self._apply_check(account, checked)
        # 标记本行刚由 Qt 处理过指示器点击，view 的 clicked 回调就不再重复翻转
        self._just_toggled_row = index.row()
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        self.checked_changed.emit()
        return True

    # ---------- 数据 ----------
    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()
        # 上一页勾选的账号可能已不在本页，勾选数没变但表头三态要重算
        self.checked_changed.emit()

    # ---------- 勾选 ----------
    def _apply_check(self, account: str, checked: bool) -> None:
        if checked:
            self._checked.add(account)
        else:
            self._checked.discard(account)

    def toggle(self, row: int) -> None:
        """翻转某行勾选。点击勾选列的任意位置都会走这里。"""
        account = self.account_at(row)
        if not account:
            return
        self._apply_check(account, account not in self._checked)
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        self.checked_changed.emit()

    def consume_recent_toggle(self, row: int) -> bool:
        """该行是否刚由 Qt 的指示器点击处理过（一次性消耗）。

        没有这个去重，直接点到小方框上会先走 setData、再走 view 的 clicked，
        两次翻转互相抵消，表现为「点勾选框没反应」。
        """
        if self._just_toggled_row == row:
            self._just_toggled_row = None
            return True
        self._just_toggled_row = None
        return False

    def set_checked(self, accounts: List[str], checked: bool = True) -> None:
        for account in accounts:
            if account:
                self._apply_check(account, checked)
        self._notify_all_rows()

    def set_page_checked(self, checked: bool) -> None:
        for row in self._rows:
            account = str(row.get("account") or "")
            if account:
                self._apply_check(account, checked)
        self._notify_all_rows()

    def clear_checked(self) -> None:
        if not self._checked:
            return
        self._checked.clear()
        self._notify_all_rows()

    def checked_accounts(self) -> List[str]:
        return sorted(self._checked)

    def checked_count(self) -> int:
        return len(self._checked)

    def page_check_state(self) -> Qt.CheckState:
        """本页整体勾选状态，用于工具栏「全选」三态显示。"""
        accounts = [str(r.get("account") or "") for r in self._rows]
        accounts = [a for a in accounts if a]
        if not accounts:
            return Qt.Unchecked
        hits = sum(1 for a in accounts if a in self._checked)
        if hits == 0:
            return Qt.Unchecked
        if hits == len(accounts):
            return Qt.Checked
        return Qt.PartiallyChecked

    def _notify_all_rows(self) -> None:
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._rows) - 1, 0),
                [Qt.CheckStateRole],
            )
        self.checked_changed.emit()

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

        # 勾选相关：本页全选（三态）+ 跨页全选当前筛选结果 + 清空勾选
        self.check_all = QCheckBox("全选本页")
        self.check_all.setTristate(True)
        self.check_all.setToolTip("勾选/取消当前页的全部账号")
        fit_checkbox(self.check_all)
        self.check_all.clicked.connect(self._on_check_all_clicked)

        self.btn_select_matched = button(
            "全选筛选结果",
            tooltip="按当前状态筛选跨页勾选全部账号，例如筛「登录成功」后一键全选",
        )
        self.btn_clear_checked = button("清空勾选")
        self.btn_select_matched.clicked.connect(self._on_select_matched)
        self.btn_clear_checked.clicked.connect(self._on_clear_checked)

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
                self.check_all,
                self.btn_select_matched,
                self.btn_clear_checked,
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
        self.model.checked_changed.connect(self._on_checked_changed)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        # 点勾选列的任意位置都能切换（不必精准命中 15px 的小方框）
        self.table.clicked.connect(self._on_cell_clicked)
        # 行高按运行时字体实测，不写死——中文字体 + 高 DPI 缩放下 28px 会裁字
        apply_row_height(self.table)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, _CHECK_COL_WIDTH)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in range(2, len(AccountTableModel.COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # 点表头勾选列 = 本页全选/取消
        header.sectionClicked.connect(self._on_header_clicked)
        layout.addWidget(self.table, 1)

        self.checked_label = QLabel("")
        self.checked_label.setProperty("role", "hint")
        layout.addWidget(self.checked_label)

        self.btn_prev = button("上一页")
        self.btn_next = button("下一页")
        self.page_label = QLabel("-")
        self.page_label.setProperty("role", "hint")
        self.btn_prev.clicked.connect(lambda: self._page(-1))
        self.btn_next.clicked.connect(lambda: self._page(1))
        layout.addWidget(toolbar(self.page_label, self.btn_prev, self.btn_next, stretch_at=0))

        self._on_checked_changed()

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

    def checked_accounts(self) -> List[str]:
        return self.model.checked_accounts()

    def target_accounts(self) -> List[str]:
        """批量操作的作用对象。

        勾选优先于高亮选中：勾选是显式、可跨页保持的意图，而行高亮很容易
        被一次误点击重置。两者都空时才让调用方提示用户。
        """
        return self.checked_accounts() or self.selected_accounts()

    # ---------- 勾选交互 ----------
    def _on_cell_clicked(self, index) -> None:
        """点勾选列的任意位置切换勾选。

        指示器只有 15px，要求用户精准命中才能勾上很难用；但 Qt 已经处理了
        直接点指示器的情况（走 setData），这里需要去重，否则两次翻转互相抵消。
        """
        if not index.isValid() or index.column() != 0:
            return
        if self.model.consume_recent_toggle(index.row()):
            return
        self.model.toggle(index.row())

    def _on_header_clicked(self, section: int) -> None:
        if section != 0:
            return
        self.model.set_page_checked(self.model.page_check_state() != Qt.Checked)

    def _on_check_all_clicked(self, _checked: bool = False) -> None:
        # 三态勾选框点一下的下一态不直观，这里直接按「本页是不是已全选」判定
        self.model.set_page_checked(self.model.page_check_state() != Qt.Checked)

    def _on_clear_checked(self) -> None:
        self.model.clear_checked()

    def _on_select_matched(self) -> None:
        """跨页勾选当前筛选/搜索匹配的全部账号。

        需要它是因为列表默认每页 200 行，而「登录成功」可能有几千个 ——
        只能全选本页的话用户要翻几十页。
        """
        status = self._current_status()
        keyword = self.search.text().strip().lower()

        def work():
            if status:
                names = self.ctx.am.accounts_with_status(status)
            else:
                names = [a.account for a in self.ctx.am.list(limit=1000000)]
            if keyword:
                names = [n for n in names if keyword in n.lower()]
            return names

        def done(names: List[str]) -> None:
            self.model.set_checked(names, True)
            label = self.status_filter.currentText()
            scope = f"【{label}】" if status else "全部"
            notify(self, f"已勾选 {scope} {len(names)} 个账号")

        run_async(work, on_result=done, on_error=lambda msg: error(self, "全选失败", msg))

    def _on_checked_changed(self) -> None:
        """勾选变化后同步表头三态、提示文字与按钮文案。"""
        count = self.model.checked_count()
        state = self.model.page_check_state()
        self.check_all.blockSignals(True)
        self.check_all.setCheckState(state)
        self.check_all.blockSignals(False)
        self.btn_clear_checked.setEnabled(count > 0)

        if count:
            self.checked_label.setText(f"已勾选 {count} 个账号（删除/重置优先作用于勾选项）")
            self.btn_delete.setText(f"删除勾选 ({count})")
            self.btn_reset.setText(f"重置勾选 ({count})")
        else:
            self.checked_label.setText("未勾选：删除/重置作用于表格中高亮的行")
            self.btn_delete.setText("删除")
            self.btn_reset.setText("重置状态")

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
        accounts = self.target_accounts()
        if not accounts:
            info(self, "重置状态", "请先勾选（或选中）要重置的账号")
            return
        if not confirm(
            self,
            "重置状态",
            f"将 {len(accounts)} 个账号状态打回「未处理」，"
            "已有的失败备注会被清除。继续？",
        ):
            return

        run_async(
            lambda: self.ctx.am.reset_many(accounts),
            on_result=lambda n: self._after_change(f"已重置 {n} 个账号", clear_checked=True),
            on_error=lambda msg: error(self, "重置失败", msg),
        )

    def _on_delete(self) -> None:
        accounts = self.target_accounts()
        if not accounts:
            info(self, "删除账号", "请先勾选（或选中）要删除的账号")
            return
        preview = "、".join(accounts[:3])
        if len(accounts) > 3:
            preview += f" 等 {len(accounts)} 个"
        if not confirm(
            self,
            "删除账号",
            f"确认删除 {len(accounts)} 个账号？\n\n{preview}\n\n"
            "此操作不可恢复，账号的历史任务记录会保留但失去关联。",
            danger=True,
        ):
            return

        run_async(
            lambda: self.ctx.am.remove_many(accounts),
            on_result=lambda n: self._after_change(f"已删除 {n} 个账号", clear_checked=True),
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

    def _after_change(self, message: str, clear_checked: bool = False) -> None:
        # 已删除/已重置的账号再继续勾着没意义，且会让计数牌子误导用户
        if clear_checked:
            self.model.clear_checked()
        self.refresh()
        self.data_changed.emit()
        notify(self, message)
