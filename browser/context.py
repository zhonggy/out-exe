"""浏览器上下文参数构建：启动参数、反检测、地区/时区/视口/指纹。"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

# 反检测 + 关闭干扰弹窗的启动参数（来自 OutlookRegister 实战积累）
BASE_ARGS: List[str] = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-autofill-keyboard-accessory-view",
    # WebRTC 泄漏真实 IP 防护
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-non-proxied-udp",
    # 抑制 Windows Hello / Passkey / 安全密钥系统弹窗
    "--disable-webauthn",
    "--disable-features=WebAuthentication,WebAuthenticationConditionalUI,"
    "WebAuthenticationCable,WebAuthenticationHybridTransport,"
    "WebAuthenticationPasskeysUI,Translate,OptimizationHints,MediaRouter,"
    "DialMediaRouteProvider,AutofillServerCommunication,"
    "PasswordManagerOnboarding,PasswordImport,BiometricAuthenticationInSettings",
    "--disable-save-password-bubble",
    "--disable-password-manager-reauthentication",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
]


def build_args(
    locale: str = "zh-CN",
    timezone: str = "Asia/Shanghai",
    fingerprint_seed: Optional[int] = None,
    fingerprint_platform: str = "windows",
    fingerprint_brand: str = "Chrome",
    extra: Optional[List[str]] = None,
) -> List[str]:
    """组装 Chromium 启动参数。

    --fingerprint 系列仅 fingerprint-chromium 内核识别，普通 Chromium 会忽略。
    """
    args = [
        f"--lang={locale}",
        f"--accept-lang={locale},{locale.split('-')[0]},en-US,en",
        *BASE_ARGS,
    ]
    if timezone:
        # 直连模式下 timezone 为空：不注入 --timezone，跟随系统真实时区
        args.append(f"--timezone={timezone}")
    if fingerprint_seed is not None:
        args.append(f"--fingerprint={fingerprint_seed}")
        if fingerprint_platform:
            args.append(f"--fingerprint-platform={fingerprint_platform}")
        if fingerprint_brand:
            args.append(f"--fingerprint-brand={fingerprint_brand}")
    if extra:
        args.extend(extra)
    return args


def random_viewport(
    widths: Optional[List[int]] = None, heights: Optional[List[int]] = None
) -> Dict[str, int]:
    widths = widths or [1366, 1440, 1536, 1680, 1920]
    heights = heights or [768, 864, 900, 1050, 1080]
    return {"width": random.choice(widths), "height": random.choice(heights)}


def build_context_options(
    locale: str = "zh-CN",
    timezone: str = "Asia/Shanghai",
    viewport: Optional[Dict[str, int]] = None,
    geolocation: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """BrowserContext / launch_persistent_context 的公共参数。"""
    opts: Dict[str, Any] = {
        "locale": locale,
        "viewport": viewport or random_viewport(),
    }
    if timezone:
        opts["timezone_id"] = timezone
    if geolocation:
        opts["geolocation"] = geolocation
        opts["permissions"] = ["geolocation"]
    return opts


def build_proxy_option(proxy_url: str) -> Optional[Dict[str, str]]:
    """proxy://host:port → Playwright proxy 参数。空串返回 None（直连）。"""
    if not proxy_url:
        return None
    return {"server": proxy_url, "bypass": "localhost,127.0.0.1"}
