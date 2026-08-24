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

from ..theme import TEXT, TEXT_DIM, metric_height, text_height


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


def toolbar(*widgets: QWidget, stretch_at: int = -1) -> QWidget:
    """横向工具栏。stretch_at 指定在第几个控件后插入弹簧。"""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget)
        if index == stretch_at:
            layout.addStretch(1)
    if stretch_at < 0:
        layout.addStretch(1)
    return holder


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
