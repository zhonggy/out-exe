"""bridge 包：GUI 与执行进程之间的桥接层。

- worker_proc.py  执行进程生命周期（从原 api/server.py 的 WorkerProcessManager 平移）
- ipc.py          行分隔 JSON 的 IPC 编解码 + QLocalServer/Socket 两端
- tasks.py        GUI 后台线程（QThreadPool）封装，避免阻塞主线程
"""

from .ipc import (
    IPC_ENV_VAR,
    decode_stream,
    encode_message,
    ipc_server_name,
)
from .worker_proc import WorkerProcessManager, build_worker_command

__all__ = [
    "IPC_ENV_VAR",
    "WorkerProcessManager",
    "build_worker_command",
    "decode_stream",
    "encode_message",
    "ipc_server_name",
]
