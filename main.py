"""OutlookAutomation 入口。

打包后同一个 EXE 承担两个角色，由 argv 分流：
    OutlookAutomation.exe                       启动桌面 GUI
    OutlookAutomation.exe --exec-worker         执行进程（由 GUI 拉起）

开发模式下的命令行用法：
    python main.py gui                      启动桌面 GUI
    python main.py work                     手动启动执行进程
    python main.py import                   从 accounts.txt 导入账号
    python main.py run --limit 10           批量执行登录任务（跑完即退出）
    python main.py run --account a@b.com    只跑单个账号
    python main.py stats                    查看统计
    python main.py accounts --status NEW    列出账号
    python main.py tasks --limit 20         列出最近任务
    python main.py export                   导出结果 CSV
    python main.py clean --profiles         清理临时浏览器环境
    python main.py doctor                   环境自检
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

from account import describe, get_account_manager
from browser import get_profile_manager
from config import APP_VERSION, load_config
from database import get_db
from logger import get_logger, setup_from_config


def _force_utf8_streams() -> None:
    """把标准流切成 UTF-8。必须在任何 print 之前执行。

    本项目的日志与提示全是中文，而 Python 在 Windows 上给重定向的
    stdout 用的是 locale 编码。GUI 拉起执行进程时把输出重定向到
    logs/worker.out，若系统 locale 是 cp1252（英文版 Windows），
    第一条中文 print 就会抛 UnicodeEncodeError —— Worker 直接起不来。

    errors="replace" 而不是严格模式：日志里出个乱码可以接受，
    因为写不出日志而停工不可以。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


_force_utf8_streams()


def _bootstrap():
    cfg = load_config()
    cfg.ensure_dirs()
    setup_from_config(cfg)
    log = get_logger(name="main", flow="MAIN")
    db = get_db(cfg.path_of("database.path", "data/app.db"))
    return cfg, log, db


# ---------- IPC（执行进程 → GUI 实时推送）----------
#
# 旧 Web 面板的缺陷：日志缓冲在执行进程内，面板进程读自己的空缓冲，
# 所以面板上看不到任务日志。这里给 logger 挂一个 sink 把日志推给 GUI。
# 未设置 IPC 地址（如手动跑 CLI）时全部退化为空操作。
_ipc_sink = None


def _attach_ipc_sink() -> None:
    global _ipc_sink
    if _ipc_sink is not None:
        return
    try:
        from desktop.bridge.ipc import get_client
        from logger import add_sink
    except ImportError:
        return
    client = get_client()
    if client is None:
        return

    def sink(record) -> None:
        client.send({"kind": "log", **record})

    add_sink(sink)
    _ipc_sink = sink


def _detach_ipc_sink() -> None:
    global _ipc_sink
    if _ipc_sink is None:
        return
    try:
        from desktop.bridge.ipc import reset_client
        from logger import remove_sink

        remove_sink(_ipc_sink)
        reset_client()
    except ImportError:
        pass
    _ipc_sink = None


def _ipc_send(payload) -> None:
    """发一条非日志消息（统计 / 上下线）。无 IPC 时空操作。"""
    try:
        from desktop.bridge.ipc import publish

        publish(payload)
    except ImportError:
        pass


