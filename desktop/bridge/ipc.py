"""GUI ↔ 执行进程 IPC：行分隔 JSON 消息。

设计要点：

- **不开 TCP 端口。** Windows 用命名管道（``AF_PIPE``），POSIX 用 Unix domain
  socket，都局限于当前用户会话，不产生网络监听面。
- **传输层用 stdlib**（``multiprocessing.connection``），不依赖 PySide6。
  这样执行进程侧完全不需要加载 Qt，GUI 侧才把消息转成 Qt 信号。
- **推送永不阻塞业务。** 客户端把消息丢进有界队列由后台线程发送，
  队列满或连接断开时直接丢弃并计数，绝不让日志调用点卡住 Worker。
- **IPC 只是展示层加速。** 断开后 GUI 退回轮询 ``worker_stats.json`` +
  读日志文件，任务本身的正确性只依赖 SQLite。

消息形状（``kind`` 决定其余字段）::

    {"kind": "log",   "ts":…, "level":…, "flow":…, "stage":…, "message":…}
    {"kind": "stats", "ts":…, "snapshot": {...}}
    {"kind": "hello", "ts":…, "pid":…, "workers":…}
    {"kind": "bye",   "ts":…, "pid":…}
"""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

#: 执行进程通过该环境变量拿到要连接的 IPC 地址
IPC_ENV_VAR = "OA_IPC_ADDRESS"

#: 客户端发送队列上限。满了丢弃最旧消息——宁可丢日志，不能拖慢 Worker
_SEND_QUEUE_MAX = 2000

#: 单条消息长度上限，避免异常长的 message 把管道堵死
_MAX_MESSAGE_BYTES = 64 * 1024


# ---------------------------------------------------------------- 编解码
def encode_message(payload: Dict[str, Any]) -> bytes:
    """字典 → 一行 JSON（含结尾换行）。

    ``ensure_ascii=True`` 让消息内的换行/中文都被转义，因此换行只会出现在
    帧边界，分帧不会被日志内容破坏。
    """
    text = json.dumps(payload, ensure_ascii=True, default=str)
    data = text.encode("ascii", errors="replace")
    if len(data) > _MAX_MESSAGE_BYTES:
        data = data[:_MAX_MESSAGE_BYTES]
        # 截断后 JSON 已不合法，退化成一条可解析的告警消息
        data = json.dumps(
            {"kind": "log", "level": "WARN", "stage": "ipc",
             "message": "消息过长已丢弃"},
            ensure_ascii=True,
        ).encode("ascii")
    return data + b"\n"


def decode_stream(buffer: bytes) -> Tuple[List[Dict[str, Any]], bytes]:
    """解析字节流中的完整消息。

    返回 ``(消息列表, 剩余未成帧字节)``。坏行被丢弃，不影响后续消息。
    """
    messages: List[Dict[str, Any]] = []
    while True:
        idx = buffer.find(b"\n")
        if idx < 0:
            break
        line, buffer = buffer[:idx], buffer[idx + 1:]
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(obj, dict):
            messages.append(obj)
    return messages, buffer


# ---------------------------------------------------------------- 地址
def ipc_server_name(pid: Optional[int] = None) -> str:
    """生成本进程专属的 IPC 地址。

    带 PID 是为了同一台机器上多个用户会话/多个副本不会互相串台。
    """
    ident = f"OutlookAutomation-ipc-{pid or os.getpid()}"
    if sys.platform == "win32":
        return rf"\\.\pipe\{ident}"
    return str(Path(tempfile.gettempdir()) / f"{ident}.sock")


def _family_for(address: str) -> str:
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


