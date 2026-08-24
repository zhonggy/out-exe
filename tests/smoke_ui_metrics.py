"""界面度量与并发配置冒烟。

用户报「字都显示不全」，根因是行高与部分 Label 高度写死了像素值。
中文字体的实际占高约为像素字号的 1.3~1.4 倍，再叠加 Windows DPI 缩放
（125%/150%/175%），写死的 28px 在高缩放下必然裁字。

这条测试盯住两件事：
1. 所有表格行高 >= 文字实测高度 + 余量
2. 关键 Label 的实际高度不小于 sizeHint（小于就是被压缩=显示不全）

顺带验证并发线程数的默认值与文案（默认 1，不再叫 Worker 数）。
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

#: 行高相对文字高度的最小余量（上下各 5px）
MIN_ROW_PADDING = 10


def main() -> int:
    if not os.environ.get("OA_DATA_DIR"):
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实数据")
        return 1

    sys.argv = ["main.py"]
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

    app = QApplication(sys.argv)
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    from desktop.theme import apply_theme, text_height

    apply_theme(app)

    from desktop.context import AppContext
    from desktop.main_window import MainWindow

    ctx = AppContext()
    for index in range(12):
        ctx.db.upsert_account(f"ui{index}@example.invalid", "pw")

    window = MainWindow(ctx)
    window.show()

    def pump(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

    pump(3.0)
    failures: list = []

    # ---------- 1. 表格行高 ----------
    print("=== 表格行高 ===")
    for name in ("账号管理", "任务管理", "Profile", "代理"):
        page = window._pages.get(name)
        table = getattr(page, "table", None)
        if table is None:
            continue
        row = table.verticalHeader().defaultSectionSize()
        need = text_height(table) + MIN_ROW_PADDING
        header = table.horizontalHeader().height()
        if row < need:
            failures.append(f"{name} 行高 {row} < 需要 {need}")
            print(f"[FAIL] {name:<10} 行高={row} 需要>={need} 表头={header}")
        elif header < need:
            failures.append(f"{name} 表头高 {header} < 需要 {need}")
            print(f"[FAIL] {name:<10} 表头={header} 需要>={need}")
        else:
            print(f"[OK]   {name:<10} 行高={row} 表头={header} 字高={text_height(table)}")

    # ---------- 2. Label 是否被压缩 ----------
    print()
    print("=== Label 高度（h < sizeHint 即显示不全）===")
    squeezed = []
    for page_name, page in window._pages.items():
        for label in page.findChildren(QLabel):
            text = label.text().strip()
            if not text or not label.isVisible():
                continue
            # 换行 Label 的 sizeHint 与实际布局宽度相关，只查单行的
            if label.wordWrap() or "<br>" in text or "\n" in text:
                continue
            if label.height() < label.sizeHint().height():
                squeezed.append(
                    (page_name, text[:24], label.height(), label.sizeHint().height())
                )
    if squeezed:
        for item in squeezed[:10]:
            print(f"[FAIL] {item[0]:<8} {item[1]:<26} h={item[2]} sizeHint={item[3]}")
        failures.append(f"{len(squeezed)} 个 Label 被压缩")
    else:
        print("[OK]   没有单行 Label 被压缩")

    # ---------- 3. 仪表盘指标卡 ----------
    print()
    print("=== 仪表盘指标卡 ===")
    dashboard = window._pages["仪表盘"]
    bad_cards = []
    for key, card in dashboard.cards.items():
        value = card._value
        if value.height() < value.sizeHint().height():
            bad_cards.append(key)
    if bad_cards:
        failures.append(f"指标卡数字被压缩: {bad_cards}")
        print(f"[FAIL] 指标卡数字被压缩: {bad_cards}")
    else:
        sample = next(iter(dashboard.cards.values()))
        print(f"[OK]   数字高度={sample._value.height()} (28px 字号)")

    # ---------- 4. 并发线程默认值 ----------
    print()
    print("=== 并发线程配置 ===")
    configured = int(ctx.cfg.get("system.max_workers", 0) or 0)
    if configured != 1:
        failures.append(f"默认并发线程数应为 1，实为 {configured}")
        print(f"[FAIL] 配置默认值 = {configured}，期望 1")
    else:
        print("[OK]   配置默认值 = 1")

    tasks_page = window._pages["任务管理"]
    settings_page = window._pages["设置"]
    for label, spin in (
        ("任务页", tasks_page.workers_spin),
        ("设置页", settings_page.max_workers),
    ):
        if spin.value() != 1:
            failures.append(f"{label}并发线程控件默认 {spin.value()}，期望 1")
            print(f"[FAIL] {label}控件默认 = {spin.value()}")
        else:
            print(f"[OK]   {label}控件默认 = 1  范围 {spin.minimum()}-{spin.maximum()}")

    # ---------- 5. 文案不再出现 "Worker 数" ----------
    stale = []
    for page_name, page in window._pages.items():
        for label in page.findChildren(QLabel):
            if "Worker 数" in label.text():
                stale.append((page_name, label.text()))
    if stale:
        failures.append(f"仍有 Worker 数文案: {stale}")
        print(f"[FAIL] 仍有旧文案: {stale}")
    else:
        print("[OK]   界面无「Worker 数」旧文案")

    # ---------- 6. 派发对话框 ----------
    print()
    print("=== 派发对话框 ===")
    from database import AccountStatus
    from desktop.views.tasks_view import DISPATCH_SOFT_LIMIT, DispatchDialog

    pending = int(ctx.am.stats()["by_status"].get(AccountStatus.NEW.value, 0))
    dialog = DispatchDialog(["login"], pending)

    if dialog.limit.value() != min(pending, DISPATCH_SOFT_LIMIT):
        failures.append(
            f"派发数量默认 {dialog.limit.value()}，期望 {min(pending, DISPATCH_SOFT_LIMIT)}"
        )
        print(f"[FAIL] 数量默认={dialog.limit.value()} 期望={min(pending, DISPATCH_SOFT_LIMIT)}")
    else:
        print(f"[OK]   数量默认={dialog.limit.value()} (= 待处理数 {pending})")

    if dialog.limit.maximum() != DISPATCH_SOFT_LIMIT:
        failures.append(f"派发上限 {dialog.limit.maximum()} != {DISPATCH_SOFT_LIMIT}")
        print(f"[FAIL] 上限={dialog.limit.maximum()}")
    else:
        print(f"[OK]   单次上限={DISPATCH_SOFT_LIMIT}")

    if not dialog.summary.text():
        failures.append("派发对话框缺少数量摘要")
        print("[FAIL] 无摘要文案")
    else:
        print(f"[OK]   摘要: {dialog.summary.text()}")

    empty = DispatchDialog(["login"], 0)
    if "没有" not in empty.summary.text():
        failures.append("零待处理时未给出明确提示")
        print(f"[FAIL] 零待处理摘要: {empty.summary.text()}")
    else:
        print(f"[OK]   零待处理提示: {empty.summary.text()}")

    # ---------- 7. 高 DPI 缩放 ----------
    # offscreen 平台的字体度量退化（height() == pixelSize），所以直接放大
    # widget 字号来模拟 Windows 的 125%/150%/175% 缩放。
    # 这是真实用户场景：笔记本默认就是 125%~150%。
    print()
    print("=== 高 DPI 缩放下的行高 ===")
    from PySide6.QtGui import QFont

    from desktop.theme import FONT_SIZE, row_height

    probe = QLabel()
    for scale in (1.0, 1.25, 1.5, 1.75, 2.0):
        font = QFont(probe.font())
        font.setPixelSize(round(FONT_SIZE * scale))
        probe.setFont(font)
        need = text_height(probe) + MIN_ROW_PADDING
        got = row_height(probe)
        if got < need:
            failures.append(f"{int(scale * 100)}% 缩放行高 {got} < 需要 {need}")
            print(f"[FAIL] {int(scale * 100):>4}% 字号={font.pixelSize()} 行高={got} 需要>={need}")
        else:
            print(f"[OK]   {int(scale * 100):>4}% 字号={font.pixelSize()} 行高={got} 字高={text_height(probe)}")

    ctx.shutdown()

    print()
    if failures:
        print(f"界面度量冒烟失败：{len(failures)} 项")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("界面度量冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
