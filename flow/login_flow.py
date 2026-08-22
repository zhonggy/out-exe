"""登录流程（规划文档第 6 节）。

状态机：
    CREATED → BROWSER_STARTED → LOGIN_PAGE → USERNAME_INPUT → PASSWORD_INPUT
    → CHECK_STATUS → [WAIT_VERIFY → VERIFIED] → COMPLETED

每一步都写 checkpoint，中断后可从最后阶段恢复。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from account import (
    StatusVerdict,
    verdict_from_exception,
    verdict_from_page_state,
)
from database import AccountStatus, FlowStage

from .action import PageAction
from .base import BaseFlow, FlowResult, register_flow
from .captcha import CaptchaSolver
from .detector import PageDetector, any_text

# 登录页可能落到的稳定状态
_ENTRY_STATES = (
    "login_email",
    "login_password",
    "account_type",
    "mailbox",
    "captcha",
    "email_verify_offer",
    "verify_required",
    "protect_account",
    "account_unblocked",
    "kmsi",
    "passkey",
    "account_not_found",
    "account_lookup_error",
    "password_wrong",
    "account_locked",
    "password_login_blocked",
    "risk_blocked",
)


def page_visible_text(page) -> str:
    """抓取页面可见文本（失败现场存档用）。"""
    try:
        return page.evaluate(
            """() => {
                const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                const out = [];
                while (walk.nextNode()) {
                    const t = walk.currentNode.textContent.trim();
                    if (!t) continue;
                    const el = walk.currentNode.parentElement;
                    if (el && (el.offsetWidth || el.offsetHeight)) out.push(t);
                }
                return [...new Set(out)].join(' | ');
            }"""
        ) or ""
    except Exception:
        return ""


@register_flow
class LoginFlow(BaseFlow):
    """Outlook 账号密码登录流程。"""

    name = "login"

    def __init__(self, session, cfg, logger=None, checkpoint=None, proxy_manager=None):
        super().__init__(session, cfg, logger, checkpoint, proxy_manager)
        self.detector = PageDetector(self.page, logger=logger)
        self.action = PageAction(self.page, logger=logger)
        self.login_url = str(cfg.get("flow.login_url", "https://login.live.com/"))
        self.wait_verify_timeout = int(cfg.get("flow.wait_verify_timeout", 300))

    # ---------- 主流程 ----------
    def run(self, account: str = "", password: str = "", **kwargs: Any) -> FlowResult:
        if not account:
            return FlowResult(False, FlowStage.FAILED.value, message="缺少账号")
        self._last_account = account

        try:
            return self._run_inner(account, password)
        except Exception as exc:
            if self.log:
                self.log.exception("login_flow", "流程异常", exc)
            verdict = verdict_from_exception(exc)
            self.mark(FlowStage.FAILED, error=str(exc))
            self.feedback_proxy(False)
            return FlowResult(
                False,
                FlowStage.FAILED.value,
                verdict=verdict,
                message=verdict.reason,
                retryable=verdict.retryable,
            )

    def _run_inner(self, account: str, password: str) -> FlowResult:
        self.mark(FlowStage.BROWSER_STARTED, url=self.login_url)

        # ① 打开登录页
        if not self.action.goto(self.login_url):
            verdict = StatusVerdict(AccountStatus.FAILED.value, "登录页打开失败", retryable=True)
            self.feedback_proxy(False, penalty=2)
            return self._fail(verdict, FlowStage.LOGIN_PAGE)
        state = self.detector.wait_for_any(list(_ENTRY_STATES), timeout_ms=30000)
        self.mark(FlowStage.LOGIN_PAGE, state=state)
        if self.log:
            self.log.info("login_page", f"入口状态={state}")

        # 已有登录态（persistent profile 复用）
        if state == "mailbox":
            return self._success("已有登录态，直接进入邮箱")

        # ② 输入邮箱。保险：若误判为帐户类型页但邮箱框可见，仍优先填邮箱
        if state == "login_email" or (state == "account_type" and self.detector.has_email_input()):
            if not self.action.fill_email(account):
                verdict = StatusVerdict(AccountStatus.FAILED.value, "邮箱提交失败", retryable=True)
                return self._fail(verdict, FlowStage.USERNAME_INPUT)
            self.mark(FlowStage.USERNAME_INPUT, account=account)
            state = self.detector.wait_for_any(list(_ENTRY_STATES), timeout_ms=30000, ignore=["login_email"])
            if self.log:
                self.log.info("username_input", f"提交邮箱后状态={state}")

        # 帐户类型选择页：选个人帐户
        if state == "account_type":
            state = self._resolve_account_type()

        # ③ 输入密码
        if state == "login_password":
            # 复用会话可能记住其它邮箱直接进密码页：核对身份，不符则回到邮箱输入
            shown = self.detector.displayed_account()
            if shown and account and shown.lower() != account.lower():
                if self.log:
                    self.log.warn(
                        "identity_mismatch",
                        f"页面记住的是 {shown}，与目标 {account} 不符，切换账号",
                    )
                self.action.click_text(
                    ("使用其他帐户", "使用其他账户", "使用其他账号", "Sign in with another account"),
                    timeout_ms=4000,
                )
                state = self.detector.wait_for_any(
                    list(_ENTRY_STATES), timeout_ms=20000, ignore=["login_password"]
                )
                if state == "login_email":
                    if not self.action.fill_email(account):
                        verdict = StatusVerdict(AccountStatus.FAILED.value, "邮箱提交失败", retryable=True)
                        return self._fail(verdict, FlowStage.USERNAME_INPUT)
                    self.mark(FlowStage.USERNAME_INPUT, account=account)
                    state = self.detector.wait_for_any(list(_ENTRY_STATES), timeout_ms=30000, ignore=["login_email"])

        if state == "login_password":
            if not password:
                verdict = StatusVerdict(AccountStatus.FAILED.value, "缺少密码")
                return self._fail(verdict, FlowStage.PASSWORD_INPUT)
            self.action.fill_password(password)
            self.mark(FlowStage.PASSWORD_INPUT)
            state = self.detector.wait_for_any(
                list(_ENTRY_STATES), timeout_ms=40000, ignore=["login_password"]
            )
            if self.log:
                self.log.info("password_input", f"提交密码后状态={state}")

        # ④ 检测账户状态
        self.mark(FlowStage.CHECK_STATUS, state=state)
        return self._handle_state(state, account, password)

    # ---------- 状态处理 ----------
    def _handle_state(self, state: str, account: str, password: str, depth: int = 0) -> FlowResult:
        """按状态分派处理。depth 限制递归，避免页面来回跳导致死循环。"""
        if depth > 6:
            verdict = StatusVerdict(AccountStatus.FAILED.value, f"状态处理超过深度限制（最后={state}）", retryable=True)
            return self._fail(verdict, FlowStage.CHECK_STATUS)

        if state == "mailbox":
            return self._success("登录成功")

        if state == "captcha":
            return self._handle_captcha(account, password, depth)

        if state == "verify_required":
            return self._handle_wait_verify(account, password, depth)

        if state == "account_unblocked":
            # 人机验证通过、帐户已解除阻止：任务目标已达成，直接判定成功
            return self._success("人机验证通过，帐户已取消阻止")

        if state in ("kmsi", "passkey", "protect_account"):
            # 非阻塞拦截页：关掉继续
            handled = self._dismiss_intercept(state)
            nxt = self.detector.wait_for_any(list(_ENTRY_STATES), timeout_ms=20000, ignore=[state] if handled else [])
            return self._handle_state(nxt, account, password, depth + 1)

        if state == "account_locked":
            # 锁定页若带人机验证入口（“下一步完成人机验证”），点下一步尝试解锁；
            # 纯锁定页才是终态
            if any_text(self.page, ("人机验证", "human verification", "Human verification", "下一步")):
                nxt = self._attempt_unlock()
                return self._handle_state(nxt, account, password, depth + 1)
            return self._fail(verdict_from_page_state("account_locked"), FlowStage.CHECK_STATUS)

        if state == "account_lookup_error":
            # 查询瞬时失败：按页面提示重试点下一步，最多 2 次
            nxt = self._retry_lookup_error(account)
            if nxt is None:
                verdict = StatusVerdict(
                    AccountStatus.FAILED.value, "帐户查询失败（重试后仍报错，建议换代理或清理 profile）",
                    retryable=True,
                )
                self.feedback_proxy(False, penalty=2)
                return self._fail(verdict, FlowStage.USERNAME_INPUT)
            return self._handle_state(nxt, account, password, depth + 1)

        if state == "email_verify_offer":
            # 邮箱验证页：点「使用密码」绕过，进入密码页
            nxt = self._bypass_email_verify()
            return self._handle_state(nxt, account, password, depth + 1)

        if state == "account_type":
            nxt = self._resolve_account_type()
            return self._handle_state(nxt, account, password, depth + 1)

        if state == "login_password" and password:
            # 页面回退到密码页（如输入被吞）：再试一次
            self.action.fill_password(password)
            nxt = self.detector.wait_for_any(list(_ENTRY_STATES), timeout_ms=30000, ignore=["login_password"])
            return self._handle_state(nxt, account, password, depth + 1)

        if state == "risk_blocked":
            # 软判定：验证页加载前常短暂出现“异常活动”字样，等几秒复查，
            # 弹出验证码则走按压，仍无变化才按风控失败
            self.action.wait_random(4000, 6000)
            redetected = self.detector.detect()
            if redetected == "captcha":
                if self.log:
                    self.log.info("risk_recheck", "复查发现验证码，改走按压流程")
                return self._handle_captcha(account, password, depth + 1)
            if redetected not in ("risk_blocked", "unknown"):
                return self._handle_state(redetected, account, password, depth + 1)
            self.feedback_proxy(False, penalty=4)
            return self._fail(verdict_from_page_state("risk_blocked"), FlowStage.CHECK_STATUS)

        verdict = verdict_from_page_state(state)
        if verdict.status == AccountStatus.OK.value:
            return self._success(verdict.reason)
        return self._fail(verdict, FlowStage.CHECK_STATUS)

    def _handle_captcha(self, account: str, password: str, depth: int) -> FlowResult:
        """验证码：进入 WAIT_VERIFY，调用按压模块。"""
        self.mark(FlowStage.WAIT_VERIFY, kind="captcha")
        solver = CaptchaSolver(
            self.page,
            logger=self.log,
            max_retries=int(self.cfg.get("flow.max_captcha_retries", 3)),
            strategy=int(self.cfg.get("flow.captcha_strategy", 0)),
            screenshot_dir=str(self.cfg.path_of("logger.dir", "logs") / "captcha"),
            screenshot_on_fail=bool(self.cfg.get("flow.captcha_screenshot", True)),
            success_check=self._captcha_passed,
            manual_timeout=self.wait_verify_timeout,
        )
        result = solver.solve()

        if not result.success:
            self.feedback_proxy(False, penalty=4 if result.ip_should_be_penalized else 0)
            verdict = StatusVerdict(
                AccountStatus.WAIT_VERIFY.value, f"验证码未通过({result.reason})", retryable=True
            )
            return self._fail(verdict, FlowStage.WAIT_VERIFY)

        self.mark(FlowStage.VERIFIED, kind="captcha")
        nxt = self.detector.wait_for_any(list(_ENTRY_STATES), timeout_ms=30000, ignore=["captcha"])
        return self._handle_state(nxt, account, password, depth + 1)

    def _captcha_passed(self) -> bool:
        """验证码通过判据：已进邮箱，或已离开验证码页进入其它稳定状态。"""
        if self.detector.is_mailbox():
            return True
        state = self.detector.detect()
        return state in ("kmsi", "protect_account", "passkey", "login_password")

    def _handle_wait_verify(self, account: str, password: str, depth: int) -> FlowResult:
        """需要人工介入的安全验证（短信/邮件验证码等）：等待人工完成或超时。"""
        self.mark(FlowStage.WAIT_VERIFY, kind="manual_verify")
        if self.log:
            self.log.warn(
                "wait_verify",
                f"需要安全验证，等待人工完成（最多 {self.wait_verify_timeout}s）",
            )
        deadline = time.time() + self.wait_verify_timeout
        while time.time() < deadline:
            self.action.wait(2000)
            state = self.detector.detect()
            if state == "mailbox":
                self.mark(FlowStage.VERIFIED, kind="manual_verify")
                return self._success("人工验证完成，已进入邮箱")
            if state not in ("verify_required", "unknown"):
                self.mark(FlowStage.VERIFIED, kind="manual_verify", state=state)
                return self._handle_state(state, account, password, depth + 1)
        verdict = StatusVerdict(AccountStatus.WAIT_VERIFY.value, "安全验证等待超时")
        return self._fail(verdict, FlowStage.WAIT_VERIFY)

    def _bypass_email_verify(self) -> str:
        """邮箱验证页点「使用密码」绕过，返回下一个状态。"""
        if self.log:
            self.log.info("email_verify", "检测到邮箱验证页，尝试「使用密码」绕过")
        if not self.action.click_use_password():
            # 兑底：再试一次带更多文案的点击
            self.action.click_text(
                ("使用密码", "使用密码登录", "Use your password", "Use password instead"),
                timeout_ms=4000,
            )
            self.action.wait_random(1000, 2000)
        return self.detector.wait_for_any(
            list(_ENTRY_STATES), timeout_ms=25000, ignore=["email_verify_offer"]
        )

    def _attempt_unlock(self) -> str:
        """锁定页点「下一步」进入人机验证，返回下一个状态（通常是 captcha）。"""
        if self.log:
            self.log.warn("unlock", "帐户被锁但带人机验证入口，尝试解锁")
        self.action.wait_random(1000, 2500)
        clicked = self.action.click_text(("下一步", "Next"), timeout_ms=5000)
        if not clicked:
            self.action.submit_primary()
        return self.detector.wait_for_any(
            list(_ENTRY_STATES), timeout_ms=30000, ignore=["account_locked"]
        )

    def _retry_lookup_error(self, account: str, max_retry: int = 2) -> Optional[str]:
        """“查找帐户时遇到问题”页：点下一步重试，成功返回新状态，失败返回 None。"""
        if self.log:
            self.log.warn("lookup_error", "帐户查询失败，按页面提示重试")
        for i in range(max_retry):
            self.action.wait_random(1500, 3000)
            clicked = self.action.click_text(("下一步", "Next", "提交"), timeout_ms=4000)
            if not clicked:
                self.action.submit_primary()
            state = self.detector.wait_for_any(
                list(_ENTRY_STATES), timeout_ms=20000, ignore=["account_lookup_error", "login_email"]
            )
            if state not in ("account_lookup_error", "unknown"):
                if self.log:
                    self.log.ok("lookup_error", f"重试成功，进入 {state}")
                return state
            if state == "login_email":
                # 回到邮箱页：重新填入再提交一次
                self.action.fill_email(account)
                state = self.detector.wait_for_any(
                    list(_ENTRY_STATES), timeout_ms=20000, ignore=["account_lookup_error"]
                )
                if state not in ("account_lookup_error", "unknown"):
                    return state
            if self.log:
                self.log.warn("lookup_error", f"第 {i + 1} 次重试后仍失败（state={state}）")
        return None

    def _dismiss_intercept(self, state: str) -> bool:
        """处理 KMSI / Passkey / 保护帐户 三类拦截页。"""
        if self.log:
            self.log.info("intercept", f"处理拦截页 state={state}")
        if state == "kmsi":
            # 「保持登录状态」选是，减少后续验证
            if self.action.click_text(("是", "Yes"), timeout_ms=3000):
                self.action.wait_random(1000, 2000)
                return True
            return self.action.submit_primary()
        clicked = self.action.dismiss_dialogs()
        if clicked:
            return True
        # 「保护帐户」页可能只有主按钮可跳过
        return self.action.click_text(("以后再说", "跳过", "Skip for now", "Not now"), 3000) is not None

    def _resolve_account_type(self) -> str:
        """帐户类型页选择个人帐户（勿点工作/学校帐户）。"""
        if self.log:
            self.log.info("account_type", "选择个人帐户")
        for selector in ("#msaTile", "#msaTileTitle"):
            if self.action.click_if_visible(selector, timeout_ms=3000):
                self.action.wait_random(1200, 2200)
                break
        else:
            self.action.click_text(("个人帐户", "个人账户", "Personal account"), 3000)
            self.action.wait_random(1200, 2200)
        return self.detector.wait_for_any(list(_ENTRY_STATES), timeout_ms=25000, ignore=["account_type"])

    # ---------- 结果 ----------
    def _save_failure_evidence(self, verdict: StatusVerdict) -> None:
        """失败现场：截图到 logs/fails/，页面文本与状态写入同名 .txt。"""
        try:
            fail_dir = self.cfg.path_of("logger.dir", "logs") / "fails"
            fail_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            acc = getattr(self, "_last_account", "") or "unknown"
            base = fail_dir / f"fail_{acc}_{stamp}"
            try:
                self.page.screenshot(path=str(base) + ".png")
            except Exception:
                pass
            det = PageDetector(self.page)
            snap = det.snapshot()
            shown = det.displayed_account()
            text = page_visible_text(self.page)
            (base.with_suffix(".txt")).write_text(
                f"verdict={verdict.status} reason={verdict.reason}\n"
                f"snapshot={snap}\nshown_account={shown!r}\n"
                f"--- visible text ---\n{text[:2000]}\n",
                encoding="utf-8",
            )
            if self.log:
                self.log.info("fail_evidence", f"已保存失败现场 {base}.png/.txt")
        except Exception:
            pass

    def _success(self, message: str) -> FlowResult:
        self.mark(FlowStage.COMPLETED, message=message)
        self.feedback_proxy(True)
        if self.log:
            self.log.ok("login_done", message)
        url = ""
        try:
            url = self.page.url
        except Exception:
            pass
        return FlowResult(
            True,
            FlowStage.COMPLETED.value,
            verdict=StatusVerdict(AccountStatus.OK.value, message),
            message=message,
            data={"url": url},
        )

    def _fail(self, verdict: StatusVerdict, stage: FlowStage) -> FlowResult:
        self.mark(FlowStage.FAILED, reason=verdict.reason, at=stage.value)
        if verdict.status != AccountStatus.WAIT_VERIFY.value:
            self.feedback_proxy(False)
        if self.log:
            self.log.fail("login_done", f"{verdict.status}: {verdict.reason}")
        # 留证据：截图 + 页面快照，便于排查误判
        self._save_failure_evidence(verdict)
        return FlowResult(
            False,
            stage.value,
            verdict=verdict,
            message=verdict.reason,
            retryable=verdict.retryable,
        )


def run_login(
    session,
    cfg,
    account: str,
    password: str,
    logger=None,
    checkpoint=None,
    proxy_manager=None,
) -> FlowResult:
    """便捷入口。"""
    flow = LoginFlow(session, cfg, logger=logger, checkpoint=checkpoint, proxy_manager=proxy_manager)
    return flow.run(account=account, password=password)
