"""浏览器实例管理器：创建、追踪、批量关闭。

Worker 通过 BrowserManager 申请会话，管理器登记所有活跃实例，
面板可查询当前有哪些浏览器在跑；停止任务时能统一收尾。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from .browser import BrowserLaunchError, BrowserSession
from .profile import ProfileManager, get_profile_manager


class BrowserManager:
    """活跃浏览器实例登记表 + 会话工厂。"""

    def __init__(self, cfg, profile_manager: Optional[ProfileManager] = None, proxy_manager=None, logger=None):
        self.cfg = cfg
        self.pm = profile_manager
        self.proxy_manager = proxy_manager
        self.log = logger
        self._lock = threading.RLock()
        self._sessions: Dict[str, BrowserSession] = {}
        self._seq = 0

    # ---------- 会话 ----------
    def create(self, account: str = "", key: Optional[str] = None) -> BrowserSession:
        """创建并启动一个会话，登记后返回。启动失败抛 BrowserLaunchError。"""
        with self._lock:
            self._seq += 1
            session_key = key or f"s{self._seq}"

        session = BrowserSession(
            cfg=self.cfg,
            profile_manager=self.pm,
            proxy_manager=self.proxy_manager,
            logger=self.log,
            account=account,
        )
        try:
            session.start()
        except BrowserLaunchError:
            raise
        with self._lock:
            self._sessions[session_key] = session
        return session

    def release(self, session: BrowserSession, broken: bool = False) -> None:
        with self._lock:
            keys = [k for k, v in self._sessions.items() if v is session]
            for k in keys:
                self._sessions.pop(k, None)
        session.close(broken=broken)

    def close_all(self, broken: bool = False) -> int:
        """关闭全部活跃会话，返回关闭数量。"""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.close(broken=broken)
            except Exception:
                pass
        if self.log and sessions:
            self.log.info("browser_close_all", f"已关闭 {len(sessions)} 个浏览器实例")
        return len(sessions)

    # ---------- 查询 ----------
    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._sessions.items())
        out = []
        for key, session in items:
            data = session.info()
            data["key"] = key
            out.append(data)
        return out


_bm_lock = threading.Lock()
_bm: Optional[BrowserManager] = None


def get_browser_manager(cfg=None, logger=None) -> BrowserManager:
    """进程级单例。"""
    global _bm
    with _bm_lock:
        if _bm is None:
            if cfg is None:
                from config import load_config

                cfg = load_config()
            from proxy import get_proxy_manager

            _bm = BrowserManager(
                cfg=cfg,
                profile_manager=get_profile_manager(cfg, logger=logger),
                proxy_manager=get_proxy_manager(cfg.section("proxy"), logger=logger),
                logger=logger,
            )
        return _bm


def reset_browser_manager() -> None:
    global _bm
    with _bm_lock:
        _bm = None
