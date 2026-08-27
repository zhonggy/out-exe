"""账号页勾选框冒烟：按状态筛选 → 全选 → 批量删除。

覆盖用户的主场景：只想删掉「登录成功」的账号，不想逐行 Ctrl+点。
重点验证三件容易写错的事：

1. 勾选状态按账号名记忆，翻页/刷新后不丢；
2. 「全选筛选结果」是跨页的（每页只 200 行，OK 可能几千个）；
3. 删除作用于勾选项而不是表格高亮行，且不会误删其他状态的账号。
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

OK_COUNT = 7
NEW_COUNT = 5


def main() -> int:
    if not os.environ.get("OA_DATA_DIR"):
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实数据")
        return 1

    sys.argv = ["main.py"]
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    import desktop.views.accounts_view as av
    from desktop.bridge.tasks import wait_for_idle
    from desktop.context import AppContext

    # confirm() 直接放行，否则冒烟会卡在模态框上
    av.confirm = lambda *a, **k: True

    ctx = AppContext()
    for i in range(OK_COUNT):
        ctx.db.upsert_account(f"ok{i}@example.invalid", "pw")
        ctx.db.update_account_status(f"ok{i}@example.invalid", "OK")
    for i in range(NEW_COUNT):
        ctx.db.upsert_account(f"new{i}@example.invalid", "pw")

    view = av.AccountsView(ctx)

    def drain(seconds: float = 4.0) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        wait_for_idle(6000)
        for _ in range(40):
            app.processEvents()
            time.sleep(0.01)

    drain(3.0)
    failures: list = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        if ok:
            print(f"[OK]   {label:<24} {detail}")
        else:
            failures.append(f"{label}: {detail}")
            print(f"[FAIL] {label:<24} {detail}")

    # ---------- 1. 勾选列存在且可勾选 ----------
    check(
        "首列是勾选列",
        av.AccountTableModel.COLUMNS[0][1] == av._CHECK_KEY,
        f"COLUMNS[0]={av.AccountTableModel.COLUMNS[0]}",
    )
    index = view.model.index(0, 0)
    check(
        "勾选列可勾选",
        bool(view.model.flags(index) & Qt.ItemIsUserCheckable),
        "",
    )

    # ---------- 2. setData 勾选 + 单元格点击去重 ----------
    view.model.setData(index, Qt.Checked, Qt.CheckStateRole)
    checked_one = view.model.checked_count()
    view._on_cell_clicked(index)  # 紧随其后的 clicked 不应把它翻回去
    check(
        "点击指示器不双翻转",
        checked_one == 1 and view.model.checked_count() == 1,
        f"勾选数={view.model.checked_count()}",
    )
    view.model.clear_checked()

    # ---------- 3. 本页全选 ----------
    view._on_check_all_clicked()
    total = ctx.am.stats()["total"]
    check(
        "全选本页",
        view.model.checked_count() == min(total, view._page_size),
        f"勾选={view.model.checked_count()} 本页={view.model.rowCount()}",
    )
    check("表头三态=全选", view.model.page_check_state() == Qt.Checked, "")
    view._on_clear_checked()
    check("清空勾选", view.model.checked_count() == 0, "")

    # ---------- 4. 筛选「登录成功」→ 全选筛选结果 ----------
    view.status_filter.setCurrentIndex(view.status_filter.findData("OK"))
    drain(3.0)
    view._on_select_matched()
    drain(3.0)
    names = view.model.checked_accounts()
    check(
        "全选筛选结果(OK)",
        len(names) == OK_COUNT and all(n.startswith("ok") for n in names),
        f"勾选={len(names)} 期望={OK_COUNT}",
    )

    # ---------- 5. 勾选跨刷新保持 ----------
    view.refresh()
    drain(3.0)
    check(
        "刷新后勾选保持",
        view.model.checked_count() == OK_COUNT,
        f"勾选={view.model.checked_count()}",
    )

    # ---------- 6. 删除作用于勾选项 ----------
    check(
        "删除目标=勾选项",
        sorted(view.target_accounts()) == sorted(names),
        f"{len(view.target_accounts())} 个",
    )
    view._on_delete()
    drain(5.0)
    counts = ctx.db.count_accounts()
    check(
        "OK 账号已全删",
        counts.get("OK", 0) == 0,
        f"剩余 OK={counts.get('OK', 0)}",
    )
    check(
        "NEW 账号未受影响",
        counts.get("NEW", 0) == NEW_COUNT,
        f"NEW={counts.get('NEW', 0)} 期望={NEW_COUNT}",
    )
    check("删除后勾选已清空", view.model.checked_count() == 0, "")

    ctx.shutdown()

    print()
    if failures:
        print(f"勾选删除冒烟失败：{len(failures)} 项")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("勾选删除冒烟通过：筛选 → 全选 → 批量删除链路正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
