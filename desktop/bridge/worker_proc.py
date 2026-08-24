"""执行进程生命周期管理。

从原 ``api/server.py`` 的 ``WorkerProcessManager`` 平移，改动两处：

1. **argv 分流**：打包后 ``sys.executable`` 就是 ``OutlookAutomation.exe``，
   直接 Popen 它会再开一个 GUI。冻结模式改用 ``--exec-worker`` 标志，
   入口层在建 QApplication 之前就分流出去。
2. **IPC 地址下发**：通过环境变量把 GUI 的 IPC 地址传给子进程，
   子进程的 logger sink 据此实时回推日志。

保留的容错（都是已验证过的行为，不要动）：

- PID 文件接管：GUI 重启后仍能识别在跑的执行进程
- CTRL_BREAK 优雅停止 → 超时 kill → taskkill 兜底
- GUI 关闭默认不停止执行进程（独立性是设计目标）
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import FROZEN

from .ipc import IPC_ENV_VAR

#: 冻结模式下走执行进程分支的 argv 标志
EXEC_WORKER_FLAG = "--exec-worker"

#: 停止标志文件。执行进程主循环每 2 秒检查一次，发现则优雅收尾。
#
# 为何不用信号：Windows 上跨进程送 CTRL_BREAK 需要 AttachConsole 到目标进程组，
# 而 GUI 是 windowed 进程（无控制台）、执行进程又带 CREATE_NO_WINDOW，
# 实测信号递不到，最后只能强杀 —— 而强杀会跳过 tm.stop()，
# 导致浏览器不关、profile 不回收、任务卡在 RUNNING。
# 文件标志不依赖控制台，开发/冻结两种模式行为一致。
STOP_FLAG_NAME = "data/worker.stop"

_WORKERS_MIN = 1
_WORKERS_MAX = 16


def clamp_workers(value: Any, default: int = 1) -> int:
    """并发线程数收敛到 [1, 16]。上限防止用户手滑输 999 把机器打死。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(_WORKERS_MIN, min(n, _WORKERS_MAX))


def build_worker_command(
    cfg,
    workers: int,
    executable: Optional[str] = None,
) -> List[str]:
    """构造执行进程命令行。

    冻结模式：``OutlookAutomation.exe --exec-worker --workers N``
    开发模式：``python main.py work --workers N``
    """
    exe = executable or sys.executable
    n = str(clamp_workers(workers))
    if FROZEN:
        return [exe, EXEC_WORKER_FLAG, "--workers", n]
    return [exe, str(Path(cfg.root) / "main.py"), "work", "--workers", n]


