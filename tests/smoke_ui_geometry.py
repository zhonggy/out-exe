"""界面几何冒烟：任何控件都不能放不下它要显示的文字。

用户报「字都显示不全」。v1.0.3 只修了表格行高，实际还有三类问题：

1. **工具栏挤压**：QHBoxLayout 放不下时压缩可伸缩控件而不换行。
   账号页 9 个控件在 1030px 宽下需要 1026px，搜索框被压到 74px，
   占位符根本显示不出来。改用 FlowLayout 自动换行。
2. **固定像素宽度**：``setMaximumWidth(260)`` 这类值在字体变化时不跟着变。
   改成按内容文字宽度计算（``fit_input`` / ``fit_checkbox`` / ``fit_spinbox``）。
3. **构造期字号 ≠ 运行期字号**：控件还没被样式表 polish 时
   ``widget.font()`` 是 9pt 默认字体，比样式表的 14px 窄；Qt 还会按系统
   DPI 缩放样式表的 px（125% → 18px）。所以构造期只能估下限，
   必须在 show 后用真实字体重算一遍（``refit_widget_tree``）。

本测试按 100%/125%/150% 三档缩放、两种窗口宽度，逐控件对比
"实际可用尺寸" vs "文字需要的尺寸"。

判定注意：

- 不能用 ``sizeHint()``。QLineEdit 的 sizeHint 是按约 17 个字符的平均宽度算的
  固定值（~242px），与实际内容无关，会产生大量假阳性。
- 内容宽度超出可视区不一定是 bug：窗口宽度有物理上限，1030px 窗口在 150%
  缩放下等效逻辑宽度只剩约 687px，一条带长 token 的 URL 要 1070px，装不下是
  必然的。判定标准是「放不下时有没有 tooltip 兜底」。
- offscreen 平台的 ``descent`` 恒为 0（``height() == pixelSize``），真实
  Windows 上中文字体的 height 约为字号的 1.33 倍。所以高度判定走
  ``theme.input_min_height()``，它对字号取系数兜底而不只信实测值。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import _console  # noqa: E402,F401

import time  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PAGES = ["仪表盘", "账号管理", "任务管理", "运行日志", "浏览器", "Profile", "代理", "设置", "关于与更新"]

#: 各档 DPI 缩放。真实用户里 125%/150% 是笔记本默认值。
SCALES = ((1.0, "100%"), (1.25, "125%"), (1.5, "150%"))
WIDTHS = (1030, 1280)

#: 真实 Windows 上中文字体 height()/pixelSize 的比例（微软雅黑约 1.33）
CJK_HEIGHT_RATIO = 1.33

#: 样式表给输入类控件的上下 padding（6×2）+ 边框（1×2）
INPUT_VPAD = 14


def required_input_height(widget) -> int:
    """输入类控件需要的最小高度 —— 独立判定，不依赖被测实现。

    offscreen 平台的 ``descent`` 恒为 0（``height() == pixelSize``），
    用它判会漏掉真机上的裁字。``lineSpacing()`` 在 offscreen 下不退化
    （14px → 16），再与「字号 × 1.33」取大，兼顾两种环境。
    """
    from PySide6.QtGui import QFontMetrics

    font = widget.font()
    metrics = QFontMetrics(font)
    pixels = font.pixelSize()
    if pixels <= 0:
        pixels = max(1, metrics.height())
    return max(metrics.lineSpacing(), int(pixels * CJK_HEIGHT_RATIO)) + INPUT_VPAD


def main() -> int:
    if not os.environ.get("OA_DATA_DIR"):
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实数据")
        return 1

    sys.argv = ["main.py"]
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QTableView,
    )

    app = QApplication(sys.argv)
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    from desktop.theme import FONT_SIZE, apply_theme, refit_widget_tree

    apply_theme(app)

    from desktop.context import AppContext
    from desktop.main_window import MainWindow

    ctx = AppContext()
    for index in range(8):
        ctx.db.upsert_account(f"geom{index}@example.invalid", "pw")

    window = MainWindow(ctx)
    window.show()

    def pump(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

    pump(2.0)

    def scale_fonts(scale: float) -> None:
        """模拟 DPI 缩放：offscreen 下拿不到真实缩放，直接放大控件字号。"""
        if scale == 1.0:
            return
        pixels = round(FONT_SIZE * scale)
        for page in window._pages.values():
            widgets = (
                page.findChildren(QLabel)
                + page.findChildren(QPushButton)
                + page.findChildren(QComboBox)
                + page.findChildren(QCheckBox)
                + page.findChildren(QSpinBox)
                + page.findChildren(QLineEdit)
                + page.findChildren(QTableView)
            )
            for widget in widgets:
                font = QFont(widget.font())
                font.setPixelSize(pixels)
                widget.setFont(font)

    def visit_all_pages(delay: float = 0.3) -> None:
        for index in range(len(PAGES)):
            window.nav.setCurrentRow(index)
            pump(delay)

    def audit(page) -> list:
        issues = []

        # QSpinBox 内部有 QLineEdit 子控件，收集起来避免重复统计
        inner = set()
        for spin in page.findChildren(QSpinBox):
            for editor in spin.findChildren(QLineEdit):
                inner.add(id(editor))
            if not spin.isVisible():
                continue
            metrics = QFontMetrics(spin.font())
            widest = f"{spin.prefix()}{spin.maximum()}{spin.suffix()}"
            editors = spin.findChildren(QLineEdit)
            available = editors[0].width() if editors else spin.width() - 38
            need = metrics.horizontalAdvance(widest)
            if available < need + 2:
                issues.append(("SpinBox", widest, f"编辑区 {available} < 文字 {need}"))

        for label in page.findChildren(QLabel):
            if not label.isVisible():
                continue
            text = label.text().strip()
            if not text:
                continue
            metrics = QFontMetrics(label.font())
            if label.height() + 1 < metrics.height():
                issues.append(
                    ("Label高", text[:28], f"h={label.height()} 字高={metrics.height()}")
                )
                continue
            # 换行/富文本的宽度由布局决定，跳过宽度判定
            if label.wordWrap() or "<" in text:
                continue
            need = metrics.horizontalAdvance(text)
            if label.width() + 2 < need:
                issues.append(("Label宽", text[:28], f"w={label.width()} 需要={need}"))

        for button in page.findChildren(QPushButton):
            if not button.isVisible() or not button.text().strip():
                continue
            metrics = QFontMetrics(button.font())
            need = metrics.horizontalAdvance(button.text()) + 32
            if button.width() + 2 < need:
                issues.append(("Button", button.text()[:22], f"w={button.width()} 需要={need}"))

        for combo in page.findChildren(QComboBox):
            if not combo.isVisible():
                continue
            metrics = QFontMetrics(combo.font())
            longest = max(
                (combo.itemText(i) for i in range(combo.count())),
                key=metrics.horizontalAdvance,
                default="",
            )
            need = metrics.horizontalAdvance(longest) + 44
            if combo.width() + 2 < need:
                issues.append(("ComboBox", longest[:22], f"w={combo.width()} 需要={need}"))

        for box in page.findChildren(QCheckBox):
            if not box.isVisible():
                continue
            metrics = QFontMetrics(box.font())
            need = metrics.horizontalAdvance(box.text()) + 26
            if box.width() + 2 < need:
                issues.append(("CheckBox", box.text()[:22], f"w={box.width()} 需要={need}"))

        # 输入类控件高度。
        #
        # 判定标准在测试内独立计算，**不调用 theme.input_min_height()** ——
        # 否则实现放宽时判定标准跟着放宽，形成自证循环（踩过：回退兜底系数
        # 后测试仍全绿）。
        for widget in (
            page.findChildren(QLineEdit)
            + page.findChildren(QComboBox)
            + page.findChildren(QSpinBox)
        ):
            if not widget.isVisible():
                continue
            need = required_input_height(widget)
            if widget.height() + 1 < need:
                kind = type(widget).__name__
                issues.append((f"{kind}高", "-", f"h={widget.height()} 需要={need}"))

        for edit in page.findChildren(QLineEdit):
            if id(edit) in inner or not edit.isVisible():
                continue
            probe = edit.text() or edit.placeholderText()
            if not probe:
                continue
            metrics = QFontMetrics(edit.font())
            need = metrics.horizontalAdvance(probe) + 20
            if edit.width() + 2 < need:
                # 内容确实放不下时，必须有 tooltip 让用户能看到全文。
                # 窗口宽度有物理上限，长 URL 装不下是必然的，不算 bug；
                # 没有兜底手段才算。
                if probe in (edit.toolTip() or ""):
                    continue
                issues.append(
                    ("LineEdit", probe[:28], f"w={edit.width()} 需要={need} 且无 tooltip 兜底")
                )

        for table in page.findChildren(QTableView):
            if not table.isVisible():
                continue
            metrics = QFontMetrics(table.font())
            row = table.verticalHeader().defaultSectionSize()
            if row < metrics.height() + 8:
                issues.append(("表格行高", "-", f"row={row} 字高={metrics.height()}"))
        return issues

    failures = []
    for scale, scale_name in SCALES:
        for width in WIDTHS:
            window.resize(width, 700)
            pump(0.6)

            # 真实启动时序：样式表在 DPI 已确定时生效 → show → refit
            scale_fonts(scale)
            visit_all_pages(0.25)
            refit_widget_tree(window)
            visit_all_pages(0.25)
            pump(0.4)

            total = 0
            for index, name in enumerate(PAGES):
                window.nav.setCurrentRow(index)
                pump(0.3)
                issues = audit(window._pages[name])
                if issues:
                    total += len(issues)
                    for kind, text, detail in issues[:6]:
                        print(f"[FAIL] {scale_name} {width}px [{name}] {kind} {text} {detail}")
            if total:
                failures.append(f"{scale_name} {width}px: {total} 处")
            else:
                print(f"[OK]   {scale_name} {width}px  所有控件都放得下")

    ctx.shutdown()

    print()
    if failures:
        print(f"界面几何冒烟失败：{len(failures)} 种组合有问题")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("界面几何冒烟通过：3 档缩放 × 2 种宽度，无控件放不下")
    return 0


if __name__ == "__main__":
    sys.exit(main())
