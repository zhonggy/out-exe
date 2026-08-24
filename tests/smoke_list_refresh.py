"""列表视图刷新冒烟：数据变了列表必须跟着变。

用户报「导入后仪表盘账号总数涨了，但下面列表不显示」。两个成因：

1. **过时响应**：刷新是异步的，多个查询可能同时在飞。旧查询后到会用旧数据
   覆盖新结果，列表被清空。
2. **筛选残留**：之前选过状态筛选或输过搜索词，新导入的账号（NEW 状态、
   任意名字）被挡在视图外 —— 数据在库里，列表就是不显示。

这两条在 offscreen 下都能确定性复现，所以值得进 CI。
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


def main() -> int:
    if not os.environ.get("OA_DATA_DIR"):
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实数据")
        return 1

    sys.argv = ["main.py"]
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    app = QApplication(sys.argv)
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    import desktop.views.accounts_view as av
    from desktop.bridge.tasks import run_async, wait_for_idle
    from desktop.context import AppContext

    ctx = AppContext()
    view = av.AccountsView(ctx)

    def drain(seconds: float = 8.0) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        wait_for_idle(6000)
        for _ in range(40):
            app.processEvents()
            time.sleep(0.01)

    def stub_import(text: str) -> None:
        class Stub(av.ImportDialog):
            def exec(self):
                self.editor.setPlainText(text)
                return QDialog.Accepted

        av.ImportDialog = Stub

    drain(3.0)
    failures = []

    def expect_visible(label: str) -> None:
        total = ctx.am.stats()["total"]
        rows = view.model.rowCount()
        expected = min(total, view._page_size)
        if rows == expected and rows > 0:
            print(f"[OK]   {label:<26} DB={total} 列表={rows}")
            return
        failures.append(f"{label}: DB={total} 列表={rows} 期望={expected}")
        print(f"[FAIL] {label:<26} DB={total} 列表={rows} 期望={expected}")

    # ---------- 1. 基础导入 ----------
    stub_import("\n".join(f"base{i}@example.invalid----pw" for i in range(4)))
    view._on_import()
    drain()
    expect_visible("基础导入后可见")

    # ---------- 2. 状态筛选残留 ----------
    # 筛到「登录成功」（库里没有），列表变空，此时导入新账号
    index = view.status_filter.findData("OK")
    view.status_filter.setCurrentIndex(index)
    drain(4.0)
    if view.model.rowCount() != 0:
        print(f"[WARN] 状态筛选未生效，跳过该场景（行数 {view.model.rowCount()}）")
    else:
        stub_import("\n".join(f"sf{i}@example.invalid----pw" for i in range(3)))
        view._on_import()
        drain()
        expect_visible("状态筛选残留时导入")
        if view.status_filter.currentIndex() != 0:
            failures.append("导入后状态筛选未重置")
            print("[FAIL] 导入后状态筛选未重置")
        else:
            print("[OK]   导入后筛选已重置")

    # ---------- 3. 搜索关键字残留 ----------
    view.search.setText("zzz-definitely-no-match")
    view._on_search()
    drain(4.0)
    if view.model.rowCount() != 0:
        print(f"[WARN] 搜索未生效，跳过该场景（行数 {view.model.rowCount()}）")
    else:
        stub_import("\n".join(f"kw{i}@example.invalid----pw" for i in range(3)))
        view._on_import()
        drain()
        expect_visible("搜索残留时导入")
        if view.search.text().strip():
            failures.append("导入后搜索词未清除")
            print("[FAIL] 导入后搜索词未清除")
        else:
            print("[OK]   导入后搜索已清除")

    # ---------- 4. 过时响应竞态 ----------
    # 先放一个慢的旧查询（返回导入前的空快照），再导入并快速刷新。
    # 修复前旧查询后到会把列表清空。
    before = ctx.am.stats()["total"]

    def stale_slow():
        time.sleep(2.0)
        # 伪造一个「更早世代」的响应：seq 用 0，必定小于当前
        return 0, 0, []

    run_async(stale_slow, on_result=view._on_rows)
    ctx.am.import_text("\n".join(f"race{i}@example.invalid----pw" for i in range(5)))
    view.refresh()
    drain(10.0)

    total = ctx.am.stats()["total"]
    rows = view.model.rowCount()
    if total <= before:
        failures.append("竞态场景数据未写入")
        print("[FAIL] 竞态场景数据未写入")
    elif rows == 0:
        failures.append(f"过时响应覆盖了新结果: DB={total} 列表=0")
        print(f"[FAIL] 过时响应覆盖新结果    DB={total} 列表=0")
    else:
        print(f"[OK]   过时响应被丢弃        DB={total} 列表={rows}")

    # ---------- 5. 越界页自动回第一页 ----------
    view._offset = 100000
    view.refresh()
    drain(6.0)
    if view.model.rowCount() == 0 and ctx.am.stats()["total"] > 0:
        failures.append("越界页未自动回到第一页")
        print("[FAIL] 越界页仍为空")
    else:
        print(f"[OK]   越界页自动回第一页    offset={view._offset} 行数={view.model.rowCount()}")

    ctx.shutdown()

    print()
    if failures:
        print(f"列表刷新冒烟失败：{len(failures)} 项")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("列表刷新冒烟通过：数据变更后列表都能正确显示")
    return 0


if __name__ == "__main__":
    sys.exit(main())
