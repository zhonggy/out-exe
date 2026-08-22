"""流程断点：保存节点状态，支持中断恢复。

设计：
- 每进入一个 FlowStage 就 save 一次，写入 checkpoints 表
- 任务重新调度时用 resume_stage() 读回最后到达的阶段
- 已到 CHECK_STATUS 之后的阶段不重跑前置输入步骤
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from database import Checkpoint, Database, FlowStage


class CheckpointManager:
    """任务级断点记录。"""

    def __init__(self, db: Database, task_id: int, account: str = "", enabled: bool = True, logger=None):
        self.db = db
        self.task_id = task_id
        self.account = account
        self.enabled = enabled
        self.log = logger
        self._current: str = FlowStage.CREATED.value
        self._history: List[str] = []

    # ---------- 写 ----------
    def save(self, stage: str | FlowStage, **data: Any) -> None:
        """记录到达某阶段。"""
        stage_value = stage.value if isinstance(stage, FlowStage) else str(stage)
        self._current = stage_value
        self._history.append(stage_value)
        if self.log:
            self.log.info("checkpoint", f"stage={stage_value}" + (f" {data}" if data else ""))
        if not self.enabled:
            return
        cp = Checkpoint(
            task_id=self.task_id,
            account=self.account,
            stage=stage_value,
            data={"ts": time.time(), **data},
        )
        try:
            self.db.save_checkpoint(cp)
            self.db.update_task(self.task_id, stage=stage_value)
        except Exception as exc:
            if self.log:
                self.log.warn("checkpoint", f"保存断点失败: {exc}")

    def clear(self) -> None:
        try:
            self.db.clear_checkpoints(self.task_id)
        except Exception:
            pass
        self._history.clear()
        self._current = FlowStage.CREATED.value

    # ---------- 读 ----------
    @property
    def current(self) -> str:
        return self._current

    @property
    def history(self) -> List[str]:
        return list(self._history)

    def resume_stage(self) -> str:
        """读回数据库中最后到达的阶段。"""
        try:
            cp = self.db.latest_checkpoint(self.task_id)
        except Exception:
            cp = None
        if cp:
            self._current = cp.stage
            return cp.stage
        return FlowStage.CREATED.value

    def resume_data(self) -> Dict[str, Any]:
        try:
            cp = self.db.latest_checkpoint(self.task_id)
        except Exception:
            return {}
        return cp.data if cp else {}

    def reached(self, stage: str | FlowStage) -> bool:
        """当前阶段是否已达到（含超过）指定阶段。"""
        target = stage if isinstance(stage, FlowStage) else FlowStage(stage)
        try:
            current = FlowStage(self._current)
        except ValueError:
            return False
        ci, ti = current.index(), target.index()
        if ci < 0 or ti < 0:
            return False
        return ci >= ti

    def timeline(self) -> List[Dict[str, Any]]:
        """完整断点时间线（面板展示用）。"""
        try:
            items = self.db.list_checkpoints(self.task_id)
        except Exception:
            return []
        return [
            {
                "stage": cp.stage,
                "at": time.strftime("%H:%M:%S", time.localtime(cp.created_at)),
                "data": cp.data,
            }
            for cp in items
        ]


def latest_stage(db: Database, task_id: int) -> Optional[str]:
    cp = db.latest_checkpoint(task_id)
    return cp.stage if cp else None
