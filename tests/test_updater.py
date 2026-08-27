"""更新模块单元测试：版本比较与 Release 解析。

这些是纯函数，不需要 Qt 也不需要网络，放 pytest 里跑最快。
GUI 侧的按钮状态机在 tests/smoke_update.py 里覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from updater import UpdateManager, compare_versions, is_newer, parse_version  # noqa: E402
from updater.manager import ReleaseInfo, human_bytes  # noqa: E402


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("V1.2.3", (1, 2, 3)),
        ("1.2", (1, 2)),
        ("1", (1,)),
        ("1.2.0-build7", (1, 2, 0)),
        ("1.2.0+meta", (1, 2, 0)),
        ("0.0.0-build42", (0, 0, 0)),
        ("", (0,)),
        ("abc", (0,)),
        (None, (0,)),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_parse_version_caps_at_four_segments():
    assert parse_version("1.2.3.4.5") == (1, 2, 3, 4)


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("1.2.0", "1.2.0", 0),
        ("1.2", "1.2.0", 0),        # 补零后相等
        ("v1.2.0", "1.2.0", 0),     # v 前缀等价
        ("1.2.1", "1.2.0", 1),
        ("1.2.0", "1.2.1", -1),
        ("2.0.0", "1.9.9", 1),
        ("1.10.0", "1.9.0", 1),     # 字符串比较会判错的经典用例
        ("1.9.0", "1.10.0", -1),
        ("1.2.0-build7", "1.2.0", 0),
    ],
)
def test_compare_versions(left, right, expected):
    assert compare_versions(left, right) == expected


def test_is_newer_boundary():
    """相等不算新版本 —— 否则每次检查都会提示有更新。"""
    assert not is_newer("1.2.0", "1.2.0")
    assert is_newer("1.2.1", "1.2.0")
    assert not is_newer("1.1.9", "1.2.0")


def test_is_newer_rejects_garbage():
    """脏 tag 视为最旧，不能把用户往下推到坏版本。"""
    assert not is_newer("garbage", "1.0.0")


def test_human_bytes():
    assert human_bytes(512) == "512 B"
    assert human_bytes(2048) == "2.0 KB"
    assert human_bytes(5 * 1024 ** 2) == "5.0 MB"
    assert human_bytes(int(1.5 * 1024 ** 3)) == "1.5 GB"


class _StubCfg:
    """最小配置替身，只实现 UpdateManager 用到的接口。"""

    def __init__(self, data, root: Path):
        self._data = data
        self._root = root

    def get(self, dotted, default=None):
        cursor = self._data
        for part in dotted.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor

    def resolve(self, raw):
        return self._root / raw


def _manager(tmp_path, **overrides):
    data = {
        "update": {
            "repo": "owner/repo",
            "asset_name": "OutlookAutomation-Setup.exe",
            "include_prerelease": False,
            "timeout": 5,
            **overrides,
        }
    }
    return UpdateManager(_StubCfg(data, tmp_path))


def _payload(tag="v2.0.0", assets=None):
    return {
        "tag_name": tag,
        "name": f"Release {tag}",
        "body": "notes here",
        "published_at": "2026-08-27T10:00:00Z",
        "html_url": f"https://github.com/owner/repo/releases/tag/{tag}",
        "prerelease": False,
        "draft": False,
        "assets": assets if assets is not None else [
            {
                "name": "OutlookAutomation-Setup.exe",
                "browser_download_url": "https://example.invalid/Setup.exe",
                "size": 12345,
            }
        ],
    }


def test_release_info_picks_configured_asset(tmp_path):
    manager = _manager(tmp_path)
    info = manager._to_release_info(
        _payload(
            assets=[
                {"name": "other.zip", "browser_download_url": "u1", "size": 1},
                {
                    "name": "OutlookAutomation-Setup.exe",
                    "browser_download_url": "u2",
                    "size": 2,
                },
            ]
        )
    )
    assert info.asset_name == "OutlookAutomation-Setup.exe"
    assert info.asset_url == "u2"
    assert info.version == "2.0.0"
    assert info.tag == "v2.0.0"


def test_release_info_falls_back_to_any_exe(tmp_path):
    """资产改名后仍能找到 —— 只认死名字会让改名的一版彻底无法更新。"""
    manager = _manager(tmp_path)
    info = manager._to_release_info(
        _payload(assets=[{"name": "Setup-x64.exe", "browser_download_url": "u", "size": 9}])
    )
    assert info.asset_name == "Setup-x64.exe"
    assert info.has_asset


def test_release_info_without_asset(tmp_path):
    manager = _manager(tmp_path)
    info = manager._to_release_info(_payload(assets=[]))
    assert not info.has_asset
    assert info.size_text == "未知"


def test_check_reports_error_without_repo(tmp_path):
    manager = _manager(tmp_path, repo="")
    result = manager.check()
    assert not result.ok
    assert "repo" in result.error or "仓库" in result.error
    assert result.latest is None


def test_check_detects_update(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_get_json", lambda url: _payload("v99.0.0"))
    monkeypatch.setattr(
        UpdateManager, "current_version", property(lambda self: "1.0.0")
    )
    result = manager.check()
    assert result.ok
    assert result.has_update
    assert result.latest.version == "99.0.0"


def test_check_reports_up_to_date(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_get_json", lambda url: _payload("v1.0.0"))
    monkeypatch.setattr(
        UpdateManager, "current_version", property(lambda self: "1.0.0")
    )
    result = manager.check()
    assert result.ok
    assert not result.has_update


def test_check_wraps_network_error(tmp_path, monkeypatch):
    """网络异常必须转成 error 字段，不能抛到界面。"""
    manager = _manager(tmp_path)

    def boom(url):
        raise OSError("connection refused")

    monkeypatch.setattr(manager, "_get_json", boom)
    result = manager.check()
    assert not result.ok
    assert "connection refused" in result.error


def test_is_downloaded_rejects_partial(tmp_path):
    """半个安装包不能算已下载，否则安装时报损坏很难排查。"""
    manager = _manager(tmp_path)
    info = ReleaseInfo(version="2.0.0", asset_name="Setup.exe", asset_size=1000)
    target = manager.local_installer(info)
    target.parent.mkdir(parents=True, exist_ok=True)

    assert not manager.is_downloaded(info)

    target.write_bytes(b"\0" * 400)
    assert not manager.is_downloaded(info)

    target.write_bytes(b"\0" * 1000)
    assert manager.is_downloaded(info)


def test_download_rejects_release_without_asset(tmp_path):
    manager = _manager(tmp_path)
    info = ReleaseInfo(version="2.0.0")
    with pytest.raises(RuntimeError, match="没有可下载"):
        manager.download(info)


def test_download_reuses_existing_file(tmp_path):
    """已有完整包直接复用 —— 几百 MB 不该重下。"""
    manager = _manager(tmp_path)
    info = ReleaseInfo(
        version="2.0.0",
        asset_name="Setup.exe",
        asset_url="https://example.invalid/Setup.exe",
        asset_size=64,
    )
    target = manager.local_installer(info)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\0" * 64)

    events = []
    result = manager.download(info, progress=lambda c, t, s: events.append(s))
    assert result == target
    assert events and "跳过" in events[-1]


def test_cleanup_old_keeps_current(tmp_path):
    manager = _manager(tmp_path)
    folder = manager.download_dir()
    keep = folder / "2.0.0-Setup.exe"
    keep.write_bytes(b"x")
    (folder / "1.0.0-Setup.exe").write_bytes(b"x")
    (folder / "1.5.0-Setup.exe.part").write_bytes(b"x")

    removed = manager.cleanup_old(keep=keep)
    assert removed == 2
    assert keep.is_file()


def test_release_page_url(tmp_path):
    manager = _manager(tmp_path)
    assert manager.release_page_url() == "https://github.com/owner/repo/releases"


def test_apply_rejects_missing_installer(tmp_path):
    manager = _manager(tmp_path)
    with pytest.raises(RuntimeError, match="不存在"):
        manager.apply_and_restart(tmp_path / "nope.exe")


# ---------- 版本号来源优先级 ----------
def test_version_env_overrides_file(tmp_path, monkeypatch):
    """OA_VERSION 优先于 version.txt —— CI 里靠它把 tag 传进产物。"""
    import config.loader as loader

    monkeypatch.setenv("OA_VERSION", "v9.8.7")
    monkeypatch.setattr(loader, "APP_ROOT", tmp_path)
    monkeypatch.setattr(loader, "BUNDLE_ROOT", tmp_path)
    (tmp_path / "version.txt").write_text("1.1.1\n", encoding="utf-8")

    assert loader._detect_app_version() == "9.8.7"


def test_version_reads_file_when_no_env(tmp_path, monkeypatch):
    import config.loader as loader

    monkeypatch.delenv("OA_VERSION", raising=False)
    monkeypatch.setattr(loader, "APP_ROOT", tmp_path)
    monkeypatch.setattr(loader, "BUNDLE_ROOT", tmp_path)
    (tmp_path / "version.txt").write_text("v2.3.4\n", encoding="utf-8")

    assert loader._detect_app_version() == "2.3.4"


def test_version_falls_back_to_constant(tmp_path, monkeypatch):
    """源码模式没有 version.txt，必须回退而不是报错。"""
    import config.loader as loader

    monkeypatch.delenv("OA_VERSION", raising=False)
    monkeypatch.setattr(loader, "APP_ROOT", tmp_path)
    monkeypatch.setattr(loader, "BUNDLE_ROOT", tmp_path)

    assert loader._detect_app_version() == loader._FALLBACK_VERSION
