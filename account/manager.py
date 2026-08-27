"""账号管理：从 accounts.txt 导入、分配、状态回写、结果导出。

accounts.txt 格式（默认分隔符 ----）：
    account1@example.com----password1
    account2@example.com----password2
以 # 开头的行视为注释。
"""

from __future__ import annotations

import csv
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from database import Account, AccountStatus, Database

from .status import StatusVerdict, describe, is_terminal


class AccountManager:
    """账号仓库操作封装。"""

    def __init__(
        self,
        db: Database,
        accounts_file: str | Path = "accounts.txt",
        separator: str = "----",
        logger=None,
    ):
        self.db = db
        self.accounts_file = Path(accounts_file)
        self.separator = separator or "----"
        self.log = logger
        self._lock = threading.RLock()

    # ---------- 导入 ----------
    def parse_line(self, line: str) -> Optional[Tuple[str, str]]:
        """解析一行。兼容 ---- / 逗号 / 冒号 / 制表符 / 空格分隔。"""
        text = line.strip()
        if not text or text.startswith("#"):
            return None
        for sep in (self.separator, "\t", ",", "----", ":", " "):
            if sep and sep in text:
                account, _, password = text.partition(sep)
                account, password = account.strip(), password.strip()
                if account:
                    return account, password
        return (text, "") if "@" in text else None

    def import_file(self, path: Optional[str | Path] = None) -> Dict[str, Any]:
        """导入账号文件。已存在的账号只更新密码，不覆盖状态。"""
        target = Path(path) if path else self.accounts_file
        if not target.is_file():
            if self.log:
                self.log.warn("account_import", f"账号文件不存在: {target}")
            return {"file": str(target), "imported": 0, "skipped": 0, "exists": False}

        imported = skipped = 0
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parsed = self.parse_line(line)
                if not parsed:
                    if line.strip() and not line.strip().startswith("#"):
                        skipped += 1
                    continue
                account, password = parsed
                self.db.upsert_account(account, password)
                imported += 1

        if self.log:
            self.log.ok(
                "account_import", f"导入 {imported} 个账号，跳过 {skipped} 行: {target}"
            )
        return {"file": str(target), "imported": imported, "skipped": skipped, "exists": True}

    def import_text(self, text: str) -> Dict[str, Any]:
        """从粘贴的多行文本导入。"""
        imported = skipped = 0
        for line in (text or "").splitlines():
            parsed = self.parse_line(line)
            if not parsed:
                if line.strip() and not line.strip().startswith("#"):
                    skipped += 1
                continue
            account, password = parsed
            self.db.upsert_account(account, password)
            imported += 1
        return {"imported": imported, "skipped": skipped}

    def add(self, account: str, password: str, note: str = "") -> int:
        return self.db.upsert_account(account.strip(), password, note)

    def remove(self, account: str) -> None:
        self.db.delete_account(account)

    def remove_many(self, accounts: Iterable[str]) -> int:
        """批量删除，返回删除条数。"""
        names = [a for a in accounts if a]
        if not names:
            return 0
        count = self.db.delete_accounts(names)
        if self.log:
            self.log.ok("account_delete", f"批量删除 {count} 个账号")
        return count

    def accounts_with_status(self, status: str) -> List[str]:
        """某状态下的全部账号名（不分页），供界面批量勾选使用。"""
        return [a.account for a in self.db.list_accounts(status=status, limit=1000000)]

    # ---------- 分配 ----------
    def claim_batch(
        self, limit: int = 10, statuses: Optional[List[str]] = None
    ) -> List[Account]:
        """取一批待处理账号并标记 PENDING，避免重复排队。"""
        statuses = statuses or [AccountStatus.NEW.value]
        claimed: List[Account] = []
        with self._lock:
            for status in statuses:
                if len(claimed) >= limit:
                    break
                remaining = limit - len(claimed)
                for acc in self.db.list_accounts(status=status, limit=remaining):
                    self.db.update_account_status(acc.account, AccountStatus.PENDING.value)
                    acc.status = AccountStatus.PENDING.value
                    claimed.append(acc)
        return claimed

    def get(self, account: str) -> Optional[Account]:
        return self.db.get_account(account)

    def list(self, status: Optional[str] = None, limit: int = 500, offset: int = 0) -> List[Account]:
        return self.db.list_accounts(status=status, limit=limit, offset=offset)

    def password_of(self, account: str) -> str:
        acc = self.db.get_account(account)
        return acc.password if acc else ""

    # ---------- 状态回写 ----------
    def mark_running(self, account: str) -> None:
        self.db.update_account_status(account, AccountStatus.RUNNING.value, bump_run=True)

    def apply_verdict(self, account: str, verdict: StatusVerdict) -> None:
        """流程结果写回账号状态。"""
        self.db.update_account_status(
            account,
            verdict.status,
            note=verdict.reason,
            bump_fail=not verdict.success,
        )
        if self.log:
            level = "ok" if verdict.success else "warn"
            getattr(self.log, level)(
                "account_status",
                f"{account} → {describe(verdict.status)} ({verdict.reason})",
            )

    def reset_status(self, account: str, status: str = AccountStatus.NEW.value) -> None:
        # 传空串清除上次失败留下的备注
        self.db.update_account_status(account, status, note="")

    def reset_many(
        self, accounts: Iterable[str], status: str = AccountStatus.NEW.value
    ) -> int:
        """批量重置状态，返回条数。"""
        names = [a for a in accounts if a]
        if not names:
            return 0
        return self.db.update_accounts_status(names, status, note="")

    def reset_non_terminal(self) -> int:
        """把 PENDING/RUNNING 等中间态账号打回 NEW（进程异常退出后恢复用）。"""
        count = 0
        for status in (AccountStatus.PENDING.value, AccountStatus.RUNNING.value):
            for acc in self.db.list_accounts(status=status, limit=10000):
                self.db.update_account_status(acc.account, AccountStatus.NEW.value)
                count += 1
        return count

    # ---------- 统计与导出 ----------
    def stats(self) -> Dict[str, Any]:
        counts = self.db.count_accounts()
        total = sum(counts.values())
        return {
            "total": total,
            "by_status": counts,
            "by_status_label": {describe(k): v for k, v in counts.items()},
            "pending": counts.get(AccountStatus.NEW.value, 0),
            "ok": counts.get(AccountStatus.OK.value, 0),
        }

    def export_csv(self, path: str | Path, statuses: Optional[Iterable[str]] = None) -> Path:
        """导出结果 CSV（不含密码）。"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        wanted = set(statuses) if statuses else None
        rows = [a for a in self.db.list_accounts(limit=100000) if not wanted or a.status in wanted]
        with open(target, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["account", "status", "status_label", "note", "run_count", "fail_count", "last_run"]
            )
            for acc in rows:
                last_run = (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(acc.last_run))
                    if acc.last_run
                    else ""
                )
                writer.writerow(
                    [
                        acc.account,
                        acc.status,
                        describe(acc.status),
                        acc.note,
                        acc.run_count,
                        acc.fail_count,
                        last_run,
                    ]
                )
        if self.log:
            self.log.ok("account_export", f"导出 {len(rows)} 条到 {target}")
        return target

    def terminal_accounts(self) -> List[Account]:
        return [a for a in self.db.list_accounts(limit=100000) if is_terminal(a.status)]


def get_account_manager(cfg=None, db=None, logger=None) -> AccountManager:
    if cfg is None:
        from config import load_config

        cfg = load_config()
    if db is None:
        from database import get_db

        db = get_db(cfg.path_of("database.path", "data/app.db"))
    return AccountManager(
        db=db,
        accounts_file=cfg.path_of("system.accounts_file", "accounts.txt"),
        separator=str(cfg.get("system.account_separator", "----")),
        logger=logger,
    )
