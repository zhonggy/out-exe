"""数据模型与状态枚举（dataclass，纯数据，不含 IO）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


class AccountStatus(str, Enum):
    """账号状态。"""

    NEW = "NEW"                    # 从 accounts.txt 导入，未处理
    PENDING = "PENDING"            # 已排入任务队列
    RUNNING = "RUNNING"            # 正在执行
    OK = "OK"                      # 登录成功
    WAIT_VERIFY = "WAIT_VERIFY"    # 卡在安全验证
    PASSWORD_WRONG = "PASSWORD_WRONG"
    LOCKED = "LOCKED"              # 账号被锁定/封禁
    NOT_FOUND = "NOT_FOUND"        # 账号不存在
    FAILED = "FAILED"              # 其他失败
    SKIPPED = "SKIPPED"


class TaskStatus(str, Enum):
    """任务生命周期状态。"""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @classmethod
    def terminal(cls) -> tuple:
        return (cls.COMPLETED, cls.FAILED, cls.CANCELLED)


class FlowStage(str, Enum):
    """流程状态机节点（规划文档第 10 节）。"""

    CREATED = "CREATED"
    BROWSER_STARTED = "BROWSER_STARTED"
    LOGIN_PAGE = "LOGIN_PAGE"
    USERNAME_INPUT = "USERNAME_INPUT"
    PASSWORD_INPUT = "PASSWORD_INPUT"
    CHECK_STATUS = "CHECK_STATUS"
    WAIT_VERIFY = "WAIT_VERIFY"
    VERIFIED = "VERIFIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @classmethod
    def order(cls) -> list:
        return [
            cls.CREATED,
            cls.BROWSER_STARTED,
            cls.LOGIN_PAGE,
            cls.USERNAME_INPUT,
            cls.PASSWORD_INPUT,
            cls.CHECK_STATUS,
            cls.WAIT_VERIFY,
            cls.VERIFIED,
            cls.COMPLETED,
        ]

    def index(self) -> int:
        try:
            return self.order().index(self)
        except ValueError:
            return -1


class ProfileStatus(str, Enum):
    IDLE = "IDLE"
    IN_USE = "IN_USE"
    BROKEN = "BROKEN"


@dataclass
class Account:
    id: Optional[int] = None
    account: str = ""
    password: str = ""
    status: str = AccountStatus.NEW.value
    note: str = ""
    profile_id: str = ""
    last_run: Optional[float] = None
    run_count: int = 0
    fail_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self, mask_password: bool = True) -> Dict[str, Any]:
        data = asdict(self)
        if mask_password and data.get("password"):
            data["password"] = "***"
        return data


@dataclass
class Task:
    id: Optional[int] = None
    type: str = "login"
    account: str = ""
    account_id: Optional[int] = None
    status: str = TaskStatus.CREATED.value
    stage: str = FlowStage.CREATED.value
    priority: int = 0
    attempt: int = 0
    max_attempt: int = 1
    profile_id: str = ""
    proxy: str = ""
    result: str = ""
    error: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def duration(self) -> Optional[float]:
        if self.start_time is None:
            return None
        return (self.end_time or time.time()) - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["duration"] = self.duration()
        return data


@dataclass
class BrowserProfile:
    profile_id: str = ""
    path: str = ""
    status: str = ProfileStatus.IDLE.value
    account: str = ""
    fingerprint_seed: Optional[int] = None
    proxy: str = ""
    last_used: Optional[float] = None
    use_count: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Checkpoint:
    """流程断点：任务中断后可从此处恢复。"""

    id: Optional[int] = None
    task_id: int = 0
    account: str = ""
    stage: str = FlowStage.CREATED.value
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
