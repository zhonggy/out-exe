"""flow 包：流程引擎（检测 / 操作 / 断点 / 验证码 / 登录流程）。"""

from .action import PageAction
from .base import BaseFlow, FlowResult, get_flow, list_flows, register_flow
from .captcha import CaptchaResult, CaptchaSolver, reset_stats, stats_snapshot
from .checkpoint import CheckpointManager, latest_stage
from .detector import PageDetector
from .login_flow import LoginFlow, run_login

__all__ = [
    "BaseFlow",
    "CaptchaResult",
    "CaptchaSolver",
    "CheckpointManager",
    "FlowResult",
    "LoginFlow",
    "PageAction",
    "PageDetector",
    "get_flow",
    "latest_stage",
    "list_flows",
    "register_flow",
    "reset_stats",
    "run_login",
    "stats_snapshot",
]
