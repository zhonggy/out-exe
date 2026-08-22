"""account 包：账号导入、分配、状态管理。"""

from .manager import AccountManager, get_account_manager
from .models import Account, AccountStatus
from .status import (
    StatusVerdict,
    describe,
    is_terminal,
    next_status_for_retry,
    verdict_from_exception,
    verdict_from_page_state,
)

__all__ = [
    "Account",
    "AccountManager",
    "AccountStatus",
    "StatusVerdict",
    "describe",
    "get_account_manager",
    "is_terminal",
    "next_status_for_retry",
    "verdict_from_exception",
    "verdict_from_page_state",
]
