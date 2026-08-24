"""Chromium 内核定位。

打包发布后内核随安装包携带，路径固定不带版本号：

    APP_ROOT/Chromium/fingerprint/chrome.exe          指纹内核（默认）
    APP_ROOT/Chromium/patchright/chrome-win/chrome.exe 备用内核

开发模式下还会探测仓库里的 browsers/fingerprint-chromium/*（带版本号目录）。

config.yaml 的 browser.executable_path：
- 填绝对路径      → 直接用
- 填 "fingerprint" → 指纹内核（自动定位）
- 填 "patchright"  → patchright 自带内核（返回空串，交给 Playwright 默认查找）
- 填相对路径      → 先按 APP_ROOT 解析，再按 DATA_ROOT 解析
- 留空            → 按 fingerprint → patchright 顺序自动回退
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

KERNEL_FINGERPRINT = "fingerprint"
KERNEL_PATCHRIGHT = "patchright"

#: 打包后随安装包携带的固定位置
_BUNDLED_FINGERPRINT = ("Chromium", "fingerprint", "chrome.exe")
_BUNDLED_PATCHRIGHT_DIR = ("Chromium", "patchright")


def _exe_name() -> str:
    return "chrome.exe" if os.name == "nt" else "chrome"


def bundled_fingerprint(app_root: Path) -> Optional[Path]:
    """安装包携带的指纹内核。"""
    p = app_root.joinpath(*_BUNDLED_FINGERPRINT)
    return p if p.is_file() else None


def bundled_patchright(app_root: Path) -> Optional[Path]:
    """安装包携带的 patchright 内核（chrome-win/chrome.exe，目录名可能带版本）。"""
    root = app_root.joinpath(*_BUNDLED_PATCHRIGHT_DIR)
    if not root.is_dir():
        return None
    direct = root / "chrome-win" / _exe_name()
    if direct.is_file():
        return direct
    # chromium-1169/chrome-win/chrome.exe 这类布局
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        candidate = child / "chrome-win" / _exe_name()
        if candidate.is_file():
            return candidate
    return None


def repo_fingerprint(app_root: Path) -> Optional[Path]:
    """开发模式：仓库 browsers/fingerprint-chromium/<带版本目录>/chrome.exe。"""
    root = app_root / "browsers" / "fingerprint-chromium"
    if not root.is_dir():
        return None
    direct = root / _exe_name()
    if direct.is_file():
        return direct
    for child in sorted(root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        candidate = child / _exe_name()
        if candidate.is_file():
            return candidate
    return None


def find_fingerprint(app_root: Path) -> Optional[Path]:
    """按 打包位置 → 仓库位置 顺序定位指纹内核。"""
    return bundled_fingerprint(app_root) or repo_fingerprint(app_root)


def patchright_browsers_path(app_root: Path) -> Optional[Path]:
    """随包 patchright 内核的根目录，用于设置 PLAYWRIGHT_BROWSERS_PATH。"""
    root = app_root.joinpath(*_BUNDLED_PATCHRIGHT_DIR)
    return root if root.is_dir() else None


def resolve_executable(cfg, logger=None) -> str:
    """解析出最终传给 Playwright 的 executable_path。

    返回空串表示交给 Playwright 自己查找（patchright 默认内核）。
    找不到显式指定的内核时抛 FileNotFoundError，让上层给出明确错误。
    """
    app_root = cfg.root
    raw = str(cfg.get("browser.executable_path") or "").strip()
    low = raw.lower()

    if low in ("", "auto"):
        found = find_fingerprint(app_root)
        if found:
            return str(found)
        _prepare_patchright_env(app_root, logger)
        return ""

    if low == KERNEL_PATCHRIGHT:
        _prepare_patchright_env(app_root, logger)
        return ""

    if low == KERNEL_FINGERPRINT:
        found = find_fingerprint(app_root)
        if found:
            return str(found)
        raise FileNotFoundError(
            "未找到指纹内核。打包版应位于 Chromium/fingerprint/chrome.exe，"
            "开发模式可执行 python scripts/setup_browser.py 下载。"
        )

    # 显式路径：绝对 / 相对 APP_ROOT / 相对 DATA_ROOT 依次尝试
    candidates: List[Path] = []
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    if expanded.is_absolute():
        candidates.append(expanded)
    else:
        candidates.append(cfg.resolve_app(raw))
        candidates.append(cfg.resolve(raw))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    # 配置里写的是旧的带版本号相对路径，内核升级后目录名会变：回退自动定位
    fallback = find_fingerprint(app_root)
    if fallback:
        if logger:
            logger.warn(
                "browser_kernel",
                f"配置的内核路径不存在({raw})，已回退到 {fallback}",
            )
        return str(fallback)

    raise FileNotFoundError(f"浏览器可执行文件不存在: {raw}")


def _prepare_patchright_env(app_root: Path, logger=None) -> None:
    """让 Playwright 在随包目录里找内核（仅在未显式设置该变量时）。"""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    root = patchright_browsers_path(app_root)
    if root is None:
        return
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root)
    if logger:
        logger.info("browser_kernel", f"PLAYWRIGHT_BROWSERS_PATH={root}")


def describe(cfg) -> dict:
    """给 GUI 浏览器页用的内核状态快照。"""
    app_root = cfg.root
    fp = find_fingerprint(app_root)
    pr = bundled_patchright(app_root)
    raw = str(cfg.get("browser.executable_path") or "").strip()
    try:
        active = resolve_executable(cfg)
    except FileNotFoundError as exc:
        active = ""
        error = str(exc)
    else:
        error = ""
    if active and fp and Path(active) == fp:
        active_kernel = KERNEL_FINGERPRINT
    elif active:
        active_kernel = "custom"
    else:
        active_kernel = KERNEL_PATCHRIGHT
    return {
        "configured": raw,
        "active_kernel": active_kernel,
        "active_path": active,
        "fingerprint_path": str(fp) if fp else "",
        "patchright_path": str(pr) if pr else "",
        "fingerprint_available": fp is not None,
        "patchright_bundled": pr is not None,
        "error": error,
    }
