"""账号状态判定：把页面/流程结果映射为 AccountStatus，并给出建议动作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from database import AccountStatus


@dataclass
class StatusVerdict:
    """状态判定结果。retryable=True 表示换代理重试可能成功。"""

    status: str
    reason: str = ""
    retryable: bool = False

    @property
    def success(self) -> bool:
        return self.status == AccountStatus.OK.value


# 页面状态 → 账号状态
_PAGE_MAP = {
    "mailbox": StatusVerdict(AccountStatus.OK.value, "已进入邮箱"),
    "password_wrong": StatusVerdict(AccountStatus.PASSWORD_WRONG.value, "密码错误"),
    "account_locked": StatusVerdict(AccountStatus.LOCKED.value, "账号被锁定/需要解锁"),
    "account_not_found": StatusVerdict(AccountStatus.NOT_FOUND.value, "账号不存在"),
    "password_login_blocked": StatusVerdict(
        AccountStatus.FAILED.value, "密码登录不可用（需其他验证方式）"
    ),
    "captcha": StatusVerdict(AccountStatus.WAIT_VERIFY.value, "遇到验证码", retryable=True),
    "account_lookup_error": StatusVerdict(
        AccountStatus.FAILED.value, "帐户查询瞬时失败（已自动重试）", retryable=True
    ),
    "verify_required": StatusVerdict(
        AccountStatus.WAIT_VERIFY.value, "需要安全验证", retryable=False
    ),
    "risk_blocked": StatusVerdict(
        AccountStatus.FAILED.value, "IP 触发风控（异常活动/维护）", retryable=True
    ),
    "account_unblocked": StatusVerdict(
        AccountStatus.FAILED.value, "已解除阻止但未能继续（页面未往下走）", retryable=True
    ),
    "unknown": StatusVerdict(AccountStatus.FAILED.value, "未知页面状态", retryable=True),
}


def verdict_from_page_state(state: str) -> StatusVerdict:
    """页面状态字符串 → 状态判定。"""
    return _PAGE_MAP.get(state, _PAGE_MAP["unknown"])


def verdict_from_exception(exc: BaseException) -> StatusVerdict:
    """异常 → 状态判定。超时/网络类问题视为可重试。"""
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    retryable = any(
        k in name or k in text
        for k in ("timeout", "connection", "proxy", "net::", "closed", "target")
    )
    return StatusVerdict(AccountStatus.FAILED.value, f"{exc.__class__.__name__}: {exc}", retryable)


def is_terminal(status: str) -> bool:
    """终态：不该再自动重试的状态。"""
    return status in (
        AccountStatus.OK.value,
        AccountStatus.PASSWORD_WRONG.value,
        AccountStatus.NOT_FOUND.value,
        AccountStatus.LOCKED.value,
    )


def describe(status: str) -> str:
    return {
        AccountStatus.NEW.value: "待处理",
        AccountStatus.PENDING.value: "已排队",
        AccountStatus.RUNNING.value: "执行中",
        AccountStatus.OK.value: "登录成功",
        AccountStatus.WAIT_VERIFY.value: "等待验证",
        AccountStatus.PASSWORD_WRONG.value: "密码错误",
        AccountStatus.LOCKED.value: "账号锁定",
        AccountStatus.NOT_FOUND.value: "账号不存在",
        AccountStatus.FAILED.value: "失败",
        AccountStatus.SKIPPED.value: "已跳过",
    }.get(status, status)


def next_status_for_retry(current: str) -> Optional[str]:
    """失败后是否放回待处理队列。"""
    if is_terminal(current):
        return None
    return AccountStatus.NEW.value
