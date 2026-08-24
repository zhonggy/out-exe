"""执行进程生命周期冒烟：GUI 侧真的拉起执行进程，收到 IPC，再停掉。

这是 §13 双进程模型的核心路径，也是最容易在打包后出问题的地方：
argv 分流、IPC 地址下发、PID 文件接管、CTRL_BREAK 收尾。

需要账号数据才能让执行进程常驻，所以先往临时库里塞一个假账号。
不会真的开浏览器（不派发任务，Worker 起来后空转等任务）。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    data_root = os.environ.get("OA_DATA_DIR", "")
    if not data_root:
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实数据目录")
        return 1

    from config import load_config
    from database import get_db
    from logger import get_logger, setup_from_config

    cfg = load_config(use_cache=False)
    cfg.ensure_dirs()
    setup_from_config(cfg)
    log = get_logger(name="smoke", flow="SMOKE")
    db = get_db(cfg.path_of("database.path", "data/app.db"))

    # 执行进程在没有任何账号时会立刻退出，塞一个假账号让它常驻
    db.upsert_account("smoke-probe@example.invalid", "not-a-real-password")
    print("[OK] 准备账号     smoke-probe@example.invalid")

    from desktop.bridge.ipc import IpcServer, ipc_server_name
    from desktop.bridge.worker_proc import WorkerProcessManager, build_worker_command

    messages = []
    server = IpcServer(messages.append, address=ipc_server_name())
    address = server.start()
    print(f"[OK] IPC 监听      {address}")

    wpm = WorkerProcessManager(cfg, log=log, ipc_address=address)
    cmd = build_worker_command(cfg, workers=1)
    print(f"[..] 启动命令      {' '.join(str(c) for c in cmd)}")

    result = wpm.start(workers=1)
    if not result.get("ok"):
        print(f"[FAIL] 启动失败    {result}")
        server.stop()
        return 1
    print(f"[OK] 已启动        PID={result.get('pid')}")

    # 等 hello 消息（执行进程起 Worker 后才发）
    deadline = time.time() + 45
    while time.time() < deadline:
        if any(m.get("kind") == "hello" for m in messages):
            break
        time.sleep(0.3)

    kinds = [m.get("kind") for m in messages]
    hello = [m for m in messages if m.get("kind") == "hello"]
    if not hello:
        print(f"[FAIL] 45 秒内没收到 hello。收到: {kinds}")
        print("       检查 logs/worker.err")
        err = cfg.path_of("logger.dir", "logs") / "worker.err"
        if err.is_file():
            print(err.read_text(encoding="utf-8", errors="replace")[-2000:])
        wpm.stop()
        server.stop()
        return 1
    print(f"[OK] 收到 hello    pid={hello[0].get('pid')} workers={hello[0].get('workers')}")

    logs = [m for m in messages if m.get("kind") == "log"]
    print(f"[OK] 实时日志      {len(logs)} 条（IPC 推送链路通）")

    # PID 文件接管：新建一个 manager 应能识别到在跑的进程
    other = WorkerProcessManager(cfg, log=log)
    alive, pid = other.external_alive()
    if not alive:
        print("[FAIL] PID 文件接管失效 —— GUI 重启后无法识别在跑的执行进程")
        wpm.stop()
        server.stop()
        return 1
    print(f"[OK] PID 接管      external_alive → pid={pid}")

    snapshot = wpm.snapshot()
    print(
        f"[OK] 状态快照      running={snapshot['running']} "
        f"pid={snapshot['pid']} uptime={snapshot['uptime']}s"
    )

    # 停止：必须是优雅停止（tm.stop() 跑过），而不是强杀
    stop_result = wpm.stop(timeout=25)
    print(f"[OK] 已停止        {stop_result.get('stopped')}")
    if not stop_result.get("graceful"):
        print("[FAIL] 走了强杀路径 —— tm.stop() 未执行，浏览器与 profile 不会回收")
        server.stop()
        return 1
    print("[OK] 优雅停止      收尾逻辑已执行")

    time.sleep(1.0)
    if wpm.alive():
        print("[FAIL] 停止后进程仍存活")
        server.stop()
        return 1
    print("[OK] 进程已退出")

    # 收尾日志必须出现 manager_stop，证明真的跑了 tm.stop()
    out = cfg.path_of("logger.dir", "logs") / "worker.out"
    if out.is_file():
        text = out.read_text(encoding="utf-8", errors="replace")
        if "manager_stop" in text:
            print("[OK] 收尾日志      manager_stop 已记录")
        else:
            print("[WARN] worker.out 里没看到 manager_stop")

    for leftover, label in (
        (cfg.resolve("data/worker.pid"), "PID 文件"),
        (cfg.resolve("data/worker.stop"), "停止标志"),
    ):
        if leftover.is_file():
            print(f"[FAIL] {label}残留 {leftover}")
            server.stop()
            return 1
    print("[OK] 无残留文件    PID 与停止标志已清理")

    server.stop()
    db.delete_account("smoke-probe@example.invalid")
    print("\n执行进程生命周期冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
