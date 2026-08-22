"""account 包内的数据模型别名，统一从 database.models 复用，避免重复定义。"""

from database.models import Account, AccountStatus

__all__ = ["Account", "AccountStatus"]
