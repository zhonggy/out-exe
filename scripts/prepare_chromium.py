"""为打包准备 Chromium 内核。

把两个内核整理到 ``build/Chromium/`` 下的固定路径，供 PyInstaller/Inno Setup 打包：

    build/Chromium/fingerprint/chrome.exe            指纹内核（默认）
    build/Chromium/patchright/chromium-XXXX/...       备用内核

同时剔除不需要的组件以控制安装包体积：
- ``chromium_headless_shell-*``（约 197MB）—— 本项目 headless=false，用不到
- ``ffmpeg-*``（约 3.4MB）—— 不录屏
- ``winldd-*`` —— 仅打包工具链使用

用法：
    python scripts/prepare_chromium.py                    # 两个内核都准备
    python scripts/prepare_chromium.py --skip-fingerprint # 只要备用内核
    python scripts/prepare_chromium.py --report           # 只报告体积，不动文件
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = ROOT / "build" / "Chromium"

#: patchright install 会把内核放到这里
def playwright_cache() -> Path:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


#: 不随包发布的组件前缀
DROP_PREFIXES = ("chromium_headless_shell", "ffmpeg", "winldd")


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


def prepare_fingerprint(force: bool) -> Path | None:
    """下载并平铺指纹内核到 build/Chromium/fingerprint/。"""
    dest = BUILD_ROOT / "fingerprint"
    exe = dest / "chrome.exe"
    if exe.is_file() and not force:
        log(f"[skip] 指纹内核已就绪 {exe}")
        return exe

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "setup_browser.py"),
        "--dest",
        str(dest),
        "--flatten",
    ]
    if force:
        cmd.append("--force")
    log(f"[run ] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        log("[warn] 指纹内核准备失败，安装包将只带备用内核")
        return None
    return exe if exe.is_file() else None


def prepare_patchright(force: bool) -> Path | None:
    """从 playwright 缓存拷贝 chromium 内核，剔除不需要的组件。"""
    dest = BUILD_ROOT / "patchright"
    if dest.exists() and not force:
        found = sorted(dest.rglob("chrome.exe"))
        if found:
            log(f"[skip] 备用内核已就绪 {found[0]}")
            return found[0]

    cache = playwright_cache()
    if not cache.is_dir():
        log(f"[warn] 未找到 playwright 缓存目录 {cache}")
        log("       先执行: python -m patchright install chromium")
        return None

    entries = [
        d
        for d in sorted(cache.iterdir())
        if d.is_dir() and d.name.startswith("chromium-")
    ]
    if not entries:
        log(f"[warn] {cache} 下没有 chromium-* 目录")
        return None

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    # 取版本号最大的那个
    source = entries[-1]
    target = dest / source.name
    log(f"[copy] {source}  ->  {target}  ({human(dir_size(source))})")
    shutil.copytree(source, target)

    found = sorted(target.rglob("chrome.exe"))
    if not found:
        log(f"[warn] 拷贝完成但未找到 chrome.exe: {target}")
        return None

    dropped = [
        d.name
        for d in cache.iterdir()
        if d.is_dir() and d.name.startswith(DROP_PREFIXES)
    ]
    if dropped:
        log(f"[skip] 未随包的组件: {', '.join(sorted(dropped))}")
    return found[0]


def report() -> None:
    log("\n=== 打包内核体积 ===")
    if not BUILD_ROOT.exists():
        log(f"{BUILD_ROOT} 不存在")
        return
    total = 0
    for child in sorted(BUILD_ROOT.iterdir()):
        if child.is_dir():
            size = dir_size(child)
            total += size
            log(f"  {child.name:<14} {human(size)}")
    log(f"  {'合计':<14} {human(total)}")
    log("  （Inno Setup LZMA2 压缩后通常为此体积的 50-60%）")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--skip-fingerprint", action="store_true", help="不准备指纹内核")
    parser.add_argument("--skip-patchright", action="store_true", help="不准备备用内核")
    parser.add_argument("--force", action="store_true", help="已存在时重新准备")
    parser.add_argument("--report", action="store_true", help="只报告体积")
    args = parser.parse_args()

    if args.report:
        report()
        return 0

    BUILD_ROOT.mkdir(parents=True, exist_ok=True)

    fingerprint = None
    if not args.skip_fingerprint:
        fingerprint = prepare_fingerprint(args.force)

    patchright = None
    if not args.skip_patchright:
        patchright = prepare_patchright(args.force)

    report()

    if fingerprint is None and patchright is None:
        log("\n[FAIL] 两个内核都没准备好，打包产物将无法启动浏览器")
        return 1

    log("\n内核准备完成：")
    log(f"  指纹内核  {fingerprint or '（缺失）'}")
    log(f"  备用内核  {patchright or '（缺失）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
