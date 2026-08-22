"""页面状态检测器。

职责：只读页面，判断当前处于哪个阶段，不做任何操作。
返回值为状态字符串，供 login_flow 决策、account.status 映射账号状态。

可能的状态：
    login_email / login_password / account_type / captcha / verify_required
    protect_account / kmsi / passkey / mailbox / password_wrong
    account_not_found / account_locked / password_login_blocked
    risk_blocked / unknown
"""

from __future__ import annotations

import time
from typing import List, Optional

# ---------- 选择器 ----------
# 2025+ 新版登录页：#usernameEntry / [data-testid=primaryButton]
# 旧版保留：#i0116 / #idSIButton9（部分区域/旧 cookie 环境仍会出现）
EMAIL_SELECTORS = ("#usernameEntry", "#i0116")
EMAIL_NEXT_SELECTORS = (
    '[data-testid="primaryButton"]',
    "#idSIButton9",
    'input[type="submit"]',
)
PASSWORD_SELECTORS = ("#passwordEntry", "#i0118", 'input[type="password"]')
PRIMARY_BUTTON = '[data-testid="primaryButton"],input[data-testid="primaryButton"],input[type="submit"]'
MSA_TILE_SELECTOR = "#msaTile"
USERNAME_ERROR_SELECTORS = ("#usernameError", '[data-testid="usernameError"]')
PASSWORD_ERROR_SELECTORS = ("#passwordError", '[data-testid="passwordError"]')

# 验证码嵌套 iframe：外层 title 中文/英文两种
CAPTCHA_FRAME_SELECTORS = (
    'iframe[title="验证质询"]',
    'iframe[title="Verification challenge"]',
    'iframe[title*="challenge"]',
)
CAPTCHA_INNER_FRAME = 'iframe[style*="display: block"]'
CAPTCHA_TARGET_SELECTORS = (
    '[aria-label="可访问性挑战"]',
    '[aria-label="Accessibility challenge"]',
    "circle",
    "ellipse",
    "svg circle",
    "svg ellipse",
    '[role="button"]',
    "svg",
)
CAPTCHA_BTN2_SELECTORS = (
    '[aria-label="再次按下"]',
    '[aria-label*="再次"]',
    '[aria-label*="按下"]',
    '[aria-label="Press again"]',
    '[aria-label*="Press"]',
)

# ---------- 文案 ----------
MAILBOX_URL_HINTS = ("outlook.live.com/mail", "outlook.office.com/mail", "outlook.com/mail")

PASSWORD_WRONG_TEXTS = (
    "此密码不是你的 Microsoft 帐户的正确密码",
    "此密码不是你的 Microsoft 账户的正确密码",
    "你的帐户或密码不正确",
    "帐户或密码不正确",
    "账户或密码不正确",
    "This password is incorrect",
    "Your account or password is incorrect",
)
PASSWORD_BLOCKED_TEXTS = (
    "密码登录不可用",
    "请尝试其他方法",
    "Password login is not available",
    "Try another way",
    "Try a different way",
    "Sign-in method isn't available",
)
ACCOUNT_NOT_FOUND_TEXTS = (
    "找不到使用该用户名的帐户",
    "找不到使用该用户名的账户",
    "We couldn't find an account with that username",
    "That Microsoft account doesn't exist",
)
# 帐户查询瞬时失败：页面提示“点下一步重试”，可自动重试
ACCOUNT_LOOKUP_ERROR_TEXTS = (
    "查找帐户时遇到问题",
    "查找账户时遇到问题",
    "尝试查找您的帐户时遇到问题",
    "We're having trouble finding your account",
    "There seems to be a problem finding your account",
)
ACCOUNT_LOCKED_TEXTS = (
    "帐户已被锁定",
    "账户已被锁定",
    "帐户已锁定",
    "账户已锁定",
    "你的帐户已暂时锁定",
    "已锁定此帐户",
    "违反 Microsoft 服务协议",
    "违反 Microsoft 服务协定",
    "Your account has been locked",
    "account has been temporarily suspended",
    "violating the Microsoft Services Agreement",
    "help us protect your account",
    "帮助我们保护你的帐户",
)
VERIFY_REQUIRED_TEXTS = (
    "验证你的身份",
    "帮助我们确认是你本人",
    "输入你收到的代码",
    "Verify your identity",
    "Help us protect your account",
    "Enter the code",
    "输入验证码",
)
# 邮箱验证页：提供「使用密码」绕过入口，应优先点绕过而非等待人工
EMAIL_VERIFY_OFFER_TEXTS = (
    "验证你的电子邮件",
    "验证你的邮箱",
    "我们将向",
    "发送验证码",
    "Verify your email",
    "We'll send a code",
    "We need to verify your email",
)
RISK_BLOCKED_TEXTS = (
    "一些异常活动",
    "此站点正在维护",
    "unusual activity",
    "site is under maintenance",
)
ACCOUNT_TYPE_TEXTS = (
    "哪种类型的帐户",
    "哪种类型的账户",
    "which type of account",
    "工作或学校帐户",
    "工作或学校账户",
    "Work or school account",
)
PROTECT_ACCOUNT_TEXTS = (
    "保护你的帐户",
    "保护你的账户",
    "添加安全信息",
    "Protect your account",
    "Add security info",
)
KMSI_TEXTS = ("保持登录状态", "请勿显示此消息", "Stay signed in", "Don't show this again")
PASSKEY_TEXTS = ("正在设置密钥", "通行密钥", "安全窗口", "passkey", "Set up a passkey")


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def url_of(page) -> str:
    return _safe(lambda: page.url or "", "") or ""


