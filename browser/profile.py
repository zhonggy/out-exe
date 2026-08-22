"""浏览器环境（Profile）管理。

每个 profile 是 profiles/ 下的一个独立目录，承载 Cookie / LocalStorage / 缓存。
- reuse=True：同一账号固定复用一个 profile（登录态可保留，减少验证）
- reuse=False：每次任务新建临时 profile，任务结束按配置清理
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from database import BrowserProfile, ProfileStatus

_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")


def sanitize(name: str) -> str:
    """账号 → 可作目录名的安全字符串。"""
    return _SAFE.sub("_", name or "")[:48] or "anon"


def make_fingerprint_seed(salt: str = "") -> int:
    """生成 32 位正整数指纹种子（混入盐值 + 时间 + 随机）。"""
    salt_part = 0
    if salt:
        salt_part = int(hashlib.md5(salt.encode("utf-8")).hexdigest()[:8], 16)
    seed = (int(time.time() * 1000) ^ (salt_part * 2654435761) ^ random.getrandbits(32)) & 0x7FFFFFFF
    return seed or random.randint(1, 0x7FFFFFFF)


class ProfileManager:
    """Profile 目录分配与回收。可选接 Database 做持久化登记。"""

    def __init__(
        self,
        root: str | Path = "profiles",
        reuse: bool = True,
        cleanup_on_exit: bool = False,
        db=None,
        logger=None,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.reuse = reuse
        self.cleanup_on_exit = cleanup_on_exit
        self.db = db
        self.log = logger
        self._lock = threading.RLock()
        self._in_use: Dict[str, str] = {}  # profile_id -> account

    # ---------- 分配 ----------
    def acquire(self, account: str = "", proxy: str = "", seed: Optional[int] = None) -> BrowserProfile:
        """取得一个可用 profile。reuse 模式下同账号返回同一目录。"""
        with self._lock:
            profile_id = self._resolve_id(account)
            path = self.root / profile_id
            path.mkdir(parents=True, exist_ok=True)

            existing = self.db.get_profile(profile_id) if self.db else None
            profile = existing or BrowserProfile(profile_id=profile_id, path=str(path))
            profile.path = str(path)
            profile.account = account or profile.account
            profile.proxy = proxy
            profile.status = ProfileStatus.IN_USE.value
            profile.last_used = time.time()
            profile.use_count = (profile.use_count or 0) + 1
            if seed is not None:
                profile.fingerprint_seed = seed
            elif profile.fingerprint_seed is None:
                profile.fingerprint_seed = make_fingerprint_seed(account or profile_id)

            self._in_use[profile_id] = account
            if self.db:
                self.db.upsert_profile(profile)
                if account:
                    self.db.bind_account_profile(account, profile_id)
            if self.log:
                self.log.info("profile_acquire", f"profile={profile_id} reuse={self.reuse}")
            return profile

    def _resolve_id(self, account: str) -> str:
        if self.reuse and account:
            return f"acc_{sanitize(account)}"
        stamp = time.strftime("%Y%m%d%H%M%S")
        return f"tmp_{sanitize(account) if account else 'anon'}_{stamp}_{os.getpid()}_{threading.get_ident()}"

    # ---------- 释放 ----------
    def release(self, profile: BrowserProfile | str, broken: bool = False) -> None:
        """归还 profile。临时 profile 且 cleanup_on_exit=True 时删除目录。"""
        profile_id = profile if isinstance(profile, str) else profile.profile_id
        path = None if isinstance(profile, str) else profile.path
        with self._lock:
            self._in_use.pop(profile_id, None)
            status = ProfileStatus.BROKEN.value if broken else ProfileStatus.IDLE.value
            if self.db:
                self.db.set_profile_status(profile_id, status)

            temporary = profile_id.startswith("tmp_")
            if temporary and self.cleanup_on_exit:
                target = Path(path) if path else (self.root / profile_id)
                self.delete(profile_id, target)
            if self.log:
                self.log.info("profile_release", f"profile={profile_id} status={status}")

    def delete(self, profile_id: str, path: Optional[Path] = None) -> bool:
        """删除 profile 目录及登记。"""
        target = Path(path) if path else (self.root / profile_id)
        ok = True
        try:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
        except Exception:
            ok = False
        if self.db:
            self.db.delete_profile(profile_id)
        return ok

    # ---------- 维护 ----------
    def list_dirs(self) -> List[Dict[str, Any]]:
        """扫描磁盘上的 profile 目录（含未登记的）。"""
        out: List[Dict[str, Any]] = []
        if not self.root.exists():
            return out
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            out.append(
                {
                    "profile_id": entry.name,
                    "path": str(entry),
                    "size_mb": round(self._dir_size(entry) / 1024 / 1024, 2),
                    "in_use": entry.name in self._in_use,
                    "temporary": entry.name.startswith("tmp_"),
                    "mtime": entry.stat().st_mtime,
                }
            )
        return out

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        for dirpath, _dirnames, filenames in os.walk(path):
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
        return total

    def clear_temporary(self) -> int:
        """清理所有 tmp_ 前缀的临时 profile。"""
        removed = 0
        for item in self.list_dirs():
            if item["temporary"] and not item["in_use"]:
                if self.delete(item["profile_id"], Path(item["path"])):
                    removed += 1
        if self.log:
            self.log.info("profile_cleanup", f"清理临时 profile {removed} 个")
        return removed

    def clear_all(self) -> int:
        """清空 profiles 根目录下全部内容（保留根目录）。高危：会丢失全部登录态。"""
        removed = 0
        for item in self.list_dirs():
            if self.delete(item["profile_id"], Path(item["path"])):
                removed += 1
        if self.db:
            for profile in self.db.list_profiles():
                self.db.delete_profile(profile.profile_id)
        if self.log:
            self.log.warn("profile_cleanup", f"已清空 profiles 共 {removed} 项: {self.root}")
        return removed

    def prune_older_than(self, days: float = 30.0) -> int:
        """删除超过 N 天未使用的临时 profile。"""
        cutoff = time.time() - days * 86400
        removed = 0
        for item in self.list_dirs():
            if item["in_use"] or not item["temporary"]:
                continue
            if item["mtime"] < cutoff:
                if self.delete(item["profile_id"], Path(item["path"])):
                    removed += 1
        return removed

    def snapshot(self) -> Dict[str, Any]:
        dirs = self.list_dirs()
        return {
            "root": str(self.root),
            "reuse": self.reuse,
            "cleanup_on_exit": self.cleanup_on_exit,
            "count": len(dirs),
            "total_mb": round(sum(d["size_mb"] for d in dirs), 2),
            "in_use": list(self._in_use),
            "profiles": dirs,
        }


_pm_lock = threading.Lock()
_pm: Optional[ProfileManager] = None


def get_profile_manager(cfg=None, db=None, logger=None) -> ProfileManager:
    """进程级单例。"""
    global _pm
    with _pm_lock:
        if _pm is None:
            if cfg is None:
                from config import load_config

                cfg = load_config()
            if db is None:
                from database import get_db

                db = get_db(cfg.path_of("database.path", "data/app.db"))
            _pm = ProfileManager(
                root=cfg.path_of("profile.root", "profiles"),
                reuse=bool(cfg.get("profile.reuse", True)),
                cleanup_on_exit=bool(cfg.get("profile.cleanup_on_exit", False)),
                db=db,
                logger=logger,
            )
        return _pm


def reset_profile_manager() -> None:
    global _pm
    with _pm_lock:
        _pm = None
