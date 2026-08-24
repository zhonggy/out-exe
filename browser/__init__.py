"""browser 包：Patchright 浏览器封装、上下文参数、Profile 管理。"""

from .browser import BrowserLaunchError, BrowserSession
from .context import (
    BASE_ARGS,
    build_args,
    build_context_options,
    build_proxy_option,
    random_viewport,
)
from .manager import BrowserManager, get_browser_manager, reset_browser_manager
from .kernel import (
    KERNEL_FINGERPRINT,
    KERNEL_PATCHRIGHT,
    describe as describe_kernel,
    find_fingerprint,
    resolve_executable,
)
from .profile import (
    ProfileManager,
    get_profile_manager,
    make_fingerprint_seed,
    reset_profile_manager,
    sanitize,
)

__all__ = [
    "BASE_ARGS",
    "BrowserLaunchError",
    "BrowserManager",
    "BrowserSession",
    "KERNEL_FINGERPRINT",
    "KERNEL_PATCHRIGHT",
    "ProfileManager",
    "build_args",
    "build_context_options",
    "build_proxy_option",
    "describe_kernel",
    "find_fingerprint",
    "get_browser_manager",
    "get_profile_manager",
    "make_fingerprint_seed",
    "random_viewport",
    "reset_browser_manager",
    "reset_profile_manager",
    "resolve_executable",
    "sanitize",
]
