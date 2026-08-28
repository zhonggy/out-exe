"""停止执行后任务列表必须清空。

用户报「停止任务后任务管理里的账号列表没清空」。原行为是：停止只结束执行
进程，任务行还留在库里（QUEUED/RUNNING），列表照旧显示，看起来像没停成功。

三件容易写错的事：

1. **顺序**：必须先停进程再删记录。执行进程活着时每 2 秒 ``queue.restore()``
   会从库里补拉任务，先删会被它又写回一批。
2. **断点要跟着删**：checkpoints 按 task_id 关联，任务行没了而断点还在，
   就成了永不被读取的孤儿数据。
3. **不能牵连账号状态**：已跑出的登录结果（OK/密码错误等）必须保留，
   否则用户等于白跑一轮。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import _console  # noqa: E402,F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    if not os.environ.get("OA_DATA_DIR"):
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实数据")
        return 1

    sys.argv = ["main.py"]
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    import desktop.views.tasks_view as tv
    from database import AccountStatus, Checkpoint, Task, TaskStatus
    from desktop.bridge.tasks import wait_for_idle
    from desktop.context import AppContext

    tv.confirm = lambda *a, **k: True

    ctx = AppContext()
    failures: list = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        if ok:
            print(f"[OK]   {label:<28} {detail}")
        else:
            failures.append(f"{label}: {detail}")
            print(f"[FAIL] {label:<28} {detail}")

    # ---------- 布置数据 ----------
    # 各状态的任务都造一条，验证「全清」而不是只清某几种
    statuses = [
        TaskStatus.QUEUED.value,
        TaskStatus.CREATED.value,
        TaskStatus.RUNNING.value,
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
    ]
    task_ids = []
    for index, status in enumerate(statuses):
        account = f"st{index}@example.invalid"
        ctx.db.upsert_account(account, "pw")
        task_id = ctx.db.create_task(Task(account=account, status=status))
        task_ids.append(task_id)
        # 每条任务挂一个断点，验证级联删除
        ctx.db.save_checkpoint(
            Checkpoint(task_id=task_id, account=account, stage="CREATED", data={"k": 1})
        )

    # 账号状态：一个已成功、一个密码错误，停止后这两个必须原样保留
    ctx.db.update_account_status("st0@example.invalid", AccountStatus.OK.value)
    ctx.db.update_account_status("st1@example.invalid", AccountStatus.PASSWORD_WRONG.value)

    before_tasks = len(ctx.db.list_tasks(limit=500))
    before_accounts = ctx.db.count_accounts()
    check("初始任务已就位", before_tasks == len(statuses), f"{before_tasks} 条")

    view = tv.TasksView(ctx)

    def drain(seconds: float = 4.0) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        wait_for_idle(8000)
        for _ in range(40):
            app.processEvents()
            time.sleep(0.01)

    drain(3.0)
    check("列表已加载", view.model.rowCount() == len(statuses), f"{view.model.rowCount()} 行")

    # ---------- 停止顺序：先停进程，再删记录 ----------
    order: list = []
    real_stop = ctx.wpm.stop

    def traced_stop(*args, **kwargs):
        order.append("stop")
        return real_stop(*args, **kwargs)

    real_clear = ctx.db.clear_tasks

    def traced_clear(*args, **kwargs):
        order.append("clear")
        return real_clear(*args, **kwargs)

    ctx.wpm.stop = traced_stop
    ctx.db.clear_tasks = traced_clear

    view._on_stop()
    drain(6.0)

    ctx.wpm.stop = real_stop
    ctx.db.clear_tasks = real_clear

    check(
        "先停进程再清列表",
        order == ["stop", "clear"],
        f"实际顺序 {order}",
    )

    # ---------- 结果 ----------
    after_tasks = ctx.db.list_tasks(limit=500)
    check("库中任务已清空", len(after_tasks) == 0, f"剩余 {len(after_tasks)} 条")
    check("界面列表已清空", view.model.rowCount() == 0, f"{view.model.rowCount()} 行")

    orphan = sum(len(ctx.db.list_checkpoints(t)) for t in task_ids)
    check("断点已级联删除", orphan == 0, f"剩余 {orphan} 条断点")

    after_accounts = ctx.db.count_accounts()
    check(
        "账号状态未被牵连",
        after_accounts == before_accounts,
        f"{before_accounts} → {after_accounts}",
    )
    ok_account = ctx.db.get_account("st0@example.invalid")
    check(
        "已成功账号仍为 OK",
        ok_account is not None and ok_account.status == AccountStatus.OK.value,
        getattr(ok_account, "status", "?"),
    )
    wrong_account = ctx.db.get_account("st1@example.invalid")
    check(
        "密码错误账号状态保留",
        wrong_account is not None
        and wrong_account.status == AccountStatus.PASSWORD_WRONG.value,
        getattr(wrong_account, "status", "?"),
    )

    # ---------- 清空队列只删未开始的 ----------
    print()
    print("=== 清空队列（只删未开始） ===")
    keep_id = ctx.db.create_task(
        Task(account="keep@example.invalid", status=TaskStatus.COMPLETED.value)
    )
    ctx.db.create_task(Task(account="q1@example.invalid", status=TaskStatus.QUEUED.value))
    ctx.db.create_task(Task(account="q2@example.invalid", status=TaskStatus.CREATED.value))
    running_id = ctx.db.create_task(
        Task(account="r1@example.invalid", status=TaskStatus.RUNNING.value)
    )

    view._on_clear_queue()
    drain(5.0)

    remain = {t.id for t in ctx.db.list_tasks(limit=500)}
    check("已完成任务保留", keep_id in remain, f"id={keep_id}")
    check("执行中任务保留", running_id in remain, f"id={running_id}")
    check("排队任务已删除", len(remain) == 2, f"剩余 {len(remain)} 条")

    ctx.shutdown()

    print()
    if failures:
        print(f"停止清空冒烟失败：{len(failures)} 项")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("停止清空冒烟通过：停止后任务列表清空，账号状态与已完成任务不受牵连")
    return 0


if __name__ == "__main__":
    sys.exit(main())
