"""proxy 包：代理配置解析与加权选择。"""

from .manager import ProxyManager, get_proxy_manager, reset_proxy_manager
from .provider import (
    DEFAULT_TIMEZONE,
    LOCALE_MAP,
    ProxyConfig,
    clear_ip_cache,
    lookup_ip_info,
    resolve_geolocation,
    resolve_timezone,
)

__all__ = [
    "DEFAULT_TIMEZONE",
    "LOCALE_MAP",
    "ProxyConfig",
    "ProxyManager",
    "clear_ip_cache",
    "get_proxy_manager",
    "lookup_ip_info",
    "reset_proxy_manager",
    "resolve_geolocation",
    "resolve_timezone",
]
