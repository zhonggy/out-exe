"""PyInstaller runtime hook：修补 windowed 模式下缺失的标准流。

``console=False`` 打包时 PyInstaller 把 ``sys.stdout`` / ``sys.stderr`` / ``sys.stdin``
设为 ``None``（无控制台可写）。本项目有两处会因此直接崩：

- ``logger/logger.py`` 的 ``_SafeStreamHandler(stream=sys.stdout)``
- ``main.py`` 的 ``cmd_work`` 等大量 ``print()``

这个 hook 在任何业务代码之前执行，把 None 的流替换掉：

- 执行进程（由 GUI 用 Popen 启动，已重定向到 ``logs/worker.out``）：
  如果句柄可用就保持不动，让日志照常落到文件
- GUI 进程：重定向到 ``<数据目录>/logs/gui.out``，不静默丢弃 ——
  真出问题时用户能把这个文件发出来

写文件失败（磁盘满、权限不足）时退回黑洞，保证程序不会因为写不了日志而起不来。
"""

import io
import os
import sys


class _NullStream(io.TextIOBase):
    """最后的兜底：吞掉一切写入，但保持文件对象接口完整。"""

    def write(self, text):  # noqa: D102
        return len(text) if text else 0

    def flush(self):  # noqa: D102
        return None

    def isatty(self):  # noqa: D102
        return False

    def readable(self):  # noqa: D102
        return False

    def writable(self):  # noqa: D102
        return True

    def fileno(self):  # noqa: D102
        raise OSError("no fileno in windowed mode")


def _data_root():
    """与 config/loader.py 的 DATA_ROOT 逻辑保持一致（此处不能 import 业务模块）。"""
    override = os.environ.get("OA_DATA_DIR", "").strip()
    if override:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(override)))
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if base:
        return os.path.join(base, "OutlookAutomation")
    return os.path.join(os.path.expanduser("~"), ".outlookautomation")


def _open_fallback():
    """打开 GUI 进程的标准流落地文件。"""
    try:
        log_dir = os.path.join(_data_root(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "gui.out")
        # 行缓冲：崩溃时已写内容不至于全丢
        return open(path, "a", encoding="utf-8", buffering=1, errors="replace")
    except OSError:
        return _NullStream()


_fallback = None


def _ensure(name):
    global _fallback
    if getattr(sys, name, None) is not None:
        return
    if _fallback is None:
        _fallback = _open_fallback()
    setattr(sys, name, _fallback)


_ensure("stdout")
_ensure("stderr")

if getattr(sys, "stdin", None) is None:
    # input() 在 GUI 下不该被调用；给个空流让它抛 EOFError 而不是 AttributeError
    sys.stdin = io.StringIO("")
