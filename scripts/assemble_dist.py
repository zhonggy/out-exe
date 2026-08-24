"""组装 PyInstaller 产物：把 Chromium 内核和默认配置放到 EXE 旁边。

PyInstaller 只负责 Python 侧；内核作为外部资源单独拷贝，这样：
- 内核升级不用重新跑 PyInstaller
- ``config/loader.py`` 的 APP_ROOT 就是 EXE 所在目录，路径关系简单直白

用法：
    python scripts/assemble_dist.py
    python scripts/assemble_dist.py --verify   # 只校验，不拷贝
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_APP = ROOT / "dist" / "OutlookAutomation"
BUILD_CHROMIUM = ROOT / "build" / "Chromium"


def log(message: str) -> None:
    print(message, flush=True)


def dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def human(size: float) -> str:
    for unit, factor in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{int(size)} B"


def copy_chromium(force: bool) -> bool:
    """把 build/Chromium 拷到 dist/OutlookAutomation/Chromium。"""
    if not BUILD_CHROMIUM.is_dir():
        log(f"[FAIL] 缺少 {BUILD_CHROMIUM}，先执行 scripts/prepare_chromium.py")
        return False

    target = DIST_APP / "Chromium"
    if target.exists():
        if not force:
            log(f"[skip] {target} 已存在")
            return True
        shutil.rmtree(target, ignore_errors=True)

    log(f"[copy] Chromium  {human(dir_size(BUILD_CHROMIUM))}")
    shutil.copytree(BUILD_CHROMIUM, target)
    return True


def copy_default_config() -> bool:
    """默认配置放到 EXE 旁，首次启动由 loader 拷进用户数据目录。"""
    source = ROOT / "config" / "config.yaml"
    if not source.is_file():
        log(f"[FAIL] 缺少 {source}")
        return False
    target = DIST_APP / "config.yaml.default"
    shutil.copyfile(source, target)
    log(f"[copy] {target.name}")
    return True


def copy_docs() -> None:
    """随包放一份说明，用户能找到数据目录和常见问题。"""
    readme = DIST_APP / "使用说明.txt"
    readme.write_text(
        "OutlookAutomation\n"
        "=================\n\n"
        "启动：双击 OutlookAutomation.exe\n\n"
        "用户数据目录（账号、数据库、Profile、日志）：\n"
        "  %APPDATA%\\OutlookAutomation\n"
        "  卸载时默认保留，升级不会覆盖。\n\n"
        "常见问题\n"
        "--------\n"
        "1) 浏览器起不来\n"
        "   打开「浏览器」页点「检测环境」，按提示处理。\n"
        "   可在该页切换到备用内核（patchright Chromium）。\n\n"
        "2) 关闭窗口后任务还在跑\n"
        "   这是设计如此：任务在独立进程中执行。\n"
        "   要停止请在「任务管理」页点「停止执行」。\n\n"
        "3) 提示 database is locked\n"
        "   数据目录不要放在 OneDrive 等云盘同步范围内。\n\n"
        "4) 首次运行被 SmartScreen 拦截\n"
        "   本程序未做代码签名，点「更多信息」→「仍要运行」。\n",
        encoding="utf-8",
    )
    log(f"[copy] {readme.name}")


def verify(require_both_kernels: bool = True) -> bool:
    """校验产物完整性。缺任何一项打包出来都是坏的。

    ``require_both_kernels``：默认两个内核都必须在。只 WARN 不行 ——
    当初选“两个都带”就是为了指纹内核失效时能一键切备用，
    静默少一个等于发了个没有退路的包，而体积差异大到用户发现不了。
    """
    log("\n=== 产物校验 ===")
    ok = True

    exe = DIST_APP / "OutlookAutomation.exe"
    if exe.is_file():
        log(f"[OK]   主程序        {human(exe.stat().st_size)}")
    else:
        log(f"[FAIL] 主程序缺失    {exe}")
        ok = False

    internal = DIST_APP / "_internal"
    if internal.is_dir():
        log(f"[OK]   _internal     {human(dir_size(internal))}")
    else:
        log(f"[FAIL] _internal 缺失 {internal}")
        ok = False

    # Playwright driver：打包最常见的缺失项
    node_candidates = list(DIST_APP.rglob("patchright/driver/node.exe"))
    if node_candidates:
        log(f"[OK]   node driver   {node_candidates[0].relative_to(DIST_APP)}")
    else:
        log("[FAIL] patchright/driver/node.exe 缺失 —— 浏览器将无法启动")
        ok = False

    fingerprint = DIST_APP / "Chromium" / "fingerprint" / "chrome.exe"
    patchright_exes = list((DIST_APP / "Chromium" / "patchright").rglob("chrome.exe")) \
        if (DIST_APP / "Chromium" / "patchright").is_dir() else []

    level = "FAIL" if require_both_kernels else "WARN"
    if fingerprint.is_file():
        log(f"[OK]   指纹内核      {human(dir_size(fingerprint.parent))}")
    else:
        log(f"[{level}] 指纹内核缺失  指纹伪装不可用")
        if require_both_kernels:
            ok = False

    if patchright_exes:
        log(f"[OK]   备用内核      {patchright_exes[0].relative_to(DIST_APP)}")
    else:
        log(f"[{level}] 备用内核缺失  内核故障时无法切换")
        if require_both_kernels:
            ok = False

    if not fingerprint.is_file() and not patchright_exes:
        log("[FAIL] 两个内核都缺失 —— 程序无法工作")
        ok = False

    config_default = DIST_APP / "config.yaml.default"
    if config_default.is_file():
        log("[OK]   默认配置")
    else:
        log("[FAIL] config.yaml.default 缺失 —— 用户拿不到初始配置")
        ok = False

    # Qt 核心库
    qt_core = list(DIST_APP.rglob("Qt6Core.dll"))
    if qt_core:
        log("[OK]   Qt6Core.dll")
    else:
        log("[FAIL] Qt6Core.dll 缺失 —— GUI 无法启动")
        ok = False

    total = dir_size(DIST_APP)
    log(f"\n安装后占用 {human(total)}")
    log(f"安装包预计 {human(total * 0.55)} 左右（LZMA2 压缩）")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="只校验，不拷贝")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 Chromium")
    parser.add_argument(
        "--allow-single-kernel",
        action="store_true",
        help="允许只带一个内核（默认两个都必须在）",
    )
    args = parser.parse_args()

    if not DIST_APP.is_dir():
        log(f"[FAIL] 缺少 {DIST_APP}，先执行 pyinstaller --noconfirm build.spec")
        return 1

    if not args.verify:
        if not copy_chromium(args.force):
            return 1
        if not copy_default_config():
            return 1
        copy_docs()

    return 0 if verify(require_both_kernels=not args.allow_single_kernel) else 1


if __name__ == "__main__":
    sys.exit(main())
