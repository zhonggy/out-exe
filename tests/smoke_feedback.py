"""操作反馈冒烟：每个"会改变状态的按钮"都必须给出可见反馈。

用户报的问题是"点击按钮没有提示操作成功"。根因（回调丢失）已修，
但光修根因不够 —— 得有测试盯住"每个操作都有反馈"这件事本身，
否则以后新加按钮又会忘。

做法：把 QMessageBox 的静态方法与主窗口 show_status 都换成记录器，
逐个触发操作，断言至少有一种反馈发生。
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

# 记录所有反馈
DIALOGS: list = []
STATUS: list = []


def _install_recorders() -> None:
    from PySide6.QtWidgets import QMessageBox

    for name in ("information", "warning", "critical"):
        def stub(parent, title, text, *a, _n=name, **kw):
            DIALOGS.append((_n, title, str(text)[:60]))
            return QMessageBox.Ok

        setattr(QMessageBox, name, staticmethod(stub))

    # confirm 一律放行，否则需要人工点确定
    import desktop.views.widgets as widgets

    widgets.confirm = lambda parent, title, text, danger=False: True

    original_notify = widgets.notify

    def notify_recorder(widget, message, level="ok"):
        STATUS.append((level, message))
        original_notify(widget, message, level)

    widgets.notify = notify_recorder

    # 各页面是 from .widgets import notify 直接绑进模块命名空间的，
    # 必须逐个替换，否则替换 widgets.notify 对它们无效。
    for mod in (
        "desktop.views.accounts_view",
        "desktop.views.tasks_view",
        "desktop.views.profiles_view",
        "desktop.views.proxy_view",
        "desktop.views.browser_view",
        "desktop.views.settings_view",
    ):
        __import__(mod)
        module = sys.modules[mod]
        if hasattr(module, "notify"):
            module.notify = notify_recorder
        if hasattr(module, "confirm"):
            module.confirm = lambda parent, title, text, danger=False: True


def main() -> int:
    if not os.environ.get("OA_DATA_DIR"):
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实数据")
        return 1

    sys.argv = ["main.py"]
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    _install_recorders()

    from desktop.bridge.tasks import wait_for_idle
    from desktop.context import AppContext

    ctx = AppContext()

    # 造点数据，否则很多操作是空操作
    for i in range(3):
        ctx.db.upsert_account(f"fb{i}@example.invalid", "pw")

    def drain(seconds: float = 10.0) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            if DIALOGS or STATUS:
                # 再多跑一会儿，让后续回调也进来
                extra = time.time() + 0.6
                while time.time() < extra:
                    app.processEvents()
                    time.sleep(0.02)
                break
            time.sleep(0.02)
        wait_for_idle(5000)
        for _ in range(40):
            app.processEvents()
            time.sleep(0.01)

    failures: list = []

    def check(label: str, action, needs_selection: bool = False) -> None:
        DIALOGS.clear()
        STATUS.clear()
        try:
            action()
        except Exception as exc:
            failures.append(f"{label}: 抛异常 {exc.__class__.__name__}: {exc}")
            print(f"[FAIL] {label:<22} 抛异常 {exc}")
            return
        drain()
        if not DIALOGS and not STATUS:
            failures.append(f"{label}: 无任何反馈")
            print(f"[FAIL] {label:<22} 无任何反馈（用户会以为没点上）")
            return
        source = "弹窗" if DIALOGS else "状态栏"
        detail = (DIALOGS[-1][1] if DIALOGS else STATUS[-1][1])
        print(f"[OK]   {label:<22} {source}: {str(detail)[:44]}")

    # ---------- 账号页 ----------
    from desktop.views.accounts_view import AccountsView

    accounts = AccountsView(ctx)
    drain(3.0)
    accounts.table.selectAll()
    check("账号-重置状态", accounts._on_reset)
    accounts.refresh()
    drain(3.0)
    accounts.table.selectAll()
    check("账号-删除", accounts._on_delete)

    # ---------- 任务页 ----------
    for i in range(2):
        ctx.db.upsert_account(f"tk{i}@example.invalid", "pw")
    from desktop.views.tasks_view import TasksView

    tasks = TasksView(ctx)
    drain(3.0)
    check("任务-清空队列", tasks._on_clear_queue)

    # ---------- Profile 页 ----------
    from desktop.views.profiles_view import ProfilesView

    profiles = ProfilesView(ctx)
    drain(3.0)
    check("Profile-清理临时", profiles._on_clear_temp)
    check("Profile-清理旧", profiles._on_prune)

    # ---------- 代理页 ----------
    from desktop.views.proxy_view import ProxyView

    proxy = ProxyView(ctx)
    drain(3.0)
    check("代理-保存本地池", proxy._on_save_local)
    proxy.resin_url.setText("http://127.0.0.1:2260/fb-token")
    check("代理-保存 Resin", proxy._on_save_resin)
    check("代理-重置统计", proxy._on_reset_stats)
    check("代理-试取代理", proxy._on_pick)

    # ---------- 浏览器页 ----------
    from desktop.views.browser_view import BrowserView

    browser = BrowserView(ctx)
    drain(3.0)
    check("浏览器-应用内核", browser._on_apply_kernel)
    DIALOGS.clear()
    STATUS.clear()
    browser._on_check()
    drain(25.0)
    if not STATUS and not DIALOGS:
        failures.append("浏览器-检测环境: 无反馈")
        print("[FAIL] 浏览器-检测环境       无反馈")
    else:
        print(f"[OK]   浏览器-检测环境       状态栏: {STATUS[-1][1] if STATUS else DIALOGS[-1][1]}")

    # ---------- 设置页 ----------
    from desktop.views.settings_view import SettingsView

    settings = SettingsView(ctx)
    drain(3.0)
    check("设置-保存", settings._on_save)

    ctx.shutdown()

    print()
    if failures:
        print(f"操作反馈冒烟失败：{len(failures)} 项无反馈")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("操作反馈冒烟通过：所有操作都有可见反馈")
    return 0


if __name__ == "__main__":
    sys.exit(main())
