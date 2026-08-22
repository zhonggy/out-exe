"""Resin 外部粘性代理池接入。

两种接入方式（按请求特征选择）：
- 正向代理：给浏览器/Playwright 用。认证信息 `Platform.Account:RESIN_TOKEN`，
  Resin 按 Platform+Account 组合提供粘性 IP。
- 反向代理：给框架自身的纯 Web API 请求用（如 ipinfo 出口查询）。
  URL 格式 `<resin_url>/Platform/protocol/host/path?query`，身份走 `X-Resin-Account` 头。

账号标识约定：
- 必须使用登录前就稳定的标识（本项目即账号邮箱或其前缀），同一账号永远同一标识。
- 提供 inherit_lease() 支持临时身份 → 稳定身份的租约继承（本项目登录前即有邮箱，
  运行时通常不需要，保留接口以备扩展）。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse

_IDENTITY_MODES = ("email_prefix", "email")


class Resin:
    """Resin 配置解析与调用封装。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        raw = config or {}
        self.enabled = bool(raw.get("enabled", False))
        url = str(raw.get("url") or "").strip().rstrip("/")
        self.platform = str(raw.get("platform") or "Default").strip() or "Default"
        self.identity_mode = str(raw.get("identity_mode") or "email_prefix").strip()
        if self.identity_mode not in _IDENTITY_MODES:
            self.identity_mode = "email_prefix"

        # resin_url 例：http://127.0.0.1:2260/my-token
        self.url = url
        parsed = urlparse(url)
        self.server = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else ""
        # Token 是 url path 的最后一段
        path = (parsed.path or "").strip("/")
        self.token = path.rsplit("/", 1)[-1] if path else ""

    # ---------- 状态 ----------
    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.server) and bool(self.token)

    # ---------- 身份标识 ----------
    def account_identity(self, account: str) -> str:
        """由账号推导 Resin Account 标识。

        email_prefix：取 @ 前缀（OutlookRegister 同款，登录前稳定）；
        email：完整邮箱。两种都是登录前已知标识，全生命周期一致即可。
        """
        account = (account or "").strip()
        if self.identity_mode == "email" or "@" not in account:
            return account
        return account.split("@", 1)[0]

    # ---------- 正向代理（浏览器用） ----------
    def forward_proxy_url(self, account: str) -> Optional[str]:
        """生成 requests/httpx 用的带凭证代理 URL：`scheme://Platform.Account:Token@host:port`。

        requests 的 auth= 参数是给目标站的，不会用于 CONNECT 隔壁认证（会得 407），
        代理凭证必须内嵌在 URL 里。Account 可含 @ . 等字符，故做 percent-encode；
        requests 会先 unquote 再组 Basic 头，Resin 收到的仍是原始 `Platform.Account:Token`。
        """
        opt = self.forward_proxy_option(account)
        if not opt:
            return None
        from urllib.parse import quote

        parsed = urlparse(opt["server"])
        user = quote(opt["username"], safe="")
        pwd = quote(opt["password"], safe="")
        return f"{parsed.scheme}://{user}:{pwd}@{parsed.netloc}"

    def forward_proxy_option(self, account: str) -> Optional[Dict[str, str]]:
        """生成 Playwright 的 proxy 参数。未启用返回 None。

        认证格式 `Platform.Account:Token`；Resin 以第一个 . 和最后一个 : 分割，
        因此 Account 含 @ . : 等特殊字符均安全。
        """
        if not self.usable:
            return None
        identity = self.account_identity(account)
        if not identity:
            return None
        return {
            "server": self.server,
            "username": f"{self.platform}.{identity}",
            "password": self.token,
        }

    # ---------- 反向代理（框架自身 API 请求用） ----------
    def reverse_url(self, target_url: str, account: str) -> str:
        """目标 URL → Resin 反代 URL：`<resin_url>/Platform/protocol/host/path?query`。

        protocol 只能是 http/https（代表目标服务协议），Platform 为单段路径。
        """
        if not self.usable:
            raise RuntimeError("Resin 未启用，无法构造反向代理 URL")
        parsed = urlparse(target_url)
        protocol = (parsed.scheme or "https").lower()
        # WebSocket 目标映射：ws→http、wss→https（客户端到 Resin 这段只支持 ws 拨号）
        protocol = {"ws": "http", "wss": "https"}.get(protocol, protocol)
        if protocol not in ("http", "https"):
            raise ValueError(f"反代仅支持 http/https/ws/wss 目标，收到: {parsed.scheme}")
        host = parsed.netloc
        if not host:
            raise ValueError(f"无效的目标 URL: {target_url}")
        rest = parsed.path or "/"
        if parsed.query:
            rest += f"?{parsed.query}"
        return f"{self.url}/{self.platform}/{protocol}/{host}{rest}"

    def reverse_headers(self, account: str) -> Dict[str, str]:
        """反向代理请求必须携带的身份头。"""
        return {"X-Resin-Account": self.account_identity(account)}

    # ---------- 租约继承 ----------
    def build_inherit_request(self, temp_identity: str, stable_identity: str) -> tuple[str, Dict[str, str]]:
        """构造租约继承请求的 (url, json_body)。供 requests.post(*result) 使用。"""
        url = f"{self.url}/api/v1/{self.platform}/actions/inherit-lease"
        body = {"parent_account": temp_identity, "new_account": stable_identity}
        return url, body

    def inherit_lease(self, temp_identity: str, stable_identity: str, timeout: int = 15) -> Dict[str, Any]:
        """把临时身份的历史 IP 租约继承给稳定身份。返回 Resin 响应 JSON。"""
        import requests

        url, body = self.build_inherit_request(temp_identity, stable_identity)
        resp = requests.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"ok": True, "status": resp.status_code}

    # ---------- 出口 IP 查询（走反代，带账号身份） ----------
    def lookup_exit_info(self, account: str, timeout: int = 8) -> Dict[str, Any]:
        """通过 Resin 反代查询该账号出口 IP 的地理信息，结果带缓存。"""
        cached = _exit_cache_get(account)
        if cached is not None:
            return cached
        info: Dict[str, Any] = {"ip": "", "country": "??", "timezone": "", "loc": ""}
        try:
            import requests

            url = self.reverse_url("https://ipinfo.io/json", account)
            resp = requests.get(
                url, headers=self.reverse_headers(account), timeout=timeout
            )
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
        _exit_cache_put(account, info)
        return info

    # ---------- 连通性测试 ----------
    def test_connection(self, timeout: int = 10) -> Dict[str, Any]:
        """同临时 Account 连续两次经正向代理查 ipinfo：验证连通 + 粘性。"""
        if not self.usable:
            return {"ok": False, "detail": "Resin 未启用或 URL 未配置（格式: http://host:port/token）"}
        import random
        import string

        import requests

        account = "test" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        opt = self.forward_proxy_option(account)
        proxy_url = self.forward_proxy_url(account)
        proxies = {"http": proxy_url, "https": proxy_url}

        ips, parts = [], []
        for i in range(2):
            try:
                r = requests.get(
                    "https://ipinfo.io/json",
                    proxies=proxies,
                    timeout=timeout,
                    headers={"Accept": "application/json"},
                )
                if r.status_code != 200:
                    return {"ok": False, "detail": f"Resin 返回 HTTP {r.status_code}"}
                d = r.json()
                ip, country = d.get("ip", "?"), d.get("country", "")
                ips.append(ip)
                parts.append(f"{ip} ({country})")
            except Exception as exc:
                return {
                    "ok": False,
                    "detail": f"第{i + 1}次请求失败: {exc.__class__.__name__}: {exc}",
                }
        sticky = len(ips) == 2 and ips[0] == ips[1]
        return {
            "ok": True,
            "sticky": sticky,
            "account": f"{self.platform}.{account}",
            "ip": ips[0],
            "detail": (
                f"同 Account({self.platform}.{account}) 两次出口: "
                f"{parts[0]} → {parts[1]}；" + ("粘性 OK" if sticky else "IP 变化，粘性异常")
            ),
        }

    # ---------- 报表 ----------
    def snapshot(self) -> Dict[str, Any]:
        """状态汇总（不含 token，避免泄露）。"""
        return {
            "enabled": self.enabled,
            "usable": self.usable,
            "server": self.server,
            "platform": self.platform,
            "identity_mode": self.identity_mode,
        }


# ---------- 出口信息缓存（按账号标识） ----------
_exit_cache: Dict[str, Dict[str, Any]] = {}
_exit_lock = threading.Lock()


def _exit_cache_get(identity: str) -> Optional[Dict[str, Any]]:
    with _exit_lock:
        return _exit_cache.get(identity)


def _exit_cache_put(identity: str, info: Dict[str, Any]) -> None:
    with _exit_lock:
        _exit_cache[identity] = info


def clear_exit_cache() -> None:
    with _exit_lock:
        _exit_cache.clear()


# ---------- 进程级单例 ----------
_instance: Optional[Resin] = None
_lock = threading.Lock()


def get_resin(config: Optional[Dict[str, Any]] = None) -> Resin:
    global _instance
    with _lock:
        if _instance is None:
            if config is None:
                from config import load_config

                config = load_config().section("resin")
            _instance = Resin(config)
        return _instance


def reset_resin() -> None:
    global _instance
    with _lock:
        _instance = None