# ---------------------------------------------------------------- 客户端
class IpcClient:
    """执行进程侧：后台线程发送，调用点永不阻塞。"""

    def __init__(self, address: str = "", connect_timeout: float = 3.0):
        self.address = address or os.environ.get(IPC_ENV_VAR, "")
        self._queue: "queue.Queue[Optional[bytes]]" = queue.Queue(_SEND_QUEUE_MAX)
        self._conn = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._connect_timeout = connect_timeout
        self.dropped = 0
        self.sent = 0

    # ---------- 生命周期 ----------
    @property
    def enabled(self) -> bool:
        return bool(self.address)

    def start(self) -> bool:
        """连接并启动发送线程。失败返回 False（调用方应静默降级）。"""
        if not self.enabled or self._thread is not None:
            return False
        if not self._connect():
            return False
        self._thread = threading.Thread(
            target=self._run, name="ipc-client", daemon=True
        )
        self._thread.start()
        return True

    def _connect(self) -> bool:
        from multiprocessing.connection import Client

        deadline = time.time() + self._connect_timeout
        while time.time() < deadline and not self._stop.is_set():
            try:
                self._conn = Client(self.address, family=_family_for(self.address))
                return True
            except (OSError, ValueError):
                time.sleep(0.2)
        return False

    def close(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._close_conn()

    def _close_conn(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    # ---------- 发送 ----------
    def send(self, payload: Dict[str, Any]) -> None:
        """入队一条消息。队列满则丢弃最旧的，保证不阻塞。"""
        if self._conn is None and self._thread is None:
            return
        data = encode_message(payload)
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            self.dropped += 1
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(data)
            except (queue.Empty, queue.Full):
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            conn = self._conn
            if conn is None:
                self.dropped += 1
                continue
            try:
                conn.send_bytes(item)
                self.sent += 1
            except (OSError, EOFError, ValueError):
                # GUI 已关闭：停止发送，剩余消息丢弃，业务不受影响
                self._close_conn()
                break
        self._close_conn()


_client_lock = threading.Lock()
_client: Optional[IpcClient] = None


def get_client() -> Optional[IpcClient]:
    """进程级单例。未设置地址（例如 CLI 直接运行）时返回 None。"""
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        address = os.environ.get(IPC_ENV_VAR, "")
        if not address:
            return None
        client = IpcClient(address)
        if not client.start():
            return None
        _client = client
        return _client


def reset_client() -> None:
    global _client
    with _client_lock:
        client, _client = _client, None
    if client is not None:
        client.close()


def publish(payload: Dict[str, Any]) -> None:
    """给 logger sink 用的便捷入口。无 IPC 时是空操作。"""
    client = get_client()
    if client is not None:
        client.send(payload)


# ---------------------------------------------------------------- 服务端
class IpcServer:
    """GUI 侧：后台线程 accept + 读取，逐条回调。

    回调在**后台线程**执行，Qt 侧必须用信号切回主线程再动 UI。
    """

    def __init__(self, on_message: Callable[[Dict[str, Any]], None], address: str = ""):
        self.address = address or ipc_server_name()
        self._on_message = on_message
        self._listener = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._conn = None
        self.received = 0

    def start(self) -> str:
        """开始监听，返回实际地址（写进子进程环境变量）。"""
        from multiprocessing.connection import Listener

        if self._thread is not None:
            return self.address
        if sys.platform != "win32":
            # 残留的 socket 文件会让 bind 失败
            try:
                Path(self.address).unlink()
            except OSError:
                pass
        self._listener = Listener(self.address, family=_family_for(self.address))
        self._thread = threading.Thread(
            target=self._run, name="ipc-server", daemon=True
        )
        self._thread.start()
        return self.address

    def stop(self) -> None:
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None
        if sys.platform != "win32":
            try:
                Path(self.address).unlink()
            except OSError:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                conn = listener.accept()
            except (OSError, EOFError, ValueError):
                if self._stop.is_set():
                    return
                time.sleep(0.2)
                continue
            self._conn = conn
            self._serve(conn)
            self._conn = None

    def _serve(self, conn) -> None:
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv_bytes()
            except (EOFError, OSError, ValueError):
                break
            if not chunk:
                continue
            buffer += chunk
            messages, buffer = decode_stream(buffer)
            for message in messages:
                self.received += 1
                try:
                    self._on_message(message)
                except Exception:
                    # 回调异常不能让 IPC 线程死掉
                    pass
        try:
            conn.close()
        except OSError:
            pass
