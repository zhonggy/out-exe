"""页面通用组件：卡片、指标、工具栏、确认框。

抽出来是为了让各页面视觉一致，也避免每个页面重复写 layout 样板。
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme import TEXT, TEXT_DIM, fit_spinbox, metric_height, text_height


def title_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "title")
    # 19px 字号的中文标题，布局若按默认字号算高度会裁掉底部
    label.setMinimumHeight(text_height(label, 19) + 8)
    return label


def hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "hint")
    label.setWordWrap(True)
    # 提示文字常含 <br>，多行时必须让布局按内容算高度
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)
    return label


def card() -> QFrame:
    frame = QFrame()
    frame.setProperty("role", "card")
    return frame


def separator() -> QFrame:
    line = QFrame()
    line.setProperty("role", "separator")
    line.setFixedHeight(1)
    return line


class MetricCard(QFrame):
    """仪表盘指标卡：大数字 + 说明 + 可选颜色。

    大数字用 28px 字号，中文/数字实际占高约 36-40px（还要看 DPI 缩放），
    所以显式设最小高度——否则布局按默认字号算，数字被裁掉下半截。
    """

    def __init__(self, caption: str, value: str = "-", color: str = TEXT):
        super().__init__()
        self.setProperty("role", "card")
        self.setMinimumWidth(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        self._value = QLabel(value)
        self._value.setProperty("role", "metric")
        self._value.setStyleSheet(f"color: {color};")
        self._value.setMinimumHeight(metric_height(self._value))

        self._caption = QLabel(caption)
        self._caption.setProperty("role", "hint")
        self._caption.setMinimumHeight(text_height(self._caption) + 4)
        self._caption.setWordWrap(True)

        layout.addWidget(self._value)
        layout.addWidget(self._caption)

    def set_value(self, value, color: Optional[str] = None) -> None:
        self._value.setText(str(value))
        if color:
            self._value.setStyleSheet(f"color: {color};")

    def set_caption(self, text: str) -> None:
        self._caption.setText(text)


class KeyValueRow(QWidget):
    """左键右值一行，用于状态详情。

    ``elide=True`` 用于长路径这类内容：超宽时中间省略并挂 tooltip，比换行成
    三行整齐。默认换行，适合错误信息这类需要读全的内容。
    """

    def __init__(self, key: str, value: str = "-", elide: bool = False):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(8)

        self._elide = elide
        self._full_text = str(value)
        line = text_height(self) + 4

        self._key = QLabel(key)
        self._key.setStyleSheet(f"color: {TEXT_DIM};")
        self._key.setMinimumWidth(128)
        self._key.setMinimumHeight(line)
        self._key.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self._value = QLabel(value)
        self._value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._value.setWordWrap(not elide)
        self._value.setMinimumHeight(line)
        if elide:
            self._value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            # 允许被压缩到比内容窄，否则长路径会把整行撑开
            self._value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self._value.setToolTip(self._full_text)
        else:
            self._value.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        layout.addWidget(self._key)
        layout.addWidget(self._value, 1)

    def set_value(self, value: str, color: Optional[str] = None) -> None:
        self._full_text = str(value)
        self._value.setStyleSheet(f"color: {color};" if color else "")
        if self._elide:
            self._value.setToolTip(self._full_text)
            self._apply_elide()
        else:
            self._value.setText(self._full_text)

    def _apply_elide(self) -> None:
        from PySide6.QtGui import QFontMetrics

        width = max(60, self._value.width() - 4)
        metrics = QFontMetrics(self._value.font())
        self._value.setText(metrics.elidedText(self._full_text, Qt.ElideMiddle, width))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        super().resizeEvent(event)
        if self._elide:
            self._apply_elide()


def toolbar(
    *widgets: QWidget,
    stretch_at: int = -1,
    expanding: Optional[int] = None,
) -> QWidget:
    """横向工具栏，放不下自动换行。

    原来用 QHBoxLayout + 弹簧，窗口偏窄时 Qt 会压缩可伸缩控件而不是换行 ——
    账号页 9 个控件在 1030px 宽下需要 1026px，搜索框被压到 74px，
    占位符「搜索账号（回车）」根本显示不出来。

    ``stretch_at`` 保留是为了兼容调用方，语义改为「该位置之后的控件
    尽量排在后面」，实际由换行保证每个控件拿到完整宽度。
    ``expanding`` 指定哪个控件吃掉本行剩余宽度（默认沿用 stretch_at 的位置）。
    """
    from .flow_layout import flow_toolbar

    grow = expanding
    if grow is None and stretch_at >= 0:
        # 旧调用里 stretch_at 之前通常是搜索/筛选类控件，让它吃剩余宽度
        grow = stretch_at if stretch_at < len(widgets) else None
    return flow_toolbar(*widgets, expanding=grow)


def button(
    text: str,
    variant: str = "",
    tooltip: str = "",
    enabled: bool = True,
) -> QPushButton:
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setEnabled(enabled)
    btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return btn


def spinbox(
    minimum: int,
    maximum: int,
    value: Optional[int] = None,
    suffix: str = "",
    step: int = 1,
    tooltip: str = "",
):
    """构造一个宽度足够显示最大值的 QSpinBox。

    直接 new QSpinBox 的话，样式表 padding 会把内部编辑器挤到刚好等于
    文字宽度，数字被裁。这里统一走 fit_spinbox() 算宽度。
    """
    from PySide6.QtWidgets import QSpinBox

    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    if suffix:
        spin.setSuffix(suffix)
    if step != 1:
        spin.setSingleStep(step)
    if value is not None:
        spin.setValue(value)
    if tooltip:
        spin.setToolTip(tooltip)
    fit_spinbox(spin)
    return spin


def attach_overflow_tooltip(edit, hint: str = "") -> None:
    """内容超出可视宽度时，悬停显示全文。

    窗口宽度有物理上限：1030px 窗口在 150% 缩放下等效逻辑宽度只剩约
    687px，而一条带长 token 的 URL 要 1070px —— 装不下是必然的。
    Qt 会横向滚动，但用户看不到全貌，所以用 tooltip 补上。

    ``hint`` 是格式说明，始终附在 tooltip 末尾。
    """
    from PySide6.QtGui import QFontMetrics

    def refresh() -> None:
        text = edit.text()
        parts = []
        if text:
            metrics = QFontMetrics(edit.font())
            # 只在真的放不下时才把全文塞进 tooltip，避免无谓的悬浮框
            if metrics.horizontalAdvance(text) > max(1, edit.width() - 20):
                parts.append(text)
        if hint:
            parts.append(hint)
        edit.setToolTip("\n\n".join(parts))

    edit.textChanged.connect(refresh)
    refresh()
    # 存一份引用，防止闭包被 GC（Qt 侧只持有弱引用）
    edit.setProperty("oa_overflow_hint", hint)
    if not hasattr(edit, "_oa_refs"):
        edit._oa_refs = []
    edit._oa_refs.append(refresh)


def notify(widget: QWidget, message: str, level: str = "ok") -> None:
    """向主窗口状态栏回报操作结果。

    页面可能被单独构造（测试场景），此时 window() 没有 show_status，
    因此需要容错。
    """
    window = widget.window()
    handler = getattr(window, "show_status", None)
    if handler is not None:
        handler(message, level)


def confirm(parent: QWidget, title: str, text: str, danger: bool = False) -> bool:
    """确认对话框。危险操作默认焦点在"取消"上，避免手滑回车。"""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    yes = box.addButton("确定", QMessageBox.AcceptRole)
    no = box.addButton("取消", QMessageBox.RejectRole)
    box.setDefaultButton(no if danger else yes)
    box.exec()
    return box.clickedButton() is yes


def warn(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def info(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def error(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)


def human_bytes(size: float) -> str:
    """字节数转可读体积。Profile 页要显示磁盘占用。"""
    units: Iterable[Tuple[str, float]] = (
        ("TB", 1024 ** 4),
        ("GB", 1024 ** 3),
        ("MB", 1024 ** 2),
        ("KB", 1024),
    )
    for unit, factor in units:
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{int(size)} B"


def human_duration(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def human_time(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    import time

    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
