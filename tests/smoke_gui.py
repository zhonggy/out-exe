"""GUI 冒烟测试：offscreen 模式下建窗口、走一遍所有页面。

不是单元测试（依赖 PySide6 + 显示后端），CI 里单独跑，用 QT_QPA_PLATFORM=offscreen。
验证目标：各页构造不炸、切页 refresh 不炸、快照字段齐全、IPC 能起停。
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

import _console  # noqa: E402,F401  导入即把标准流切成 UTF-8

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    sys.argv = ["main.py"]

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    from desktop.theme import apply_theme

    apply_theme(app)

    from desktop.context import AppContext

    ctx = AppContext()
    print(f"[OK] context      data_root={ctx.cfg.data_root}")

    address = ctx.start_ipc()
    print(f"[OK] ipc          {address or '(未启动)'}")

    from desktop.main_window import MainWindow

    window = MainWindow(ctx)
    window.show()
    print(f"[OK] main window  pages={window.stack.count()}")

    for index in range(window.stack.count()):
        window.nav.setCurrentRow(index)
        app.processEvents()
    print("[OK] nav walk     所有页面切换正常")

    snapshot = ctx.stats_snapshot()
    required = {
        "worker",
        "tasks",
        "accounts",
        "queue",
        "browsers",
        "captcha",
        "db",
        "proxy",
        "profiles",
        "kernel",
        "ipc_fresh",
    }
    missing = required - set(snapshot)
    if missing:
        print(f"[FAIL] snapshot   缺少字段: {sorted(missing)}")
        return 1
    kernel = snapshot["kernel"]
    print(
        f"[OK] snapshot     kernel={kernel['active_kernel']} "
        f"fingerprint={kernel['fingerprint_available']}"
    )

    # 让线程池回调跑完（各页 refresh 都是异步的）
    for _ in range(40):
        app.processEvents()
        time.sleep(0.02)
    print("[OK] async        后台任务回调已排空")

    # 后台任务回调必须真的到达。
    # 曾经的 bug：QThreadPool 默认接管并删除 QRunnable，Python 侧不保留
    # 引用时任务对象被 GC，它持有的 signals 一起销毁，已排队的信号全部丢弃。
    # 表现是：业务函数确实执行了（配置真的保存了），但没有成功提示、
    # 按钮永久停在禁用状态。这个断言盯住它。
    from desktop.bridge.tasks import inflight_count, run_async, wait_for_idle

    got_result = []
    got_done = []
    got_error = []

    def probe(tag):
        return f"probe-{tag}"

    BATCH = 25
    for index in range(BATCH):
        run_async(
            probe,
            index,
            on_result=got_result.append,
            on_error=got_error.append,
            on_done=lambda: got_done.append(1),
        )

    deadline = time.time() + 15
    while time.time() < deadline and len(got_done) < BATCH:
        app.processEvents()
        time.sleep(0.02)
    wait_for_idle(5000)
    for _ in range(60):
        app.processEvents()
        time.sleep(0.01)

    if len(got_result) != BATCH or len(got_done) != BATCH:
        print(
            f"[FAIL] 后台回调丢失  on_result={len(got_result)}/{BATCH} "
            f"on_done={len(got_done)}/{BATCH} on_error={len(got_error)}"
        )
        print("       后果：所有“保存/测试/删除”操作都不会弹提示，按钮也不会恢复")
        return 1
    print(f"[OK] 后台回调      {BATCH}/{BATCH} 全部到达（不受 GC 影响）")

    if inflight_count() != 0:
        print(f"[FAIL] 任务登记表泄漏  残留 {inflight_count()} 个")
        return 1
    print("[OK] 登记表清空    无引用泄漏")

    # 异常路径同样不能丢：否则失败时静默，比报错更难排查。
    # 下面会打印一个预期内的 RuntimeError 堆栈，属于正常现象。
    print("[..] 异常回调      下方堆栈是测试故意触发的，非真实错误")
    err_seen = []
    done_seen = []

    def boom():
        raise RuntimeError("intentional")

    run_async(
        boom,
        on_error=err_seen.append,
        on_done=lambda: done_seen.append(1),
    )
    deadline = time.time() + 10
    while time.time() < deadline and not done_seen:
        app.processEvents()
        time.sleep(0.02)
    wait_for_idle(3000)
    for _ in range(30):
        app.processEvents()
        time.sleep(0.01)

    if not err_seen or not done_seen:
        print(f"[FAIL] 异常回调丢失  on_error={len(err_seen)} on_done={len(done_seen)}")
        return 1
    print(f"[OK] 异常回调      {err_seen[0][:40]}")

    # 执行进程命令行构造
    from desktop.bridge.worker_proc import build_worker_command

    cmd = build_worker_command(ctx.cfg, workers=2)
    print(f"[OK] worker cmd   {' '.join(str(c) for c in cmd)}")

    window.close()
    ctx.shutdown()
    print("[OK] shutdown     IPC 已停止")
    print("\nGUI 冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