def _pid_alive(pid: int) -> bool:
    """跨平台判断进程是否存活。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class WorkerProcessManager:
    """管理独立执行进程的生命周期。GUI 进程本身不开浏览器。"""

    def __init__(self, cfg, log=None, ipc_address: str = ""):
        self.cfg = cfg
        self.log = log
        self.ipc_address = ipc_address
        self.proc: Optional[subprocess.Popen] = None
        self.started_at: Optional[float] = None
        self.requested_workers = 0
        self._out_handle = None
        self._err_handle = None

    # ---------- 探测 ----------
    def _pid_file(self) -> Path:
        return self.cfg.resolve("data/worker.pid")

    def _stop_flag(self) -> Path:
        return self.cfg.resolve(STOP_FLAG_NAME)

    def external_alive(self) -> Tuple[bool, int]:
        """检测 GUI 之外启动的执行进程（如手动 python main.py work）。"""
        pf = self._pid_file()
        if pf.is_file():
            try:
                pid = int(pf.read_text(encoding="utf-8").strip() or 0)
            except (ValueError, OSError):
                pid = 0
            if pid and _pid_alive(pid):
                return True, pid
        return False, 0

    def alive(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        # GUI 重启后重新接管外部进程的状态显示
        ext, _pid = self.external_alive()
        return ext

    def pid(self) -> int:
        if self.proc is not None and self.proc.poll() is None:
            return self.proc.pid
        ext, pid = self.external_alive()
        return pid if ext else 0

    def uptime(self) -> float:
        if self.started_at and self.alive():
            return round(time.time() - self.started_at, 1)
        return 0.0

    # ---------- 启停 ----------
    def start(self, workers: Optional[int] = None) -> Dict[str, Any]:
        n = clamp_workers(
            workers if workers is not None else self.cfg.get("system.max_workers", 1)
        )
        if self.alive():
            self.requested_workers = n
            return {"ok": True, "already": True, "workers": n, "pid": self.pid()}

        # 清理残留的过期 pid 文件
        ext, _ext_pid = self.external_alive()
        if not ext and self._pid_file().is_file():
            try:
                self._pid_file().unlink()
            except OSError:
                pass

        log_dir = self.cfg.path_of("logger.dir", "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        self._close_handles()

        # 上次停止残留的标志会让新进程刚起来就自杀
        self._clear_stop_flag()

        try:
            self._out_handle = open(log_dir / "worker.out", "ab")
            self._err_handle = open(log_dir / "worker.err", "ab")
        except OSError as exc:
            return {"ok": False, "error": f"无法写日志文件: {exc}"}

        env = os.environ.copy()
        if self.ipc_address:
            env[IPC_ENV_VAR] = self.ipc_address
        else:
            env.pop(IPC_ENV_VAR, None)

        creationflags = 0
        if os.name == "nt":
            # CTRL_BREAK 需要独立进程组；冻结后无控制台仍可用 kill 兜底
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

        cmd = build_worker_command(self.cfg, n)
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(self.cfg.root),
                stdout=self._out_handle,
                stderr=self._err_handle,
                stdin=subprocess.DEVNULL,
                env=env,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._close_handles()
            if self.log:
                self.log.fail("worker_proc", f"启动执行进程失败: {exc}")
            return {"ok": False, "error": str(exc)}

        self.started_at = time.time()
        self.requested_workers = n
        if self.log:
            self.log.ok(
                "worker_proc", f"已拉起执行进程 PID={self.proc.pid} workers={n}"
            )
        return {"ok": True, "pid": self.proc.pid, "workers": n}

    def stop(self, timeout: float = 30.0) -> Dict[str, Any]:
        """停止执行进程。优先让它自己收尾，实在不行才强杀。

        收尾很重要：``tm.stop()`` 负责关浏览器、回收 profile、把 RUNNING 任务
        改回 QUEUED。直接 taskkill 会跳过这些，留下孤儿 chrome.exe 和卡死的任务。

        两个 PID 的区别：开发模式下 venv 的 ``python.exe`` 是启动器垫片，会再 spawn
        真正的 Python 进程，Popen 拿到的 PID 不是干活的那个。真 PID 由执行进程
        自己写在 pid 文件里。冻结模式下两者相同。
        """
        stopped: List[int] = []
        graceful = False

        _ext, real_pid = self.external_alive()
        shim_pid = (
            self.proc.pid
            if self.proc is not None and self.proc.poll() is None
            else 0
        )

        if real_pid or shim_pid:
            # ① 放停止标志，执行进程主循环两秒内会看到并走 finally 收尾
            self._write_stop_flag()
            target = real_pid or shim_pid
            graceful = self._wait_gone(target, timeout)
            if graceful:
                stopped.append(target)
            else:
                # ② 超时（正卡在验证码等待、浏览器无响应）：强杀，连子进程一起
                if self.log:
                    self.log.warn(
                        "worker_proc",
                        f"{timeout:.0f} 秒内未自行退出，转强制结束 pid={target}",
                    )
                if real_pid:
                    self._force_kill(real_pid)
                    stopped.append(real_pid)
                if shim_pid and shim_pid != real_pid:
                    self._force_kill(shim_pid)
                    stopped.append(shim_pid)

        # ③ 收割垫片进程
        if self.proc is not None:
            if self.proc.poll() is None:
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._force_kill(self.proc.pid)
                    if self.proc.pid not in stopped:
                        stopped.append(self.proc.pid)
            self.proc = None
        self._close_handles()

        # ④ 兜底：仍有外部进程存活
        still, leftover = self.external_alive()
        if still:
            self._force_kill(leftover)
            if leftover not in stopped:
                stopped.append(leftover)

        self._clear_stop_flag()
        self._cleanup_pid_file()

        if stopped and self.log:
            how = "优雅停止" if graceful else "强制结束"
            self.log.info("worker_proc", f"执行进程已{how}: {stopped}")
        self.started_at = None
        return {"ok": True, "stopped": stopped, "graceful": graceful}

    # ---------- 停止原语 ----------
    def _write_stop_flag(self) -> None:
        flag = self._stop_flag()
        try:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(str(time.time()), encoding="utf-8")
        except OSError as exc:
            if self.log:
                self.log.warn("worker_proc", f"写停止标志失败，将直接强杀: {exc}")

    def _clear_stop_flag(self) -> None:
        try:
            self._stop_flag().unlink()
        except OSError:
            pass

    def _wait_gone(self, pid: int, timeout: float) -> bool:
        deadline = time.time() + max(1.0, timeout)
        while time.time() < deadline:
            if not _pid_alive(pid):
                return True
            time.sleep(0.3)
        return not _pid_alive(pid)

    def _force_kill(self, pid: int) -> None:
        if not pid:
            return
        try:
            if os.name == "nt":
                # /T 连子进程一起杀，否则 chrome.exe 会变成孤儿进程
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=15,
                )
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception as exc:
            if self.log:
                self.log.warn("worker_proc", f"强制结束 pid={pid} 失败: {exc}")

    def _cleanup_pid_file(self) -> None:
        """进程被强杀时 pid 文件不会自己删，残留会让下次误判为“已在运行”。"""
        pf = self._pid_file()
        if not pf.is_file():
            return
        alive, _pid = self.external_alive()
        if alive:
            return
        try:
            pf.unlink()
        except OSError:
            pass

    def restart(self, workers: Optional[int] = None) -> Dict[str, Any]:
        self.stop()
        return self.start(workers)

    def _close_handles(self) -> None:
        for attr in ("_out_handle", "_err_handle"):
            handle = getattr(self, attr, None)
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
                setattr(self, attr, None)

    # ---------- 状态快照 ----------
    def live_stats(self) -> Dict[str, Any]:
        """读执行进程落盘的实时统计。IPC 断开时作为兜底。"""
        path = self.cfg.resolve("data/worker_stats.json")
        if not path.is_file():
            return {}
        try:
            import json

            return json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def snapshot(self) -> Dict[str, Any]:
        alive = self.alive()
        return {
            "running": alive,
            "pid": self.pid(),
            "workers": self.requested_workers if alive else 0,
            "uptime": self.uptime(),
            "external": self.external_alive()[0] and self.proc is None,
            "live": self.live_stats() if alive else {},
        }
