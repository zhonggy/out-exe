"""一键下载并部署 fingerprint-chromium 指纹内核。

用法：
    python scripts/setup_browser.py            # 默认版本
    python scripts/setup_browser.py --force    # 已存在也重新下载

从 fingerprint-chromium 官方 Releases 下载 Windows x64 构建，
解压到 browsers/fingerprint-chromium/<目录>/，并把路径写回 config/config.yaml。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_API = "https://api.github.com/repos/adryfish/fingerprint-chromium/releases/latest"
DEST_ROOT = ROOT / "browsers" / "fingerprint-chromium"


def pick_asset(assets: list) -> dict:
    """选 Windows x64 的 zip 资产。"""
    for a in assets:
        name = a.get("name", "")
        if "windows" in name.lower() and "x64" in name.lower() and name.endswith(".zip"):
            return a
    raise SystemExit("未找到 Windows x64 的 zip 构建资产")


def download(url: str, dest: Path) -> None:
    def report(blocks, bs, total):
        if total > 0:
            pct = min(100, blocks * bs * 100 // total)
            print(f"\r下载中... {pct}%", end="", flush=True)

    print(f"下载 {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 走系统代理（若设置了 http_proxy/https_proxy 环境变量则自动生效）
    urllib.request.urlretrieve(url, dest, reporthook=report)
    print(f"\n已下载到 {dest}")


def extract(zip_path: Path, dest_root: Path) -> Path:
    import zipfile

    print(f"解压到 {dest_root}")
    dest_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_root)
    exes = list(dest_root.rglob("chrome.exe"))
    if not exes:
        raise SystemExit("解压完成但没找到 chrome.exe")
    return exes[0]


def write_config(exe_rel: str) -> None:
    cfg_path = ROOT / "config" / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    marker = 'executable_path:'
    lines = []
    replaced = False
    for line in text.splitlines():
        if not replaced and line.strip().startswith(marker):
            indent = line[: len(line) - len(line.lstrip())]
            lines.append(f'{indent}"{exe_rel.replace(chr(92), "/")}"')
            replaced = True
        else:
            lines.append(line)
    cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入配置 executable_path = {exe_rel}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="已存在时强制重新下载")
    args = parser.parse_args()

    exe_candidates = list(DEST_ROOT.rglob("chrome.exe")) if DEST_ROOT.exists() else []
    if exe_candidates and not args.force:
        print(f"指纹内核已存在: {exe_candidates[0]}")
        print("如需重新下载请加 --force")
        return 0

    proxy = None
    for port in ("7897", "7890", "10809"):
        try:
            import socket
            s = socket.create_connection(("127.0.0.1", int(port)), timeout=1)
            s.close()
            proxy = f"http://127.0.0.1:{port}"
            print(f"检测到本机代理端口 {port}，将使用代理下载")
            break
        except OSError:
            continue

    if proxy:
        import os
        os.environ["http_proxy"] = proxy
        os.environ["https_proxy"] = proxy

    print("查询最新版本...")
    req = urllib.request.Request(REPO_API, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        release = json.loads(resp.read().decode("utf-8"))
    tag = release.get("tag_name", "?")
    asset = pick_asset(release.get("assets", []))
    print(f"版本 {tag}: {asset['name']} ({asset['size'] // 1048576} MB)")

    tmp = DEST_ROOT.parent / "_download.zip"
    download(asset["browser_download_url"], tmp)
    exe = extract(tmp, DEST_ROOT)
    tmp.unlink()

    exe_rel = str(exe.relative_to(ROOT))
    write_config(exe_rel)
    print(f"\n完成！指纹内核: {exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
