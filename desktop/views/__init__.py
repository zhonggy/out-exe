"""views 包：各功能页面。

每个页面是一个 QWidget，通过构造函数拿到 AppContext。
页面职责只有两件：展示数据、把用户操作转成对业务层的调用。
业务逻辑一律不在此处实现。
"""

from .about_view import AboutView
from .accounts_view import AccountsView
from .browser_view import BrowserView
from .dashboard import DashboardView
from .logs_view import LogsView
from .profiles_view import ProfilesView
from .proxy_view import ProxyView
from .settings_view import SettingsView
from .tasks_view import TasksView

__all__ = [
    "AboutView",
    "AccountsView",
    "BrowserView",
    "DashboardView",
    "LogsView",
    "ProfilesView",
    "ProxyView",
    "SettingsView",
    "TasksView",
]
