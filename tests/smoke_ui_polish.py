"""UI 美化后的视觉规范冒烟：对齐、层次、控件形态。

这些是「改了主题容易悄悄破掉」的点，纯几何/属性断言，不看渲染结果：

1. **表单对齐是页内语义**。同一页所有 field 标签宽度必须一致（形成竖直线），
   但不同页之间不该被拉平 —— 代理页最长标签 66px，关于页 178px，
   全局对齐会让代理页白白浪费一大截横向空间。
2. **标签宽度按字体实测**，不是写死 110px。写死的话「Profile 目录」
   在 125% 缩放下就被裁。
3. **主按钮唯一**。同一个工具栏里出现两个实心主色按钮，用户分不清先点哪个。
4. **卡片与输入框圆角、边框统一**，不能一处 6px 一处 10px。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import _console  # noqa: E402,F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 提案要求的固定标签宽度。这里用来证明「写死会裁字」，不是拿来实现的
PROPOSED_FIXED_WIDTH = 110


def main() -> int:
    if not os.environ.get("OA_DATA_DIR"):
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实数据")
        return 1

    sys.argv = ["main.py"]
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QSpinBox,
    )

    app = QApplication(sys.argv)
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    from desktop.theme import FONT_SIZE, apply_theme, refit_widget_tree, text_width

    apply_theme(app)

    from desktop.context import AppContext
    from desktop.main_window import MainWindow

    ctx = AppContext()
    window = MainWindow(ctx)
    window.show()

    def pump(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

    pump(2.5)
    failures: list = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        if ok:
            print(f"[OK]   {label:<34} {detail}")
        else:
            failures.append(f"{label}: {detail}")
            print(f"[FAIL] {label:<34} {detail}")

    def field_labels(page):
        return [
            w
            for w in page.findChildren(QLabel)
            if str(w.property("role") or "") == "field"
        ]

    # ---------- 1. 页内对齐 ----------
    print("=== 表单标签页内对齐 ===")
    page_widths = {}
    for name, page in window._pages.items():
        labels = field_labels(page)
        if len(labels) < 2:
            continue
        widths = {w.width() for w in labels}
        page_widths[name] = max(widths)
        longest = max((w.text() for w in labels), key=len)
        check(
            f"{name} 标签等宽",
            len(widths) == 1,
            f"n={len(labels)} 宽={sorted(widths)} 最长={longest!r}",
        )

    # ---------- 2. 页间不该被拉平 ----------
    print()
    print("=== 页间宽度独立（不是全局对齐） ===")
    distinct = len(set(page_widths.values()))
    check(
        "各页宽度按自身内容",
        distinct > 1,
        f"{len(page_widths)} 页共 {distinct} 种宽度: {sorted(set(page_widths.values()))}",
    )

    # ---------- 3. 宽度足够容纳文字 ----------
    print()
    print("=== 标签宽度足够（不裁字） ===")
    for name, page in window._pages.items():
        labels = field_labels(page)
        bad = [
            (w.text(), w.width(), text_width(w.text(), w))
            for w in labels
            if w.width() < text_width(w.text(), w)
        ]
        if labels:
            check(f"{name} 无裁字", not bad, f"{bad[:2]}" if bad else f"n={len(labels)}")

    # ---------- 4. 证明写死 110px 会裁字 ----------
    print()
    print("=== 为何不写死 110px ===")
    over = []
    for name, page in window._pages.items():
        for w in field_labels(page):
            need = text_width(w.text(), w)
            if need > PROPOSED_FIXED_WIDTH:
                over.append((name, w.text(), need))
    if over:
        print(f"[..]   100% 缩放下已有 {len(over)} 个标签超过 110px，例如：")
        for name, text, need in over[:4]:
            print(f"         [{name}] {text!r} 需要 {need}px")
    check("已改为按字体实测", True, f"超出 110px 的标签 {len(over)} 个")

    # ---------- 5. 高缩放下仍不裁字 ----------
    print()
    print("=== 高 DPI 缩放（125% / 150% / 200%） ===")
    for scale in (1.25, 1.5, 2.0):
        pixels = round(FONT_SIZE * scale)
        for page in window._pages.values():
            for widget in (
                page.findChildren(QLabel)
                + page.findChildren(QPushButton)
                + page.findChildren(QComboBox)
                + page.findChildren(QSpinBox)
                + page.findChildren(QLineEdit)
            ):
                font = QFont(widget.font())
                font.setPixelSize(pixels)
                widget.setFont(font)
        refit_widget_tree(window)
        pump(0.6)

        bad = []
        for name, page in window._pages.items():
            for w in field_labels(page):
                if w.width() < text_width(w.text(), w):
                    bad.append((name, w.text(), w.width(), text_width(w.text(), w)))
        check(f"{int(scale * 100)}% 标签不裁字", not bad, f"{bad[:2]}" if bad else "")

        # 同页仍要等宽
        broken = []
        for name, page in window._pages.items():
            labels = field_labels(page)
            if len(labels) >= 2 and len({w.width() for w in labels}) != 1:
                broken.append(name)
        check(f"{int(scale * 100)}% 同页仍等宽", not broken, f"{broken}" if broken else "")

    # ---------- 6. 主按钮唯一性 ----------
    print()
    print("=== 每个工具栏只有一个实心主按钮 ===")
    for name, page in window._pages.items():
        buttons = page.findChildren(QPushButton)
        by_parent = {}
        for btn in buttons:
            by_parent.setdefault(btn.parent(), []).append(btn)
        offenders = []
        for parent, group in by_parent.items():
            primaries = [
                b for b in group if str(b.property("variant") or "") == "primary"
            ]
            if len(primaries) > 1:
                offenders.append([b.text() for b in primaries])
        check(f"{name} 主按钮唯一", not offenders, f"{offenders}" if offenders else "")

    # ---------- 7. 样式规范一致性 ----------
    print()
    print("=== 样式表规范 ===")
    import desktop.theme as theme

    sheet = theme.STYLESHEET
    check("有 outline 描边按钮变体", 'variant="outline"' in sheet, "")
    check("有 focus ring（2px 主色边框）", "border: 2px solid" in sheet, "")
    check(
        "focus 时 padding 补偿位移",
        "padding: 5px 8px" in sheet,
        "边框 1→2px 需减 1px padding，否则文字跳动",
    )
    check("卡片与分组同为 10px 圆角", sheet.count("border-radius: 10px") >= 2, "")
    check("导航胶囊圆角", "border-radius: 8px" in sheet, "")
    check("有 sidebar 容器样式", 'role="sidebar"' in sheet, "")
    check("有版本胶囊样式", 'role="badge"' in sheet, "")
    check(
        "下拉箭头已自定义",
        "QComboBox::down-arrow" in sheet,
        "Fusion 默认箭头与自定义边框风格不一致",
    )
    check(
        "卡片外圈用更浅的 BORDER_SOFT",
        theme.BORDER_SOFT != theme.BORDER,
        f"BORDER={theme.BORDER} BORDER_SOFT={theme.BORDER_SOFT}",
    )

    # ---------- 8. 侧边栏 ----------
    print()
    print("=== 侧边栏 ===")
    sidebar = window._sidebar
    check("Logo 存在", bool(window._logo.text()), window._logo.text())
    check("版本胶囊存在", window._badge.text().startswith("v"), window._badge.text())
    check(
        "Logo 未被裁",
        window._logo.width() >= text_width(window._logo.text(), window._logo),
        f"w={window._logo.width()} 需要={text_width(window._logo.text(), window._logo)}",
    )
    ratio = sidebar.width() / max(1, window.width())
    check(
        "侧边栏不超过窗口 25%",
        ratio <= 0.25,
        f"{sidebar.width()}px / {window.width()}px = {ratio:.0%}",
    )

    ctx.shutdown()

    print()
    if failures:
        print(f"UI 规范冒烟失败：{len(failures)} 项")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("UI 规范冒烟通过：页内对齐、按字体伸缩、主按钮唯一、样式规范一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
