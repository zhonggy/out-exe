"""desktop 包：PySide6 桌面 GUI。

分层：
    app.py          QApplication 引导、单实例锁、argv 分流
    main_window.py  主窗口与左侧导航
    views/          各功能页面（纯展示 + 用户操作）
    bridge/         与执行进程的桥接（子进程管理、IPC、后台任务）

约束：GUI 进程不启动浏览器。所有浏览器任务在独立执行进程中运行，
见规划文档 §13。
"""

__all__ = ["run"]


def run(argv=None) -> int:
    """启动桌面 GUI。延迟导入避免非 GUI 场景加载 PySide6。"""
    from .app import run as _run

    return _run(argv)
