"""更新检查与下载：GitHub Releases 作为分发源。

设计取舍：

- **不自己实现替换逻辑。** 程序装在 Program Files（只读，需要管理员权限），
  正在运行的 EXE 也无法自我覆盖。所以「立即重启并更新」实际做的是：
  拉起下载好的安装包（走 UAC 提权），然后退出自己。Inno Setup 的安装器
  本身就处理了「同版本号覆盖安装」「结束残留进程」这些事。
- **下载到数据目录而不是临时目录。** ``%APPDATA%\\OutlookAutomation\\updates``
  下载中断后能续着看到文件，用户也能手动双击安装。临时目录会被系统清理，
  排查问题时文件已经没了。
- **版本比较用元组而不是字符串。** ``"1.10.0" > "1.9.0"`` 字符串比较是 False，
  会导致有新版却提示"已是最新"。
- **只信任 tag_name。** Release 标题可以随便改，tag 是打包时写进安装包的那个。

网络失败一律不抛到界面之外：更新检查失败不该影响程序使用。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

#: GitHub API 端点模板
_API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
_API_LIST = "https://api.github.com/repos/{repo}/releases?per_page=10"
_RELEASE_PAGE = "https://github.com/{repo}/releases"

#: 版本号里的数字段。"1.2.0-build3" → (1, 2, 0)
_NUM_RE = re.compile(r"\d+")

_USER_AGENT = "OutlookAutomation-Updater"


def parse_version(text: str) -> Tuple[int, ...]:
    """版本号 → 可比较的数字元组。

    ``v1.2.0`` / ``1.2.0`` / ``1.2`` / ``1.2.0-build7`` 都能吃。
    非法输入返回 ``(0,)``，语义是"最旧"，这样脏数据不会被误判为新版本。
    """
    if not text:
        return (0,)
    # 只取主版本段（- 之前），预发布后缀不参与大小比较
    head = str(text).strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = _NUM_RE.findall(head)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts[:4])


def compare_versions(left: str, right: str) -> int:
    """返回 -1 / 0 / 1，语义同 ``cmp(left, right)``。

    长度不同时短的补零：``1.2`` == ``1.2.0``。
    """
    a, b = parse_version(left), parse_version(right)
    size = max(len(a), len(b))
    a = a + (0,) * (size - len(a))
    b = b + (0,) * (size - len(b))
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def is_newer(candidate: str, current: str) -> bool:
    return compare_versions(candidate, current) > 0


def human_bytes(size: float) -> str:
    for unit, factor in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{int(size)} B"


@dataclass
class ReleaseInfo:
    """一个 Release 的必要信息。"""

    version: str = ""
    tag: str = ""
    name: str = ""
    notes: str = ""
    published_at: str = ""
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    html_url: str = ""
    prerelease: bool = False

    @property
    def has_asset(self) -> bool:
        return bool(self.asset_url)

    @property
    def size_text(self) -> str:
        return human_bytes(self.asset_size) if self.asset_size else "未知"


@dataclass
class CheckResult:
    """检查更新的结果。``error`` 非空表示检查本身失败。"""

    current: str = ""
    latest: Optional[ReleaseInfo] = None
    has_update: bool = False
    error: str = ""
    checked_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return not self.error


class UpdateManager:
    """更新检查 / 下载 / 应用。

    界面层只需要调四个方法，对应四个按钮：
    ``check()`` / ``download()`` / ``apply_and_restart()`` / ``release_page_url()``。
    """

    def __init__(self, cfg, logger=None):
        self.cfg = cfg
        self.log = logger
        self._last: Optional[CheckResult] = None

    # ---------- 配置 ----------
    @property
    def repo(self) -> str:
        return str(self.cfg.get("update.repo", "") or "").strip().strip("/")

    @property
    def asset_name(self) -> str:
        return str(
            self.cfg.get("update.asset_name", "OutlookAutomation-Setup.exe") or ""
        ).strip()

    @property
    def timeout(self) -> int:
        return max(3, int(self.cfg.get("update.timeout", 15) or 15))

    @property
    def include_prerelease(self) -> bool:
        return bool(self.cfg.get("update.include_prerelease", False))

    @property
    def current_version(self) -> str:
        from config import APP_VERSION

        return APP_VERSION

    def release_page_url(self) -> str:
        repo = self.repo
        if not repo:
            return "https://github.com"
        return _RELEASE_PAGE.format(repo=repo)

    def download_dir(self) -> Path:
        target = self.cfg.resolve("updates")
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ---------- 检查 ----------
    def check(self) -> CheckResult:
        """查询最新 Release。网络异常转成 ``error`` 字段，不抛出。"""
        current = self.current_version
        repo = self.repo
        if not repo:
            return CheckResult(current=current, error="未配置发布仓库（update.repo）")

        try:
            payload = self._fetch_release(repo)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                msg = "仓库或 Release 不存在（可能尚未发布任何版本）"
            elif exc.code == 403:
                msg = "GitHub API 限流（未认证请求每小时 60 次），请稍后再试"
            else:
                msg = f"HTTP {exc.code} {exc.reason}"
            return CheckResult(current=current, error=msg)
        except urllib.error.URLError as exc:
            return CheckResult(current=current, error=f"网络不可达：{exc.reason}")
        except (TimeoutError, OSError) as exc:
            return CheckResult(current=current, error=f"连接失败：{exc}")
        except (ValueError, KeyError) as exc:
            return CheckResult(current=current, error=f"响应解析失败：{exc}")

        if payload is None:
            return CheckResult(current=current, error="没有找到可用的 Release")

        info = self._to_release_info(payload)
        result = CheckResult(
            current=current,
            latest=info,
            has_update=is_newer(info.version, current),
        )
        self._last = result
        if self.log:
            if result.has_update:
                self.log.info("update", f"发现新版本 {info.version}（当前 {current}）")
            else:
                self.log.info("update", f"已是最新版本 {current}")
        return result

    def _fetch_release(self, repo: str) -> Optional[Dict[str, Any]]:
        """取最新 Release。含预发布时走列表接口自行挑选。

        ``/releases/latest`` 会跳过预发布，所以两种模式必须走不同端点。
        """
        if self.include_prerelease:
            data = self._get_json(_API_LIST.format(repo=repo))
            if not isinstance(data, list) or not data:
                return None
            for item in data:
                if not item.get("draft"):
                    return item
            return None
        return self._get_json(_API_LATEST.format(repo=repo))

    def _get_json(self, url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    def _to_release_info(self, payload: Dict[str, Any]) -> ReleaseInfo:
        tag = str(payload.get("tag_name") or "")
        wanted = self.asset_name.lower()
        asset = None
        assets = payload.get("assets") or []
        # 先精确匹配配置的资产名，退而求其次找任意 .exe
        for item in assets:
            if str(item.get("name") or "").lower() == wanted:
                asset = item
                break
        if asset is None:
            for item in assets:
                if str(item.get("name") or "").lower().endswith(".exe"):
                    asset = item
                    break

        return ReleaseInfo(
            version=tag.lstrip("vV"),
            tag=tag,
            name=str(payload.get("name") or tag),
            notes=str(payload.get("body") or ""),
            published_at=str(payload.get("published_at") or ""),
            asset_name=str((asset or {}).get("name") or ""),
            asset_url=str((asset or {}).get("browser_download_url") or ""),
            asset_size=int((asset or {}).get("size") or 0),
            html_url=str(payload.get("html_url") or self.release_page_url()),
            prerelease=bool(payload.get("prerelease")),
        )

    # ---------- 下载 ----------
    def local_installer(self, info: ReleaseInfo) -> Path:
        name = info.asset_name or self.asset_name
        return self.download_dir() / f"{info.version or 'latest'}-{name}"

    def is_downloaded(self, info: ReleaseInfo) -> bool:
        """本地已有完整安装包？

        比对文件大小而不只看存在性 —— 下载中断留下的半个文件同样存在，
        直接拿去安装会报"安装包损坏"，很难让用户联想到是上次断网导致的。
        """
        target = self.local_installer(info)
        if not target.is_file():
            return False
        if info.asset_size and target.stat().st_size != info.asset_size:
            return False
        return target.stat().st_size > 0

    def download(
        self,
        info: ReleaseInfo,
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Path:
        """下载安装包到数据目录，返回本地路径。

        已存在且大小一致时直接复用，不重复下载几百 MB。
        """
        if not info.has_asset:
            raise RuntimeError(
                f"该 Release 没有可下载的安装包（{self.asset_name}）。\n"
                "请到发布页手动下载。"
            )

        target = self.local_installer(info)
        if self.is_downloaded(info):
            if progress:
                progress(info.asset_size, info.asset_size, "已存在，跳过下载")
            return target

        # 先写临时文件，完成后再改名：中断时不会留下看起来完整的文件
        partial = target.with_suffix(target.suffix + ".part")
        partial.unlink(missing_ok=True)

        request = urllib.request.Request(
            info.asset_url, headers={"User-Agent": _USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                total = int(response.headers.get("Content-Length") or info.asset_size or 0)
                done = 0
                chunk_size = 256 * 1024
                with open(partial, "wb") as fh:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(
                                done,
                                total,
                                f"{human_bytes(done)} / {human_bytes(total) if total else '?'}",
                            )
        except Exception:
            partial.unlink(missing_ok=True)
            raise

        target.unlink(missing_ok=True)
        partial.replace(target)
        if self.log:
            self.log.ok("update", f"安装包已下载：{target}")
        return target

    def cleanup_old(self, keep: Optional[Path] = None) -> int:
        """清掉历史安装包。每个几百 MB，攒几个版本很占地方。"""
        removed = 0
        for item in self.download_dir().glob("*"):
            if keep is not None and item == keep:
                continue
            if item.suffix.lower() in (".exe", ".part"):
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    # ---------- 应用 ----------
    def apply_and_restart(self, installer: Path) -> None:
        """拉起安装包并让调用方退出当前进程。

        安装器需要管理员权限（装到 Program Files），所以用 ShellExecute 触发
        UAC 提权 —— ``subprocess.Popen`` 起的子进程继承当前权限，非管理员
        运行时安装器会直接失败。

        本方法只负责"把安装器拉起来"，退出当前进程由界面层做：
        它得先关掉 IPC、停掉定时器，顺序不能颠倒。
        """
        installer = Path(installer)
        if not installer.is_file():
            raise RuntimeError(f"安装包不存在：{installer}")

        if sys.platform != "win32":
            raise RuntimeError("自动更新目前只支持 Windows")

        try:
            # SW_SHOWNORMAL = 1；"runas" 强制提权，即使 EXE 没有 requireAdmin 清单
            os.startfile(str(installer))  # noqa: S606 - 用户确认过的本程序安装包
        except OSError as exc:
            raise RuntimeError(f"无法启动安装程序：{exc}") from exc

        if self.log:
            self.log.info("update", f"已启动安装程序：{installer}")

    def open_release_page(self) -> str:
        """在浏览器打开发布页，返回打开的 URL。"""
        url = self.release_page_url()
        try:
            if sys.platform == "win32":
                os.startfile(url)  # noqa: S606 - 固定的 GitHub 发布页
            elif sys.platform == "darwin":
                subprocess.Popen(["open", url])
            else:
                subprocess.Popen(["xdg-open", url])
        except OSError:
            pass
        return url


def get_update_manager(cfg=None, logger=None) -> UpdateManager:
    if cfg is None:
        from config import load_config

        cfg = load_config()
    return UpdateManager(cfg, logger=logger)