def _install_stop_handlers() -> None:
    """把停止信号转成 KeyboardInterrupt，让 cmd_work 的 finally 能跑完收尾。

    SIGBREAK/SIGTERM 默认动作是直接终止，tm.stop() 就不会执行 ——
    浏览器不关、profile 不回收、任务卡在 RUNNING。
    主路径是停止标志文件（见 desktop/bridge/worker_proc.py），
    这里多上一道，让手动 Ctrl+C / taskkill 不带 /F 时也能正常收尾。
    """

    def _raise_interrupt(_signum, _frame):
        raise KeyboardInterrupt

    import signal

    for name in ("SIGBREAK", "SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _raise_interrupt)
        except (OSError, ValueError):
            # 非主线程或平台不支持：保持默认行为
            pass


def _stop_requested(cfg) -> bool:
    """GUI 是否请求停止（通过停止标志文件）。"""
    from desktop.bridge.worker_proc import STOP_FLAG_NAME

    return cfg.resolve(STOP_FLAG_NAME).is_file()


# ---------- 子命令 ----------
def cmd_gui(args) -> int:
    """启动桌面 GUI。"""
    from desktop import run

    return run([sys.argv[0]])


def cmd_import(args) -> int:
    cfg, log, db = _bootstrap()
    am = get_account_manager(cfg, db, logger=log)
    result = am.import_file(args.file)
    if not result.get("exists"):
        print(f"账号文件不存在: {result['file']}")
        print(f"请创建该文件，每行一条：账号{cfg.get('system.account_separator', '----')}密码")
        return 1
    print(f"导入完成：{result['imported']} 条，跳过 {result['skipped']} 行")
    print(f"当前账号统计：{am.stats()['by_status']}")
    return 0


def cmd_run(args) -> int:
    cfg, log, db = _bootstrap()
    from task import get_task_manager

    tm = get_task_manager(cfg, logger=log)
    am = tm.am

    if am.stats()["total"] == 0:
        result = am.import_file()
        if not result.get("imported"):
            print("没有可执行的账号。先执行 python main.py import")
            return 1

    workers = args.workers or int(cfg.get("system.max_workers", 1))
    tm.start(workers=workers, restore=not args.no_restore)

    if args.account:
        tasks = [tm.submit(args.account, task_type=args.type)]
    else:
        tasks = tm.submit_batch(task_type=args.type, limit=args.limit)

    if not tasks:
        print("没有待处理账号（可用 python main.py accounts --status OK 查看已完成）")
        tm.stop()
        return 0

    print(f"已派发 {len(tasks)} 个任务，并发线程={workers}。Ctrl+C 可中断（任务会保留断点）")
    try:
        while True:
            idle = tm.wait_idle(timeout=5)
            snap = tm.stats()
            print(
                f"\r队列 {snap['queue']['size']} | 处理 {snap['processed']} | "
                f"成功 {snap['succeeded']} | 失败 {snap['failed']} | "
                f"浏览器 {snap['browsers']}",
                end="",
                flush=True,
            )
            if idle:
                break
    except KeyboardInterrupt:
        print("\n收到中断信号，正在停止...")
    finally:
        print()
        tm.stop()

    snap = tm.stats()
    print(f"完成：成功 {snap['succeeded']}，失败 {snap['failed']}")
    print(f"验证码统计：{snap['captcha']}")
    return 0


def cmd_work(args) -> int:
    """独立执行进程：由 GUI 以子进程方式拉起，也可手动运行。

    GUI 只负责下单与展示；本进程持有浏览器与 Worker 线程，
    GUI 关闭/卡顿不影响执行。停止方式：CTRL_BREAK（优雅）或 terminate（强杀）。
    """
    cfg, log, db = _bootstrap()
    _attach_ipc_sink()
    _install_stop_handlers()
    from task import get_task_manager

    tm = get_task_manager(cfg, logger=log)
    am = tm.am
    if am.stats()["total"] == 0:
        result = am.import_file()
        if not result.get("imported"):
            print("没有可执行的账号，先导入账号再启动")
            return 1

    workers = args.workers or int(cfg.get("system.max_workers", 1))
    tm.start(workers=workers, restore=not args.no_restore)

    # 先写 PID 文件再发 hello：GUI 收到 hello 就会认为执行进程就绪，
    # 而它接管/停止都依赖 PID 文件。反过来会有一个窗口期：
    # GUI 已知道进程跑了，却读不到 PID。
    pid_file = cfg.resolve("data/worker.pid")
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    _ipc_send({"kind": "hello", "ts": time.time(), "pid": os.getpid(), "workers": workers})
    print(f"[WORKER] 已启动 {workers} 个并发线程，PID={os.getpid()}，等待任务...")

    try:
        # 常驻：周期性从数据库补拉新任务（GUI 随时下单都能被接住）+ 统计落盘
        tick = 0
        stats_file = cfg.resolve("data/worker_stats.json")
        while True:
            time.sleep(2)
            tick += 1
            # GUI 请求停止：不靠信号（windowed 进程送不到 CTRL_BREAK）
            if _stop_requested(cfg):
                print("[WORKER] 收到停止请求，正在收尾...")
                break
            try:
                tm.queue.restore()
            except Exception:
                pass
            if tick % 3 == 0:
                try:
                    snap = tm.stats()
                    stats_file.write_text(json.dumps(snap), encoding="utf-8")
                    # IPC 推送让 GUI 秒级看到进度；落盘文件仍保留作兜底
                    _ipc_send({"kind": "stats", "ts": time.time(), "snapshot": snap})
                except Exception:
                    pass
    except KeyboardInterrupt:
        print("\n[WORKER] 收到停止信号，正在收尾...")
    finally:
        _ipc_send({"kind": "bye", "ts": time.time(), "pid": os.getpid()})
        tm.stop()
        try:
            pid_file.unlink()
        except OSError:
            pass
        snap = tm.stats()
        print(f"[WORKER] 已退出。累计成功 {snap['succeeded']}，失败 {snap['failed']}")
        _detach_ipc_sink()
    return 0


def cmd_stats(args) -> int:
    cfg, log, db = _bootstrap()
    from flow import stats_snapshot
    from proxy import get_proxy_manager

    am = get_account_manager(cfg, db, logger=log)
    stats = am.stats()
    print("=== 账号 ===")
    print(f"总数: {stats['total']}")
    for status, count in sorted(stats["by_status"].items()):
        print(f"  {describe(status):<8} {count}")
    print("\n=== 任务 ===")
    for status, count in sorted(db.count_tasks().items()):
        print(f"  {status:<12} {count}")
    print("\n=== 验证码 ===")
    print(f"  {stats_snapshot()}")
    print("\n=== 代理 ===")
    snap = get_proxy_manager(cfg.section("proxy")).snapshot()
    print(f"  模式: {'直连' if snap['direct'] else snap['type'] + '://' + snap['host']}")
    print(f"  端口数: {snap['ports']}")
    print("\n=== 环境 ===")
    pm = get_profile_manager(cfg, db)
    ps = pm.snapshot()
    print(f"  Profile 数: {ps['count']}  占用 {ps['total_mb']} MB  目录 {ps['root']}")
    return 0


def cmd_accounts(args) -> int:
    cfg, log, db = _bootstrap()
    am = get_account_manager(cfg, db, logger=log)
    items = am.list(status=args.status, limit=args.limit)
    if not items:
        print("无匹配账号")
        return 0
    print(f"{'ID':<5} {'账号':<34} {'状态':<14} 执行/失败  备注")
    for a in items:
        print(
            f"{a.id:<5} {a.account:<34} {describe(a.status):<14} "
            f"{a.run_count}/{a.fail_count}      {a.note[:40]}"
        )
    return 0


def cmd_tasks(args) -> int:
    _cfg, _log, db = _bootstrap()
    items = db.list_tasks(status=args.status, limit=args.limit)
    if not items:
        print("无任务记录")
        return 0
    print(f"{'ID':<5} {'类型':<8} {'账号':<30} {'状态':<11} {'阶段':<16} 耗时")
    for t in items:
        dur = f"{t.duration():.1f}s" if t.duration() else "-"
        print(f"{t.id:<5} {t.type:<8} {t.account:<30} {t.status:<11} {t.stage:<16} {dur}")
        if t.error:
            print(f"      └─ {t.error[:100]}")
    return 0


def cmd_export(args) -> int:
    cfg, log, db = _bootstrap()
    am = get_account_manager(cfg, db, logger=log)
    target = args.output or cfg.resolve("data/accounts_export.csv")
    path = am.export_csv(target)
    print(f"已导出: {path}")
    return 0


def cmd_clean(args) -> int:
    cfg, log, db = _bootstrap()
    pm = get_profile_manager(cfg, db, logger=log)
    if args.all_profiles:
        print("警告：这会删除全部浏览器环境（含所有登录态 Cookie），不可恢复。")
        if input("确认清空？输入 yes 继续: ").strip().lower() != "yes":
            print("已取消")
            return 1
        print(f"已清空 {pm.clear_all()} 个环境")
        return 0
    if args.profiles:
        print(f"已清理临时环境 {pm.clear_temporary()} 个")
    if args.tasks:
        print(f"已清理任务记录 {db.clear_tasks()} 条")
    if not (args.profiles or args.tasks):
        print("请指定 --profiles / --tasks / --all-profiles")
    return 0


def cmd_doctor(args) -> int:
    cfg, _log, db = _bootstrap()
    ok = True
    print(f"Python: {sys.version.split()[0]}")
    print(f"程序版本: {APP_VERSION}")
    print(f"程序目录: {cfg.root}")
    print(f"数据目录: {cfg.data_root}")
    print(f"配置文件: {cfg.source_path or '(使用内置默认值)'}")

    for module in ("patchright", "yaml"):
        try:
            __import__(module)
            print(f"[OK]   依赖 {module}")
        except ImportError:
            print(f"[FAIL] 依赖 {module} 未安装")
            ok = False
    try:
        import PySide6  # noqa: F401

        print("[OK]   依赖 PySide6")
    except ImportError:
        print("[FAIL] 依赖 PySide6 未安装（桌面 GUI 无法启动）")
        ok = False
    try:
        import apscheduler  # noqa: F401

        print("[OK]   依赖 apscheduler")
    except ImportError:
        print("[WARN] apscheduler 未安装（定时任务不可用）")

    # Playwright driver：打包版最常见的缺失项
    try:
        import patchright
        from pathlib import Path as _Path

        node = _Path(patchright.__file__).parent / "driver" / (
            "node.exe" if os.name == "nt" else "node"
        )
        if node.is_file():
            print(f"[OK]   Playwright driver: {node}")
        else:
            print(f"[FAIL] Playwright driver 缺少 {node.name}，浏览器无法启动")
            ok = False
    except Exception as exc:
        print(f"[FAIL] Playwright driver 检查失败: {exc}")
        ok = False

    # 浏览器内核
    from browser import describe_kernel

    kernel = describe_kernel(cfg)
    if kernel["error"]:
        print(f"[FAIL] 浏览器内核: {kernel['error']}")
        ok = False
    else:
        print(
            f"[OK]   浏览器内核: {kernel['active_kernel']} "
            f"{kernel['active_path'] or '(Playwright 默认查找)'}"
        )
    if not kernel["fingerprint_available"]:
        print("[WARN] 指纹内核未找到，指纹伪装不可用")

    print(f"[OK]   数据库: {db.path}")
    accounts_file = cfg.path_of("system.accounts_file", "accounts.txt")
    print(f"{'[OK]  ' if accounts_file.is_file() else '[WARN]'} 账号文件: {accounts_file}")
    print(f"[OK]   日志目录: {cfg.path_of('logger.dir')}")
    print(f"[OK]   环境目录: {cfg.path_of('profile.root')}")

    # 目录可写性：Program Files 下最容易踩
    for label, path in (
        ("数据库目录", cfg.path_of("database.path", "data/app.db").parent),
        ("日志目录", cfg.path_of("logger.dir", "logs")),
        ("Profile 目录", cfg.path_of("profile.root", "profiles")),
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".oa_write_probe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            print(f"[FAIL] {label} 不可写: {path} ({exc})")
            ok = False
    return 0 if ok else 1


# ---------- 参数 ----------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="OutlookAutomation 本地自动化框架",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"OutlookAutomation {APP_VERSION}",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("gui", help="启动桌面 GUI")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("import", help="导入账号文件")
    p.add_argument("--file", default=None, help="账号文件路径，默认 config.system.accounts_file")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("run", help="执行登录任务")
    p.add_argument("--limit", type=int, default=10, help="批量任务数量")
    p.add_argument("--account", default=None, help="只跑指定账号")
    p.add_argument("--type", default="login", help="流程类型")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--no-restore", action="store_true", help="不恢复上次未完成任务")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("work", help="独立执行进程（供 GUI 子进程调用，也可手动运行）")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--no-restore", action="store_true", help="不恢复未完成任务")
    p.set_defaults(func=cmd_work)

    p = sub.add_parser("stats", help="查看统计")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("accounts", help="列出账号")
    p.add_argument("--status", default=None)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("tasks", help="列出任务")
    p.add_argument("--status", default=None)
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("export", help="导出账号结果 CSV")
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("clean", help="清理数据")
    p.add_argument("--profiles", action="store_true", help="清理临时浏览器环境")
    p.add_argument("--all-profiles", action="store_true", help="清空全部环境（高危）")
    p.add_argument("--tasks", action="store_true", help="清空任务记录")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("doctor", help="环境自检")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Optional[list] = None) -> int:
    # 打包后同一个 EXE 承担两个角色，在这里分流：
    #   --exec-worker  → 执行进程（不加载 Qt）
    #   无子命令     → 桌面 GUI（仅冻结模式；开发模式保留打印帮助的行为）
    raw = list(sys.argv[1:] if argv is None else argv)
    from desktop.bridge.worker_proc import EXEC_WORKER_FLAG

    if EXEC_WORKER_FLAG in raw:
        workers = None
        if "--workers" in raw:
            idx = raw.index("--workers")
            if idx + 1 < len(raw):
                try:
                    workers = int(raw[idx + 1])
                except ValueError:
                    workers = None
        return cmd_work(
            argparse.Namespace(workers=workers, no_restore="--no-restore" in raw)
        )

    from config import FROZEN

    if FROZEN and not raw:
        from desktop import run as _run_gui

        return _run_gui([sys.argv[0]])

    parser = build_parser()
    args = parser.parse_args(raw)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
