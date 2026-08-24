"""IPC 端到端冒烟：GUI 侧起 IpcServer，子进程写日志，验证消息真的送到。

单元测试只覆盖了编解码（decode_stream/encode_message），这里验证跨进程真链路：
命名管道能连上、logger sink 能推、分帧不丢、断开能收尾。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import _console  # noqa: E402,F401  导入即把标准流切成 UTF-8

import time  # noqa: E402

CHILD_CODE = r"""
import os, sys, time
sys.path.insert(0, r"{root}")
from desktop.bridge.ipc import get_client, publish, reset_client
from logger import add_sink, get_logger, setup_logging

setup_logging(log_dir=r"{logs}", console=False, to_file=False)
client = get_client()
if client is None:
    print("CHILD: no ipc client", flush=True)
    raise SystemExit(2)

def sink(record):
    client.send({{"kind": "log", **record}})

add_sink(sink)
log = get_logger(name="child", flow="WORKER")

publish({{"kind": "hello", "ts": time.time(), "pid": os.getpid(), "workers": 2}})
log.info("browser_launch", "启动浏览器 kernel=fingerprint")
log.warn("captcha", "验证码需要重试")
log.error("multi", "第一行\n第二行\n第三行")
publish({{"kind": "stats", "ts": time.time(), "snapshot": {{"processed": 7, "succeeded": 5}}}})
publish({{"kind": "bye", "ts": time.time(), "pid": os.getpid()}})
time.sleep(1.0)
reset_client()
print("CHILD: done", flush=True)
"""


def main() -> int:
    from desktop.bridge.ipc import IPC_ENV_VAR, IpcServer, ipc_server_name

    received = []
    server = IpcServer(received.append, address=ipc_server_name())
    address = server.start()
    print(f"[..] server        {address}")

    logs_dir = ROOT / "data" / "_ipc_smoke_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    code = CHILD_CODE.format(root=str(ROOT), logs=str(logs_dir))
    env = dict(**__import__("os").environ)
    env[IPC_ENV_VAR] = address

    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(ROOT),
    )
    print(f"[..] child exit    {proc.returncode}  {proc.stdout.strip()}")
    if proc.returncode != 0:
        print(f"[FAIL] child stderr:\n{proc.stderr[-1500:]}")
        server.stop()
        return 1

    # 给 server 线程时间收尾
    for _ in range(30):
        if any(m.get("kind") == "bye" for m in received):
            break
        time.sleep(0.1)
    server.stop()

    kinds = [m.get("kind") for m in received]
    print(f"[..] received      {len(received)} 条: {kinds}")

    failures = []
    if "hello" not in kinds:
        failures.append("缺少 hello")
    if "bye" not in kinds:
        failures.append("缺少 bye")
    if "stats" not in kinds:
        failures.append("缺少 stats")

    logs = [m for m in received if m.get("kind") == "log"]
    if len(logs) < 3:
        failures.append(f"日志条数不足: {len(logs)}")

    # 换行不能破坏分帧：多行消息必须完整送达为一条
    multi = [m for m in logs if "第三行" in str(m.get("message", ""))]
    if not multi:
        failures.append("含换行的日志未完整送达（分帧被破坏）")
    elif multi[0]["message"].count("\n") != 2:
        failures.append(f"换行数不对: {multi[0]['message']!r}")

    stats = [m for m in received if m.get("kind") == "stats"]
    if stats and stats[0].get("snapshot", {}).get("processed") != 7:
        failures.append("stats 内容错误")

    # 中文不能乱码（encode_message 用 ensure_ascii，解码回来应还原）
    zh = [m for m in logs if "启动浏览器" in str(m.get("message", ""))]
    if not zh:
        failures.append("中文日志未正确还原")

    if failures:
        for item in failures:
            print(f"[FAIL] {item}")
        return 1

    print("[OK] hello/bye/stats 齐全")
    print(f"[OK] 日志 {len(logs)} 条，中文与换行完整")
    print("\nIPC 端到端冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
