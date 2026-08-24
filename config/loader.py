"""YAML 配置加载器。

约定：
- 配置文件默认 config/config.yaml（打包后为 DATA_ROOT/config/config.yaml）
- 路径分两个地基：
    APP_ROOT   只读。程序自带资源（Chromium/、config.yaml.default）
    DATA_ROOT  可写。用户数据（data/ logs/ profiles/ config/）
  开发模式下两者都等于项目根，行为与改造前一致；
  PyInstaller 冻结后自动分离，避开 Program Files 无写权限。
- resolve() / path_of() 基于 DATA_ROOT；
  resolve_app() 基于 APP_ROOT（只给浏览器内核等随包资源用）。
- 支持点号路径访问：cfg.get("browser.headless", False)
- 支持环境变量覆盖：OA_BROWSER__HEADLESS=true
"""

from __future__ import annotations

import copy
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

APP_NAME = "OutlookAutomation"

#: 源码所在目录（开发模式的项目根）
_SOURCE_ROOT = Path(__file__).resolve().parent.parent

#: 是否运行在 PyInstaller 打包产物中
FROZEN = bool(getattr(sys, "frozen", False))


def _detect_app_root() -> Path:
    """程序资源根目录（只读）。

    onedir 打包下 EXE 旁边就是 Chromium/ 与 config.yaml.default，
    所以用 EXE 所在目录，而不是 sys._MEIPASS（那是 _internal/）。
    """
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return _SOURCE_ROOT


