"""流程基类：定义自动化流程的统一接口，便于后续插件扩展。

任何流程只需继承 BaseFlow 并实现 run()，即可被 Worker 调度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from account import StatusVerdict
from database import FlowStage


@dataclass
class FlowResult:
    """流程执行结果。"""

    success: bool = False
    stage: str = FlowStage.CREATED.value
    verdict: Optional[StatusVerdict] = None
    message: str = ""
    retryable: bool = False
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "status": self.verdict.status if self.verdict else "",
            "reason": self.verdict.reason if self.verdict else "",
            "message": self.message,
            "retryable": self.retryable,
            "data": self.data,
        }


class BaseFlow:
    """流程基类。

    子类约定：
    - name：流程标识，与 Task.type 对应
    - run()：执行流程，返回 FlowResult
    """

    name = "base"

    def __init__(self, session, cfg, logger=None, checkpoint=None, proxy_manager=None):
        self.session = session
        self.page = getattr(session, "page", None)
        self.cfg = cfg
        self.log = logger
        self.ckpt = checkpoint
        self.proxy_manager = proxy_manager

    def run(self, **kwargs: Any) -> FlowResult:  # pragma: no cover - 抽象
        raise NotImplementedError

    # ---------- 辅助 ----------
    def mark(self, stage: str | FlowStage, **data: Any) -> None:
        if self.ckpt is not None:
            self.ckpt.save(stage, **data)

    def feedback_proxy(self, success: bool, penalty: int = 0) -> None:
        """把结果反馈给代理管理器，影响后续 IP 权重。"""
        if self.proxy_manager is None:
            return
        proxy_url = getattr(self.session, "proxy_url", "")
        if not proxy_url:
            return
        if penalty:
            self.proxy_manager.penalize(proxy_url, penalty=penalty)
        else:
            self.proxy_manager.record(proxy_url, success)


_REGISTRY: Dict[str, type] = {}


def register_flow(cls: type) -> type:
    """流程注册装饰器，供 Worker 按 Task.type 查找。"""
    _REGISTRY[getattr(cls, "name", cls.__name__)] = cls
    return cls


def get_flow(name: str) -> Optional[type]:
    return _REGISTRY.get(name)


def list_flows() -> Dict[str, type]:
    return dict(_REGISTRY)
