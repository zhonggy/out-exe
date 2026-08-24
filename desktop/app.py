"""QApplication 引导：argv 分流 + 单实例锁 + 主窗口。

**argv 分流必须发生在建 QApplication 之前。** 打包后 GUI 和执行进程是同一个
EXE，执行进程分支不能加载 Qt（白占内存，且无控制台环境下 Qt 初始化可能失败）。
"""

from __future__ import annotations

import sys
from typing import List, Optional

from .bridge.worker_proc import EXEC_WORKER_FLAG


def _is_worker_invocation(argv: List[str]) -> bool:
    return EXEC_WORKER_FLAG in argv


def _run_worker(argv: List[str]) -> int:
    """执行进程分支：不碰 Qt，直接复用 CLI 的 work 子命令。

    正常路径下 main.main() 已经先分流了；这里是傅射防护，
    避免直接调 desktop.run([..., "--exec-worker"]) 时递归开 GUI。
    """
    import argparse

    workers = None
    if "--workers" in argv:
        idx = argv.index("--workers")
        if idx + 1 < len(argv):
            try:
                workers = int(argv[idx + 1])
            except ValueError:
                workers = None

    import main as cli

    return cli.cmd_work(
        argparse.Namespace(workers=workers, no_restore="--no-restore" in argv)
    )


def run(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if _is_worker_invocation(argv):
        return _run_worker(argv)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .single_instance import SingleInstance

    QApplication.setAttribute(Qt.AA_DontUseNativeMenuBar, False)
    app = QApplication(argv)
    app.setApplicationName("OutlookAutomation")
    app.setOrganizationName("OutlookAutomation")

    guard = SingleInstance("OutlookAutomation-gui")
    if not guard.acquire():
        QMessageBox.warning(
            None,
            "OutlookAutomation",
            "程序已在运行。\n\n"
            "重复启动会让两套 Worker 争抢同一个数据库和 Profile 目录，"
            "因此这次启动已取消。",
        )
        return 0

    from .context import AppContext
    from .main_window import MainWindow
    from .theme import apply_theme

    apply_theme(app)

    try:
        context = AppContext()
    except Exception as exc:  # 配置/数据库初始化失败：给出可读原因而不是闪退
        QMessageBox.critical(
            None,
            "启动失败",
            f"初始化失败：{exc.__class__.__name__}: {exc}",
        )
        return 1

    context.start_ipc()
    window = MainWindow(context)
    window.show()

    try:
        code = app.exec()
    finally:
        context.shutdown()
        guard.release()
    return code
