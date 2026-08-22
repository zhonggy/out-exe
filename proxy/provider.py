"""代理提供者：解析配置、出口 IP 信息查询、国家→时区映射。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 国家代码 → (locale, 默认时区)
LOCALE_MAP: Dict[str, tuple] = {
    "JP": ("ja-JP", "Asia/Tokyo"),
    "US": ("en-US", "America/Chicago"),
    "HK": ("zh-HK", "Asia/Hong_Kong"),
    "SG": ("en-SG", "Asia/Singapore"),
    "KR": ("ko-KR", "Asia/Seoul"),
    "GB": ("en-GB", "Europe/London"),
    "DE": ("de-DE", "Europe/Berlin"),
    "FR": ("fr-FR", "Europe/Paris"),
    "CA": ("en-CA", "America/Toronto"),
    "AU": ("en-AU", "Australia/Sydney"),
    "TW": ("zh-TW", "Asia/Taipei"),
    "CN": ("zh-CN", "Asia/Shanghai"),
    "BR": ("pt-BR", "America/Sao_Paulo"),
    "IN": ("en-IN", "Asia/Kolkata"),
    "NL": ("nl-NL", "Europe/Amsterdam"),
    "TH": ("th-TH", "Asia/Bangkok"),
    "VN": ("vi-VN", "Asia/Ho_Chi_Minh"),
    "MY": ("ms-MY", "Asia/Kuala_Lumpur"),
    "PH": ("en-PH", "Asia/Manila"),
    "ID": ("id-ID", "Asia/Jakarta"),
}

DEFAULT_TIMEZONE = "Asia/Shanghai"


@dataclass
class ProxyConfig:
    """标准化后的代理配置。direct=True 表示直连。"""

    enabled: bool = False
    type: str = "http"
    host: str = ""
    ports: List[int] = field(default_factory=list)
    max_per_proxy: int = 20
    direct: bool = True
    ip_info_lookup: bool = True
    ip_info_timeout: int = 8

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "ProxyConfig":
        raw = raw or {}
        enabled = bool(raw.get("enabled", False))
        host = str(raw.get("host") or "").strip()
        proxy_type = str(raw.get("type") or "http").strip() or "http"
        mode = str(raw.get("mode") or "single").strip()

        if not enabled or not host:
            return cls(
                enabled=False,
                type=proxy_type,
                host=host,
                ports=[],
                direct=True,
                ip_info_lookup=bool(raw.get("ip_info_lookup", True)),
                ip_info_timeout=int(raw.get("ip_info_timeout", 8) or 8),
            )

        if mode == "pool":
            start = int(raw.get("port_start", 24000))
            end = int(raw.get("port_end", 24064))
            if end < start:
                start, end = end, start
            ports = list(range(start, end + 1))
        else:
            ports = [int(raw.get("single_port", 7890))]

        return cls(
            enabled=True,
            type=proxy_type,
            host=host,
            ports=ports,
            max_per_proxy=int(raw.get("max_per_proxy", 20) or 20),
            direct=False,
            ip_info_lookup=bool(raw.get("ip_info_lookup", True)),
            ip_info_timeout=int(raw.get("ip_info_timeout", 8) or 8),
        )

    def url_for(self, port: int) -> str:
        return f"{self.type}://{self.host}:{port}"


_ip_info_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()


def lookup_ip_info(proxy_url: str = "", timeout: int = 8) -> Dict[str, Any]:
    """查询出口 IP 的国家/时区/坐标。失败返回占位结果。带进程级缓存。

    直连时 key 用 '__direct__'。网络不可用不抛异常，返回 country='??'。
    """
    key = proxy_url or "__direct__"
    with _cache_lock:
        if key in _ip_info_cache:
            return _ip_info_cache[key]

    info: Dict[str, Any] = {"ip": "", "country": "??", "timezone": "", "loc": ""}
    try:
        import requests

        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        resp = requests.get("https://ipinfo.io/json", proxies=proxies, timeout=timeout)
        if resp.ok:
            data = resp.json()
            info = {
                "ip": data.get("ip", ""),
                "country": (data.get("country") or "??").upper(),
                "timezone": data.get("timezone", ""),
                "loc": data.get("loc", ""),
            }
    except Exception:
        pass

    with _cache_lock:
        _ip_info_cache[key] = info
    return info


def clear_ip_cache() -> None:
    with _cache_lock:
        _ip_info_cache.clear()


def resolve_timezone(info: Optional[Dict[str, Any]]) -> str:
    """出口 IP 信息 → 时区。ipinfo 返回的时区优先，其次国家映射。"""
    info = info or {}
    raw_tz = (info.get("timezone") or "").strip()
    if raw_tz and raw_tz != "UTC":
        return raw_tz
    country = (info.get("country") or "??").upper()
    mapped = LOCALE_MAP.get(country)
    return mapped[1] if mapped else DEFAULT_TIMEZONE


def resolve_geolocation(info: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """'31.22,121.45' → {'latitude':..., 'longitude':...}"""
    loc = ((info or {}).get("loc") or "").strip()
    if not loc or "," not in loc:
        return None
    try:
        lat, lng = loc.split(",", 1)
        return {"latitude": float(lat), "longitude": float(lng)}
    except Exception:
        return None