def _detect_bundle_root() -> Path:
    """PyInstaller 解包目录（内置 datas 所在）。开发模式 = 项目根。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()
    return _SOURCE_ROOT


def _detect_data_root() -> Path:
    """用户数据根目录（可写）。

    优先级：OA_DATA_DIR 环境变量 > 冻结模式用 %APPDATA% > 开发模式用项目根。
    OA_DATA_DIR 让便携模式和测试隔离成为可能。
    """
    override = os.environ.get("OA_DATA_DIR", "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override))).resolve()
    if FROZEN:
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base).resolve() / APP_NAME
        return Path.home() / f".{APP_NAME.lower()}"
    return _SOURCE_ROOT


APP_ROOT = _detect_app_root()
BUNDLE_ROOT = _detect_bundle_root()
DATA_ROOT = _detect_data_root()

#: 向后兼容：旧代码引用的 PROJECT_ROOT 指程序资源根
PROJECT_ROOT = APP_ROOT

DEFAULT_CONFIG_PATH = DATA_ROOT / "config" / "config.yaml"

#: 随包发布的默认配置（首次启动拷入 DATA_ROOT）
_SEED_CONFIG_CANDIDATES = (
    APP_ROOT / "config.yaml.default",
    BUNDLE_ROOT / "config" / "config.yaml",
)

ENV_PREFIX = "OA_"


def seed_user_config(target: Optional[Path] = None) -> Optional[Path]:
    """首次启动：把随包默认配置拷到用户目录。

    已存在则不动（升级不能覆盖用户配置）。返回实际写入路径，未写则 None。
    """
    dest = Path(target) if target else DEFAULT_CONFIG_PATH
    if dest.is_file():
        return None
    for candidate in _SEED_CONFIG_CANDIDATES:
        try:
            if candidate.is_file() and candidate.resolve() != dest.resolve():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(candidate, dest)
                return dest
        except OSError:
            continue
    return None


_DEFAULTS: Dict[str, Any] = {
    "browser": {
        "executable_path": "",
        "headless": False,
        "timeout": 60000,
        "nav_timeout": 45000,
        "locale": "zh-CN",
        "fingerprint_enabled": False,
        "fingerprint_platform": "windows",
        "fingerprint_brand": "Chrome",
        "viewport_widths": [1366, 1440, 1536, 1680, 1920],
        "viewport_heights": [768, 864, 900, 1050, 1080],
    },
    "profile": {
        "root": "profiles",
        "persistent": True,
        "reuse": True,
        "cleanup_on_exit": False,
    },
    "proxy": {
        "enabled": False,
        "mode": "single",
        "type": "http",
        "host": "127.0.0.1",
        "single_port": 7890,
        "port_start": 24000,
        "port_end": 24064,
        "max_per_proxy": 20,
        "ip_info_lookup": True,
        "ip_info_timeout": 8,
    },
    "resin": {
        "enabled": False,
        "url": "",
        "platform": "Default",
        "identity_mode": "email_prefix",
    },
    "flow": {
        "login_url": "https://login.live.com/",
        "max_captcha_retries": 3,
        "captcha_strategy": 0,
        "captcha_screenshot": True,
        "wait_verify_timeout": 300,
        "checkpoint_enabled": True,
    },
    "system": {
        "max_workers": 3,
        "task_retry": 1,
        "accounts_file": "accounts.txt",
        "account_separator": "----",
    },
    "database": {"path": "data/app.db"},
    "scheduler": {"enabled": False, "jobs": []},
    "logger": {
        "dir": "logs",
        "level": "INFO",
        "console": True,
        "file": True,
        "max_bytes": 10485760,
        "backup_count": 5,
    },
    "desktop": {
        "log_view_limit": 500,
        "refresh_interval": 2000,
        "table_page_size": 200,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并：override 的同名 key 覆盖 base，dict 递归，其他类型直接替换。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _coerce(text: str) -> Any:
    """把环境变量字符串转成 bool / int / float / str。"""
    low = text.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", ""):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _apply_env_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    """OA_BROWSER__HEADLESS=true → data["browser"]["headless"] = True"""
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(ENV_PREFIX):
            continue
        path = env_key[len(ENV_PREFIX):].lower().split("__")
        if not path or not path[0]:
            continue
        cursor = data
        for part in path[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[path[-1]] = _coerce(env_val)
    return data


class Config:
    """配置对象。点号访问 + 路径解析。"""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None):
        self._data = data
        self._path = path

    # ---------- 读取 ----------
    def get(self, dotted: str, default: Any = None) -> Any:
        cursor: Any = self._data
        for part in dotted.split("."):
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                return default
        return cursor

    def section(self, name: str) -> Dict[str, Any]:
        value = self._data.get(name, {})
        return value if isinstance(value, dict) else {}

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cursor = self._data
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[parts[-1]] = value

    def as_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)

    def update(self, partial: Dict[str, Any], save: bool = True) -> Path:
        """深合并更新配置（partial 只需包含要改的键），默认持久化到源文件。

        程序生成 YAML，避免手改语法错误。返回写入路径。
        """
        self._data = _deep_merge(self._data, partial)
        if save:
            return self.save()
        return self._path or DEFAULT_CONFIG_PATH

    # ---------- 路径 ----------
    @property
    def root(self) -> Path:
        """程序资源根目录（只读）。"""
        return APP_ROOT

    @property
    def data_root(self) -> Path:
        """用户数据根目录（可写）。"""
        return DATA_ROOT

    @property
    def source_path(self) -> Optional[Path]:
        return self._path

    def path_of(self, dotted: str, default: str = "") -> Path:
        """读取配置中的路径值并解析为绝对路径（基于 DATA_ROOT）。"""
        raw = str(self.get(dotted, default) or default)
        return self.resolve(raw)

    def resolve(self, raw: str) -> Path:
        """相对路径基于用户数据目录展开；绝对路径原样返回。"""
        p = Path(os.path.expandvars(os.path.expanduser(str(raw))))
        return p if p.is_absolute() else (DATA_ROOT / p)

    def resolve_app(self, raw: str) -> Path:
        """相对路径基于程序资源目录展开（浏览器内核等随包文件）。"""
        p = Path(os.path.expandvars(os.path.expanduser(str(raw))))
        return p if p.is_absolute() else (APP_ROOT / p)

    def ensure_dirs(self) -> None:
        """创建运行所需目录。"""
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        for dotted in ("logger.dir", "profile.root"):
            self.path_of(dotted).mkdir(parents=True, exist_ok=True)
        self.path_of("database.path").parent.mkdir(parents=True, exist_ok=True)

    # ---------- 持久化 ----------
    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path) if path else (self._path or DEFAULT_CONFIG_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self._data, fh, allow_unicode=True, sort_keys=False)
        return target

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Config path={self._path} sections={list(self._data)}>"


_lock = threading.Lock()
_cached: Optional[Config] = None


def load_config(path: Optional[str | Path] = None, use_cache: bool = True) -> Config:
    """加载配置。默认带进程级缓存，reload 时传 use_cache=False。"""
    global _cached
    if use_cache and path is None and _cached is not None:
        return _cached

    if path is None:
        # 冻结模式首次启动：把随包默认配置落到用户目录，否则用户无法修改
        try:
            seed_user_config()
        except OSError:
            pass

    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: Dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh)
                if isinstance(loaded, dict):
                    raw = loaded
        except yaml.YAMLError as exc:
            # 配置文件语法错误：回退默认值并大声告警，让面板至少能启动、
            # 用户可通过网页配置页重写合法配置
            print(f"=" * 60)
            print(f"[配置错误] {cfg_path} 解析失败，已回退内置默认配置！")
            print(f"  原因: {exc}")
            print(f"  可在桌面程序「设置」页重新设置并保存，")
            print(f"  或删除该文件让程序重建默认配置。")
            print(f"=" * 60)

    merged = _apply_env_overrides(_deep_merge(_DEFAULTS, raw))
    # 源文件不存在或解析失败（raw 为空）时置 None：表示当前生效的是默认配置
    cfg = Config(merged, cfg_path if raw else None)

    if use_cache and path is None:
        with _lock:
            _cached = cfg
    return cfg


def reload_config(path: Optional[str | Path] = None) -> Config:
    global _cached
    with _lock:
        _cached = None
    return load_config(path, use_cache=True)
