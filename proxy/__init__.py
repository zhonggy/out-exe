"""proxy 包：代理配置解析与加权选择、Resin 粘性代理池接入。"""

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
from .resin import Resin, clear_exit_cache, get_resin, reset_resin

__all__ = [
    "DEFAULT_TIMEZONE",
    "LOCALE_MAP",
    "ProxyConfig",
    "ProxyManager",
    "Resin",
    "clear_exit_cache",
    "clear_ip_cache",
    "get_proxy_manager",
    "get_resin",
    "lookup_ip_info",
    "reset_proxy_manager",
    "reset_resin",
    "resolve_geolocation",
    "resolve_timezone",
]
