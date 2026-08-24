"""代理页交互冒烟：保存 → 反馈 → 测试 → 反馈。

用户报的两个问题都在这条路径上：
1. 点保存没有成功提示
2. 点测试没有反应

根因是 QThreadPool 回收 QRunnable 导致回调信号丢失（业务执行了但回调没到），
这里用真实页面对象验证回调确实到达、按钮状态确实恢复。

不连真实 Resin 服务：地址指向一个必定连不上的本地端口，
验证的是"失败也必须有明确反馈"，这恰好是用户遇到的场景。
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
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实配置")
        return 1

    sys.argv = ["main.py"]
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)

    # 拦掉模态框：offscreen 下 exec() 会阻塞，且我们要的是"是否被调用"
    shown = []
    for name in ("information", "warning", "critical", "question"):
        original = getattr(QMessageBox, name)

        def stub(parent, title, text, *a, _n=name, **kw):
            shown.append((_n, title, str(text)[:80]))
            return QMessageBox.Ok

        setattr(QMessageBox, name, staticmethod(stub))
    _ = original  # noqa: F841

    from desktop.bridge.tasks import wait_for_idle
    from desktop.context import AppContext
    from desktop.views.proxy_view import ProxyView

    ctx = AppContext()
    view = ProxyView(ctx)
    print("[OK] 页面构造      ProxyView")

    def drain(seconds: float = 8.0, until=None) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            if until is not None and until():
                break
            time.sleep(0.02)
        wait_for_idle(4000)
        for _ in range(40):
            app.processEvents()
            time.sleep(0.01)

    # ---------- 保存 Resin 配置 ----------
    view.resin_enabled.setChecked(True)
    view.resin_url.setText("http://127.0.0.1:2260/smoke-token")
    view.resin_platform.setText("SmokePlatform")

    shown.clear()
    view._on_save_resin()
    drain(8.0, until=lambda: bool(shown))

    if not shown:
        print("[FAIL] 保存后没有任何提示 —— 用户无法确认是否保存成功")
        return 1
    print(f"[OK] 保存有反馈    {shown[-1][0]}: {shown[-1][1]}")

    # 配置必须真的落盘
    saved = ctx.cfg.section("resin")
    if saved.get("url") != "http://127.0.0.1:2260/smoke-token":
        print(f"[FAIL] 配置未落盘  当前 url={saved.get('url')!r}")
        return 1
    if saved.get("platform") != "SmokePlatform":
        print(f"[FAIL] platform 未落盘 {saved.get('platform')!r}")
        return 1
    print(f"[OK] 配置已落盘    {ctx.cfg.source_path}")

    # 重新读文件确认（而不是只信内存里的 Config 对象）
    from config import load_config

    fresh = load_config(use_cache=False).section("resin")
    if fresh.get("url") != "http://127.0.0.1:2260/smoke-token":
        print(f"[FAIL] 重读文件不一致 {fresh.get('url')!r}")
        return 1
    print("[OK] 重读文件一致  写盘内容正确")

    # ---------- 测试连接（预期失败，但必须有反馈） ----------
    # 指向一个必定连不上的端口，验证失败路径的反馈
    view.resin_url.setText("http://127.0.0.1:59999/nonexistent-token")
    shown.clear()
    view.resin_result.setPlainText("")

    view._on_test_resin()
    if view.btn_test_resin.isEnabled():
        print("[WARN] 点测试后按钮未立即禁用，用户可能重复点击")
    else:
        print(f"[OK] 按钮已禁用    文案={view.btn_test_resin.text()!r}")

    # 三个探测端点各自超时，给足时间
    drain(90.0, until=lambda: view.btn_test_resin.isEnabled())

    text = view.resin_result.toPlainText()
    if not text or "测试中" in text or "正在测试" in text:
        print(f"[FAIL] 测试无反应  结果框仍是: {text[:80]!r}")
        print("       这正是用户报的问题：点了测试没有任何变化")
        return 1
    print(f"[OK] 测试有结果    {text.splitlines()[0][:70]}")

    if not view.btn_test_resin.isEnabled():
        print("[FAIL] 按钮未恢复  用户无法再次测试")
        return 1
    if view.btn_test_resin.text() != "测试连接":
        print(f"[FAIL] 按钮文案未恢复 {view.btn_test_resin.text()!r}")
        return 1
    print("[OK] 按钮已恢复    可再次点击")

    # 失败时必须弹窗，不能只写进文本框（用户可能没看那一块）。
    # test_connection 失败时是正常返回 {"ok": False} 而不抛异常，
    # 只接 on_error 会让这类失败完全静默。
    if not shown:
        print("[FAIL] 测试失败未弹窗 —— 用户只能自己注意到结果框变化")
        return 1
    print(f"[OK] 失败有弹窗    {shown[-1][0]}: {shown[-1][1]}")

    # ---------- 本地代理池保存 ----------
    view.local_enabled.setChecked(True)
    view.local_single_port.setValue(7899)
    shown.clear()
    view._on_save_local()
    drain(8.0, until=lambda: bool(shown))

    if not shown:
        print("[FAIL] 本地代理池保存无提示")
        return 1
    if int(load_config(use_cache=False).get("proxy.single_port")) != 7899:
        print("[FAIL] 本地代理池配置未落盘")
        return 1
    print(f"[OK] 本地池保存    {shown[-1][1]}，配置已落盘")

    ctx.shutdown()
    print("\n代理页交互冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
