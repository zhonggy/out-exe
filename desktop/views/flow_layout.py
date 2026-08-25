"""自动换行的横向布局。

Qt 的 QHBoxLayout 放不下时会压缩可伸缩控件，而不是换行。工具栏塞了
七八个按钮时，被压扁的通常是搜索框——压到 74px 连占位符都显示不出来，
用户看到的就是"字显示不全"。

这个布局改为：宽度不够就折到下一行，每个控件始终拿到它的 sizeHint。
参考 Qt 官方 FlowLayout 示例，补了两处本项目需要的行为：

- ``expanding`` 索引：指定某个控件吃掉本行剩余宽度（搜索框需要）
- 高度缓存：``heightForWidth`` 会被布局系统高频调用，不缓存会卡顿
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """放不下就换行的横向布局。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        margin: int = 0,
        h_spacing: int = 8,
        v_spacing: int = 8,
    ):
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        #: 吃掉本行剩余宽度的控件索引集合
        self._expanding: set = set()
        self._height_cache: Dict[int, int] = {}
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # ---------- QLayout 接口 ----------
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt 命名
        self._items.append(item)
        self._height_cache.clear()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            self._height_cache.clear()
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        cached = self._height_cache.get(width)
        if cached is not None:
            return cached
        height = self._layout(QRect(0, 0, width, 0), apply=False)
        self._height_cache[width] = height
        return height

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    # ---------- 扩展 ----------
    def mark_expanding(self, index: int) -> None:
        """让该索引的控件吃掉本行剩余宽度。"""
        self._expanding.add(index)
        self._height_cache.clear()

    # ---------- 排版 ----------
    @staticmethod
    def _wanted(item: QLayoutItem) -> int:
        """控件真正需要的宽度。

        必须同时看 sizeHint 与 minimum：sizeHint 不含 setMinimumWidth
        设定的值，只用它会让 fit_spinbox() 之类的显式约束失效。
        """
        width = item.sizeHint().width()
        widget = item.widget()
        if widget is not None:
            width = max(
                width,
                widget.minimumWidth(),
                widget.minimumSizeHint().width(),
            )
        return width

    def _layout(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        right = effective.right() + 1

        # 先按 sizeHint 分行
        lines: List[List[int]] = []
        current: List[int] = []
        for index, item in enumerate(self._items):
            wanted = self._wanted(item)
            next_x = x + wanted
            if current and next_x > right:
                lines.append(current)
                current = []
                x = effective.x()
                next_x = x + wanted
            current.append(index)
            x = next_x + self._h_spacing
        if current:
            lines.append(current)

        # 逐行摆放；expanding 控件分掉本行剩余
        y = effective.y()
        for line in lines:
            widths = [self._wanted(self._items[i]) for i in line]
            used = sum(widths) + self._h_spacing * max(0, len(line) - 1)
            spare = max(0, effective.width() - used)
            growers = [pos for pos, idx in enumerate(line) if idx in self._expanding]
            if growers and spare:
                share = spare // len(growers)
                for pos in growers:
                    widths[pos] += share

            x = effective.x()
            line_height = 0
            for pos, index in enumerate(line):
                item = self._items[index]
                width = widths[pos]
                height = item.sizeHint().height()
                if apply:
                    item.setGeometry(QRect(QPoint(x, y), QSize(width, height)))
                x += width + self._h_spacing
                line_height = max(line_height, height)
            y += line_height + self._v_spacing

        total = y - self._v_spacing - effective.y() if lines else 0
        return total + margins.top() + margins.bottom()


def flow_toolbar(
    *widgets: QWidget,
    expanding: Optional[int] = None,
    h_spacing: int = 8,
    v_spacing: int = 8,
) -> QWidget:
    """构造一个会自动换行的工具栏。

    ``expanding`` 指定哪个控件吃掉本行剩余宽度（通常是搜索框）。
    与旧的 ``toolbar()`` 的区别：不再用弹簧把控件推到两端，
    而是靠换行保证每个控件都拿到完整宽度。
    """
    holder = QWidget()
    layout = FlowLayout(holder, margin=0, h_spacing=h_spacing, v_spacing=v_spacing)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget)
        if expanding is not None and index == expanding:
            layout.mark_expanding(index)
    holder.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
    return holder
