"""代理管理器：端口池加权选择 + IP 表现追踪 + 惩罚机制。

权重策略（沿用 OutlookRegister 的思路，参数化后可调）：
- 连续失败的 IP 直接排除（total>=2 且 win==0，或 fail>=4 且 win*2<fail）
- 权重 = ((1 + win*4) / (1 + fail*3)) * max(rate, 0.05)^2
- 单代理使用次数超过 max_per_proxy 后暂时跳过；全部超限时重置计数
"""

from __future__ import annotations

import random
import threading
from typing import Any, Dict, List, Optional

from .provider import (
    ProxyConfig,
    lookup_ip_info,
    resolve_geolocation,
    resolve_timezone,
)


class ProxyManager:
    """线程安全代理选择器。一个进程共享一个实例即可。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None, logger=None):
        self.cfg = ProxyConfig.from_dict(config or {})
        self.log = logger
        self._lock = threading.RLock()
        self._usage: Dict[int, int] = {}
        self._tracker: Dict[str, Dict[str, int]] = {}

    # ---------- 状态 ----------
    @property
    def direct(self) -> bool:
        return self.cfg.direct or not self.cfg.ports

    def reset(self) -> None:
        with self._lock:
            self._usage.clear()
            self._tracker.clear()

    @staticmethod
    def _key(proxy_url: str) -> str:
        if not proxy_url:
            return ""
        return proxy_url.split("//")[-1]

    def _stats_of(self, key: str) -> Dict[str, int]:
        return self._tracker.get(key, {"win": 0, "total": 0})

    # ---------- 选择 ----------
    def pick(self, exclude: str = "") -> str:
        """选一个代理 URL。直连模式返回空字符串。"""
        if self.direct:
            return ""

        with self._lock:
            candidates = self._available_ports(exclude)
            if not candidates:
                # 全部超限：重置使用计数再来一轮（仍避开被拉黑的 IP）
                self._usage.clear()
                candidates = self._available_ports(exclude)
            if not candidates:
                # 连拉黑的也算上还是空：降级使用全量端口，否则无代理可用
                candidates = self._available_ports(exclude, ignore_blacklist=True)
            if not candidates:
                candidates = list(self.cfg.ports)

            weights = [self._weight_of(port) for port in candidates]
            port = random.choices(candidates, weights=weights, k=1)[0]
            self._usage[port] = self._usage.get(port, 0) + 1
            return self.cfg.url_for(port)

    def _available_ports(self, exclude: str = "", ignore_blacklist: bool = False) -> List[int]:
        excluded_key = self._key(exclude)
        out: List[int] = []
        for port in self.cfg.ports:
            key = f"{self.cfg.host}:{port}"
            if excluded_key and key == excluded_key:
                continue
            if self._usage.get(port, 0) >= self.cfg.max_per_proxy:
                continue
            if not ignore_blacklist and self._is_blacklisted(key):
                continue
            out.append(port)
        return out

    def _is_blacklisted(self, key: str) -> bool:
        info = self._stats_of(key)
        total, win = info.get("total", 0), info.get("win", 0)
        fail = max(total - win, 0)
        if total >= 2 and win == 0:
            return True
        if fail >= 4 and win * 2 < fail:
            return True
        return False

    def _weight_of(self, port: int) -> float:
        key = f"{self.cfg.host}:{port}"
        info = self._stats_of(key)
        total, win = info.get("total", 0), info.get("win", 0)
        fail = max(total - win, 0)
        rate = (win / total) if total else 0.5
        weight = ((1 + win * 4) / (1 + fail * 3)) * (max(rate, 0.05) ** 2)
        return max(0.01, weight)

    def fresh(self, exclude: str = "") -> str:
        """换一个与 exclude 不同的代理（重试时用）。"""
        if self.direct:
            return ""
        for _ in range(8):
            candidate = self.pick(exclude=exclude)
            if candidate != exclude:
                return candidate
        return self.pick()

    # ---------- 反馈 ----------
    def record(self, proxy_url: str, success: bool) -> None:
        """登记一次使用结果，影响后续权重。"""
        key = self._key(proxy_url)
        if not key:
            return
        with self._lock:
            info = self._tracker.setdefault(key, {"win": 0, "total": 0})
            info["total"] += 1
            if success:
                info["win"] += 1

    def penalize(self, proxy_url: str, penalty: int = 4) -> None:
        """重罚：直接加满失败计数（用于验证码 iframe 都没出现的场景）。"""
        key = self._key(proxy_url)
        if not key:
            return
        with self._lock:
            info = self._tracker.setdefault(key, {"win": 0, "total": 0})
            info["total"] += max(1, penalty)

    # ---------- 环境信息 ----------
    def context_info(self, proxy_url: str) -> Dict[str, Any]:
        """返回浏览器上下文需要的地区信息：timezone / geolocation / country。"""
        if self.cfg.ip_info_lookup:
            info = lookup_ip_info(proxy_url, timeout=self.cfg.ip_info_timeout)
        else:
            info = {"ip": "", "country": "??", "timezone": "", "loc": ""}
        return {
            "ip": info.get("ip", ""),
            "country": info.get("country", "??"),
            "timezone": resolve_timezone(info),
            "geolocation": resolve_geolocation(info),
        }

    # ---------- 报表 ----------
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            entries = []
            for key, info in sorted(self._tracker.items()):
                total, win = info.get("total", 0), info.get("win", 0)
                entries.append(
                    {
                        "proxy": key,
                        "win": win,
                        "total": total,
                        "rate": round(win / total * 100, 1) if total else None,
                        "blacklisted": self._is_blacklisted(key),
                    }
                )
            return {
                "direct": self.direct,
                "type": self.cfg.type,
                "host": self.cfg.host,
                "ports": len(self.cfg.ports),
                "max_per_proxy": self.cfg.max_per_proxy,
                "usage": dict(self._usage),
                "tracker": entries,
            }


_manager_lock = threading.Lock()
_manager: Optional[ProxyManager] = None


def get_proxy_manager(config: Optional[Dict[str, Any]] = None, logger=None) -> ProxyManager:
    """进程级单例。"""
    global _manager
    with _manager_lock:
        if _manager is None:
            if config is None:
                from config import load_config

                config = load_config().section("proxy")
            _manager = ProxyManager(config, logger=logger)
        return _manager


def reset_proxy_manager() -> None:
    global _manager
    with _manager_lock:
        _manager = None
