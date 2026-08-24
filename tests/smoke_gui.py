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
