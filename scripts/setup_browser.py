"""下载并部署 fingerprint-chromium 指纹内核。

用法：
    python scripts/setup_browser.py                      # 锁定版本，开发模式
    python scripts/setup_browser.py --tag latest         # 跟随上游最新（不可重现）
    python scripts/setup_browser.py --dest build/Chromium/fingerprint --flatten
    python scripts/setup_browser.py --force              # 已存在也重新下载

默认锁定 PINNED_TAG 而不是 latest：``latest`` 会跟着上游变，CI 构建不可重现，
升级内核应该是一次显式的代码提交。

不再写 config.yaml。内核路径由 ``browser/kernel.py`` 自动定位：
配置里写 ``fingerprint`` 关键字即可，内核升级换目录名也不用改配置。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPO = "adryfish/fingerprint-chromium"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases"

#: 已验证可用的版本。升级时改这里并实测，不要依赖 latest。
#
# 注意：这是 **release tag**，不带 -1.1 后缀。上游的 tag 是纯版本号
# （如 148.0.7778.215），而资产文件名里多一个构建号
# （ungoogled-chromium_148.0.7778.215-1.1_windows_x64.zip）。两者别混。
PINNED_TAG = "148.0.7778.215"

DEST_ROOT = ROOT / "browsers" / "fingerprint-chromium"

#: 国内网络常见的本地代理端口
PROXY_PORTS = ("7897", "7890", "10809", "1080")


def log(message: str) -> None:
    print(message, flush=True)


def detect_proxy() -> str:
    """探测本机代理。CI 上探不到就直连，不影响流程。"""
    if os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY"):
        return ""
    import socket

    for port in PROXY_PORTS:
        try:
            sock = socket.create_connection(("127.0.0.1", int(port)), timeout=1)
            sock.close()
            return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return ""


def api_request(url: str) -> dict | list:
    """带 UA 与可选 token 的 GitHub API 请求。

    匿名请求限 60 次/小时/IP，CI 上很容易撞限流，所以支持 GITHUB_TOKEN。
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OutlookAutomation-setup",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise SystemExit(
                "GitHub API 限流（403）。设置 GITHUB_TOKEN 环境变量后重试。"
            ) from exc
        raise SystemExit(f"GitHub API 请求失败 {exc.code}: {url}") from exc


def find_release(tag: str) -> dict:
    if tag == "latest":
        log("查询最新版本（不可重现，仅建议手动使用）…")
        return api_request(f"{RELEASES_API}/latest")

    log(f"查询锁定版本 {tag} …")
    for candidate in (tag, f"v{tag}"):
        try:
            return api_request(f"{RELEASES_API}/tags/{candidate}")
        except SystemExit:
            continue

    # 精确匹配失败：遍历列表双向模糊匹配。
    # 双向是必要的 —— 传入的可能是带构建号的资产版本
    # （148.0.7778.215-1.1），而上游 tag 是纯版本号（148.0.7778.215）。
    log(f"tag {tag} 精确匹配失败，尝试模糊匹配…")
    releases = api_request(f"{RELEASES_API}?per_page=100")
    if isinstance(releases, list):
        for release in releases:
            name = str(release.get("tag_name", ""))
            if not name:
                continue
            if tag == name or tag in name or name in tag:
                log(f"模糊匹配到 tag={name}")
                return release
        available = ", ".join(str(r.get("tag_name")) for r in releases[:10])
        raise SystemExit(f"未找到版本 {tag}。上游最近的 tag: {available}")
    raise SystemExit(f"未找到版本 {tag}，且无法列出可用版本")


def pick_asset(assets: list) -> dict:
    """选 Windows x64 的 zip 资产。"""
    for asset in assets:
        name = str(asset.get("name", "")).lower()
        if "windows" in name and "x64" in name and name.endswith(".zip"):
            return asset
    names = ", ".join(str(a.get("name")) for a in assets)
    raise SystemExit(f"未找到 Windows x64 zip 资产。可用资产: {names}")


def download(url: str, dest: Path) -> None:
    last_pct = -1

    def report(blocks: int, block_size: int, total: int) -> None:
        nonlocal last_pct
        if total <= 0:
            return
        pct = min(100, blocks * block_size * 100 // total)
        # CI 日志里每 5% 打一行就够，避免刷屏
        if pct >= last_pct + 5:
            last_pct = pct
            log(f"  下载 {pct}%")

    log(f"下载 {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest, reporthook=report)
    log(f"已下载 {dest} ({dest.stat().st_size // 1048576} MB)")


def extract(zip_path: Path, dest_root: Path, flatten: bool) -> Path:
    """解压并返回 chrome.exe 路径。

    flatten=True 时把内核文件直接放到 dest_root 下（去掉带版本号的中间目录），
    这样打包产物里的路径是固定的 ``Chromium/fingerprint/chrome.exe``。
    """
    import zipfile

    staging = dest_root.parent / f"_extract_{dest_root.name}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    log(f"解压到 {staging}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging)

    exes = sorted(staging.rglob("chrome.exe"))
    if not exes:
        shutil.rmtree(staging, ignore_errors=True)
        raise SystemExit("解压完成但没找到 chrome.exe")
    exe = exes[0]

    dest_root.mkdir(parents=True, exist_ok=True)
    if flatten:
        source_dir = exe.parent
        for item in source_dir.iterdir():
            target = dest_root / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))
        result = dest_root / "chrome.exe"
    else:
        version_dir = exe.parent
        target = dest_root / version_dir.name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(version_dir), str(target))
        result = target / "chrome.exe"

    shutil.rmtree(staging, ignore_errors=True)
    if not result.is_file():
        raise SystemExit(f"部署后未找到 {result}")
    return result


def existing_kernel(dest_root: Path) -> Path | None:
    if not dest_root.exists():
        return None
    direct = dest_root / "chrome.exe"
    if direct.is_file():
        return direct
    found = sorted(dest_root.rglob("chrome.exe"))
    return found[0] if found else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tag",
        default=PINNED_TAG,
        help=f"要下载的版本，默认锁定 {PINNED_TAG}；传 latest 跟随上游",
    )
    parser.add_argument(
        "--dest",
        default=str(DEST_ROOT),
        help="部署目录，默认 browsers/fingerprint-chromium",
    )
    parser.add_argument(
        "--flatten",
        action="store_true",
        help="把内核文件直接放到 dest 下（打包时用，路径固定不带版本号）",
    )
    parser.add_argument("--force", action="store_true", help="已存在时强制重新下载")
    args = parser.parse_args()

    dest_root = Path(args.dest)
    if not dest_root.is_absolute():
        dest_root = ROOT / dest_root

    found = existing_kernel(dest_root)
    if found and not args.force:
        log(f"指纹内核已存在: {found}")
        log("如需重新下载请加 --force")
        return 0

    proxy = detect_proxy()
    if proxy:
        log(f"使用本机代理 {proxy}")
        os.environ["http_proxy"] = proxy
        os.environ["https_proxy"] = proxy

    release = find_release(args.tag)
    tag = release.get("tag_name", "?")
    asset = pick_asset(release.get("assets", []))
    log(f"版本 {tag}: {asset['name']} ({asset.get('size', 0) // 1048576} MB)")

    tmp = dest_root.parent / f"_download_{dest_root.name}.zip"
    try:
        download(asset["browser_download_url"], tmp)
        exe = extract(tmp, dest_root, flatten=args.flatten)
    finally:
        if tmp.exists():
            tmp.unlink()

    log(f"\n完成。指纹内核: {exe}")
    log("配置中 browser.executable_path 写 \"fingerprint\" 即可自动定位。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
