"""单实例锁。

双开的后果不是"多开个窗口"这么轻：两套 GUI 会各自拉起执行进程，
两批 Worker 争抢同一个 SQLite 和同一批 profile 目录，导致
database is locked、profile 被并发写坏、账号状态互相覆盖。

实现用 OS 级原语而非 PID 文件——PID 文件在强杀后会残留，误判成"已在运行"。
Windows 用命名互斥体，POSIX 用 flock（进程退出时内核自动释放）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional


class SingleInstance:
    """获取失败即表示已有实例在运行。"""

    def __init__(self, name: str = "OutlookAutomation-gui"):
        self.name = name
        self._handle = None
        self._lock_file = None
        self._acquired = False

    def acquire(self) -> bool:
        if self._acquired:
            return True
        if sys.platform == "win32":
            self._acquired = self._acquire_windows()
        else:
            self._acquired = self._acquire_posix()
        return self._acquired

    def _acquire_windows(self) -> bool:
        import ctypes
        from ctypes import wintypes

        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        # Local\ 前缀：锁的作用域是当前登录会话，不同用户可各自运行
        handle = kernel32.CreateMutexW(None, True, f"Local\\{self.name}")
        if not handle:
            # 拿不到句柄时不阻塞启动——宁可放行也别让程序打不开
            return True
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def _acquire_posix(self) -> bool:
        import fcntl

        path = Path(tempfile.gettempdir()) / f"{self.name}.lock"
        try:
            handle = open(path, "w")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        handle.write(str(os.getpid()))
        handle.flush()
        self._lock_file = handle
        return True

    def release(self) -> None:
        if sys.platform == "win32":
            if self._handle:
                import ctypes

                ctypes.windll.kernel32.ReleaseMutex(self._handle)
                ctypes.windll.kernel32.CloseHandle(self._handle)
                self._handle = None
        else:
            handle, self._lock_file = self._lock_file, None
            if handle is not None:
                try:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    handle.close()
                except OSError:
                    pass
        self._acquired = False

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc_info) -> None:
        self.release()