def text_exists(page, text: str) -> bool:
    return bool(_safe(lambda: page.get_by_text(text, exact=False).count() > 0, False))


def any_text(page, texts) -> Optional[str]:
    for t in texts:
        if text_exists(page, t):
            return t
    return None


def locator_visible(page, selector: str) -> bool:
    def check():
        loc = page.locator(selector)
        return loc.count() > 0 and loc.first.is_visible()

    return bool(_safe(check, False))


def visible_password_selector(page) -> Optional[str]:
    for sel in PASSWORD_SELECTORS:
        if locator_visible(page, sel):
            return sel
    return None


def visible_email_selector(page) -> Optional[str]:
    for sel in EMAIL_SELECTORS:
        if locator_visible(page, sel):
            return sel
    return None


class PageDetector:
    """页面状态检测。所有方法均只读。"""

    def __init__(self, page, logger=None):
        self.page = page
        self.log = logger

    # ---------- 单项判定 ----------
    def is_mailbox(self) -> bool:
        url = url_of(self.page).lower()
        return any(h in url for h in MAILBOX_URL_HINTS)

    def has_email_input(self) -> bool:
        return visible_email_selector(self.page) is not None

    def displayed_account(self) -> str:
        """读取密码页/确认页上显示的已记住账号（含 @ 的文本）。找不到返回空串。"""
        for sel in (
            ".identity",
            "#displaySign",
            '#displaySign\, [data-test-id="identity"]',
            '[data-testid="identity"]',
            "#loginHint",
            ".table-cell .identity",
        ):
            try:
                loc = self.page.locator(sel)
                if loc.count() > 0:
                    text = (loc.first.text_content(timeout=1500) or "").strip()
                    if "@" in text:
                        return text.replace("'", "")
            except Exception:
                continue
        return ""

    def has_password_input(self) -> bool:
        return visible_password_selector(self.page) is not None

    def has_captcha(self) -> bool:
        """外层验证码 iframe 是否存在（内部是否就绪由 captcha 模块判断）。"""
        for sel in CAPTCHA_FRAME_SELECTORS:
            if _safe(lambda s=sel: self.page.locator(s).count() > 0, False):
                return True
        return False

    def captcha_frame(self):
        """返回 (外层 frame_locator, 内层 frame_locator)，找不到返回 (None, None)。"""
        for sel in CAPTCHA_FRAME_SELECTORS:
            try:
                if self.page.locator(sel).count() == 0:
                    continue
                frame1 = self.page.frame_locator(sel)
                if frame1.locator("iframe").count() > 0:
                    return frame1, frame1.frame_locator(CAPTCHA_INNER_FRAME)
                return frame1, frame1
            except Exception:
                continue
        return None, None

    def is_password_wrong(self) -> bool:
        if any_text(self.page, PASSWORD_WRONG_TEXTS):
            return True
        for sel in PASSWORD_ERROR_SELECTORS:
            if locator_visible(self.page, sel):
                return True
        return False

    def is_password_blocked(self) -> bool:
        return any_text(self.page, PASSWORD_BLOCKED_TEXTS) is not None

    def is_account_not_found(self) -> bool:
        if any_text(self.page, ACCOUNT_NOT_FOUND_TEXTS):
            return True
        for sel in USERNAME_ERROR_SELECTORS:
            if locator_visible(self.page, sel):
                return True
        return False

    def is_account_lookup_error(self) -> bool:
        """帐户查询瞬时失败页（提示点下一步重试）。"""
        return any_text(self.page, ACCOUNT_LOOKUP_ERROR_TEXTS) is not None

    def is_account_locked(self) -> bool:
        return any_text(self.page, ACCOUNT_LOCKED_TEXTS) is not None

    def is_email_verify_offer(self) -> bool:
        """邮箱验证页（带「使用密码」绕过入口）。"""
        if any_text(self.page, EMAIL_VERIFY_OFFER_TEXTS) is None:
            return False
        # 页面上必须同时存在「使用密码」入口，否则就是强制验证，归入 verify_required
        return any_text(self.page, ("使用密码", "Use your password", "Use password instead")) is not None

    def is_verify_required(self) -> bool:
        return any_text(self.page, VERIFY_REQUIRED_TEXTS) is not None

    def is_risk_blocked(self) -> bool:
        return any_text(self.page, RISK_BLOCKED_TEXTS) is not None

    def is_account_type_page(self) -> bool:
        # 真·帐户类型选择页：有 msaTile 磁贴，或明确问「哪种类型」；
        # 不能只看「个人帐户」字样 —— 普通登录页也有该文案（如「对于个人帐户」）
        if locator_visible(self.page, MSA_TILE_SELECTOR):
            return True
        return any_text(self.page, ACCOUNT_TYPE_TEXTS) is not None

    def is_protect_account(self) -> bool:
        return any_text(self.page, PROTECT_ACCOUNT_TEXTS) is not None

    def is_kmsi(self) -> bool:
        return any_text(self.page, KMSI_TEXTS) is not None

    def is_passkey(self) -> bool:
        url = url_of(self.page).lower()
        if "fido" in url:
            return True
        return any_text(self.page, PASSKEY_TEXTS) is not None

    # ---------- 综合状态 ----------
    def detect(self) -> str:
        """按优先级返回当前页面状态。终态判定优先于输入页判定。"""
        if self.is_mailbox():
            return "mailbox"
        if self.has_captcha():
            # 验证码优先于风控/锁定类判定：这些页面常带“异常活动”字样但实际可按压通过
            return "captcha"
        if self.is_risk_blocked():
            return "risk_blocked"
        if self.is_account_not_found():
            return "account_not_found"
        if self.is_account_lookup_error():
            return "account_lookup_error"
        if self.is_password_wrong():
            return "password_wrong"
        if self.is_account_locked():
            return "account_locked"
        if self.is_password_blocked():
            return "password_login_blocked"
        if self.is_email_verify_offer():
            return "email_verify_offer"
        if self.is_verify_required():
            return "verify_required"
        if self.is_passkey():
            return "passkey"
        if self.is_protect_account():
            return "protect_account"
        if self.is_kmsi():
            return "kmsi"
        if self.is_account_type_page():
            return "account_type"
        if self.has_password_input():
            if self.has_email_input() and not self.displayed_account():
                # 两框同时可见且没有显示已记住账号：这是邮箱页
                # （microsoftonline 的 KO 页面会在邮箱步骤残留可见的密码框）
                return "login_email"
            return "login_password"
        if self.has_email_input():
            return "login_email"
        return "unknown"

    def wait_for_any(
        self,
        wanted: List[str],
        timeout_ms: int = 30000,
        poll_ms: int = 500,
        ignore: Optional[List[str]] = None,
    ) -> str:
        """轮询等待，直到 detect() 落在 wanted 中；超时返回最后一次状态。"""
        ignore = ignore or []
        deadline = time.time() + timeout_ms / 1000
        state = "unknown"
        while time.time() < deadline:
            state = self.detect()
            if state in wanted and state not in ignore:
                return state
            try:
                self.page.wait_for_timeout(poll_ms)
            except Exception:
                time.sleep(poll_ms / 1000)
        return state

    def snapshot(self) -> dict:
        """调试快照。"""
        return {
            "url": url_of(self.page),
            "state": self.detect(),
            "email_input": self.has_email_input(),
            "password_input": self.has_password_input(),
            "captcha": self.has_captcha(),
        }
