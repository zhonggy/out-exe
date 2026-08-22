"""SQLite 存储层。

设计要点：
- 单文件数据库，WAL 模式，多线程共享连接（check_same_thread=False + 写锁）
- 表：accounts / tasks / profiles / checkpoints / events
- 所有写操作走 _write() 串行化，避免 Worker 并发写冲突
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import (
    Account,
    AccountStatus,
    BrowserProfile,
    Checkpoint,
    FlowStage,
    ProfileStatus,
    Task,
    TaskStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'NEW',
    note        TEXT NOT NULL DEFAULT '',
    profile_id  TEXT NOT NULL DEFAULT '',
    last_run    REAL,
    run_count   INTEGER NOT NULL DEFAULT 0,
    fail_count  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL DEFAULT 'login',
    account     TEXT NOT NULL DEFAULT '',
    account_id  INTEGER,
    status      TEXT NOT NULL DEFAULT 'CREATED',
    stage       TEXT NOT NULL DEFAULT 'CREATED',
    priority    INTEGER NOT NULL DEFAULT 0,
    attempt     INTEGER NOT NULL DEFAULT 0,
    max_attempt INTEGER NOT NULL DEFAULT 1,
    profile_id  TEXT NOT NULL DEFAULT '',
    proxy       TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL DEFAULT '{}',
    start_time  REAL,
    end_time    REAL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_account ON tasks(account);

CREATE TABLE IF NOT EXISTS profiles (
    profile_id       TEXT PRIMARY KEY,
    path             TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'IDLE',
    account          TEXT NOT NULL DEFAULT '',
    fingerprint_seed INTEGER,
    proxy            TEXT NOT NULL DEFAULT '',
    last_used        REAL,
    use_count        INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    account    TEXT NOT NULL DEFAULT '',
    stage      TEXT NOT NULL DEFAULT 'CREATED',
    data       TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ckpt_task ON checkpoints(task_id);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    task_id INTEGER,
    account TEXT NOT NULL DEFAULT '',
    flow    TEXT NOT NULL DEFAULT '',
    level   TEXT NOT NULL DEFAULT 'INFO',
    stage   TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
"""


def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(**{k: row[k] for k in row.keys()})


def _row_to_task(row: sqlite3.Row) -> Task:
    data = {k: row[k] for k in row.keys()}
    raw = data.get("payload") or "{}"
    try:
        data["payload"] = json.loads(raw)
    except Exception:
        data["payload"] = {}
    return Task(**data)


def _row_to_profile(row: sqlite3.Row) -> BrowserProfile:
    return BrowserProfile(**{k: row[k] for k in row.keys()})


class Database:
    """线程安全的 SQLite 封装。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    # ---------- 基础 ----------
    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            finally:
                self._conn.close()

    def _write(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ---------- accounts ----------
    def upsert_account(self, account: str, password: str, note: str = "") -> int:
        """导入账号：已存在则更新密码，不覆盖状态。返回账号 id。"""
        now = time.time()
        self._write(
            """INSERT INTO accounts(account, password, status, note, created_at, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(account) DO UPDATE SET
                 password=excluded.password,
                 note=CASE WHEN excluded.note != '' THEN excluded.note ELSE accounts.note END,
                 updated_at=excluded.updated_at""",
            (account, password, AccountStatus.NEW.value, note, now, now),
        )
        row = self._query_one("SELECT id FROM accounts WHERE account=?", (account,))
        return int(row["id"]) if row else 0

    def get_account(self, account: str) -> Optional[Account]:
        row = self._query_one("SELECT * FROM accounts WHERE account=?", (account,))
        return _row_to_account(row) if row else None

    def get_account_by_id(self, account_id: int) -> Optional[Account]:
        row = self._query_one("SELECT * FROM accounts WHERE id=?", (account_id,))
        return _row_to_account(row) if row else None

    def list_accounts(
        self, status: Optional[str] = None, limit: int = 500, offset: int = 0
    ) -> List[Account]:
        if status:
            rows = self._query(
                "SELECT * FROM accounts WHERE status=? ORDER BY id LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        else:
            rows = self._query(
                "SELECT * FROM accounts ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
            )
        return [_row_to_account(r) for r in rows]

    def count_accounts(self) -> Dict[str, int]:
        rows = self._query("SELECT status, COUNT(*) AS c FROM accounts GROUP BY status")
        return {r["status"]: int(r["c"]) for r in rows}

    def update_account_status(
        self,
        account: str,
        status: str,
        note: Optional[str] = None,
        bump_run: bool = False,
        bump_fail: bool = False,
    ) -> None:
        sets = ["status=?", "updated_at=?"]
        params: List[Any] = [status, time.time()]
        if note is not None:
            # 传空串表示清除备注
            sets.append("note=?")
            params.append(note)
        if bump_run:
            sets.append("run_count=run_count+1")
            sets.append("last_run=?")
            params.append(time.time())
        if bump_fail:
            sets.append("fail_count=fail_count+1")
        params.append(account)
        self._write(f"UPDATE accounts SET {', '.join(sets)} WHERE account=?", params)

    def bind_account_profile(self, account: str, profile_id: str) -> None:
        self._write(
            "UPDATE accounts SET profile_id=?, updated_at=? WHERE account=?",
            (profile_id, time.time(), account),
        )

    def delete_account(self, account: str) -> None:
        self._write("DELETE FROM accounts WHERE account=?", (account,))

    # ---------- tasks ----------
    def create_task(self, task: Task) -> int:
        now = time.time()
        cur = self._write(
            """INSERT INTO tasks(type, account, account_id, status, stage, priority,
                                 attempt, max_attempt, profile_id, proxy, result, error,
                                 payload, start_time, end_time, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task.type,
                task.account,
                task.account_id,
                task.status,
                task.stage,
                task.priority,
                task.attempt,
                task.max_attempt,
                task.profile_id,
                task.proxy,
                task.result,
                task.error,
                json.dumps(task.payload, ensure_ascii=False),
                task.start_time,
                task.end_time,
                now,
                now,
            ),
        )
        task.id = int(cur.lastrowid)
        return task.id

    def get_task(self, task_id: int) -> Optional[Task]:
        row = self._query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        return _row_to_task(row) if row else None

    def list_tasks(
        self,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Task]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        if task_type:
            where.append("type=?")
            params.append(task_type)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([limit, offset])
        rows = self._query(
            f"SELECT * FROM tasks {clause} ORDER BY id DESC LIMIT ? OFFSET ?", params
        )
        return [_row_to_task(r) for r in rows]

    def update_task(self, task_id: int, **fields: Any) -> None:
        if not fields:
            return
        if "payload" in fields and isinstance(fields["payload"], (dict, list)):
            fields["payload"] = json.dumps(fields["payload"], ensure_ascii=False)
        fields["updated_at"] = time.time()
        sets = ", ".join(f"{k}=?" for k in fields)
        params = list(fields.values()) + [task_id]
        self._write(f"UPDATE tasks SET {sets} WHERE id=?", params)

    def count_tasks(self) -> Dict[str, int]:
        rows = self._query("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status")
        return {r["status"]: int(r["c"]) for r in rows}

    def pending_tasks(self, limit: int = 100) -> List[Task]:
        rows = self._query(
            """SELECT * FROM tasks WHERE status IN (?,?)
               ORDER BY priority DESC, id ASC LIMIT ?""",
            (TaskStatus.CREATED.value, TaskStatus.QUEUED.value, limit),
        )
        return [_row_to_task(r) for r in rows]

    def reset_stale_running(self) -> int:
        """进程异常退出后，把残留 RUNNING 任务打回 QUEUED，便于重新调度。"""
        cur = self._write(
            "UPDATE tasks SET status=?, updated_at=? WHERE status=?",
            (TaskStatus.QUEUED.value, time.time(), TaskStatus.RUNNING.value),
        )
        return cur.rowcount or 0

    def delete_task(self, task_id: int) -> None:
        self._write("DELETE FROM tasks WHERE id=?", (task_id,))
        self._write("DELETE FROM checkpoints WHERE task_id=?", (task_id,))

    def clear_tasks(self, statuses: Optional[List[str]] = None) -> int:
        if statuses:
            marks = ",".join("?" * len(statuses))
            cur = self._write(f"DELETE FROM tasks WHERE status IN ({marks})", statuses)
        else:
            cur = self._write("DELETE FROM tasks", ())
        return cur.rowcount or 0

    # ---------- profiles ----------
    def upsert_profile(self, profile: BrowserProfile) -> None:
        self._write(
            """INSERT INTO profiles(profile_id, path, status, account, fingerprint_seed,
                                    proxy, last_used, use_count, created_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(profile_id) DO UPDATE SET
                 path=excluded.path, status=excluded.status, account=excluded.account,
                 fingerprint_seed=excluded.fingerprint_seed, proxy=excluded.proxy,
                 last_used=excluded.last_used, use_count=excluded.use_count""",
            (
                profile.profile_id,
                profile.path,
                profile.status,
                profile.account,
                profile.fingerprint_seed,
                profile.proxy,
                profile.last_used,
                profile.use_count,
                profile.created_at,
            ),
        )

    def get_profile(self, profile_id: str) -> Optional[BrowserProfile]:
        row = self._query_one("SELECT * FROM profiles WHERE profile_id=?", (profile_id,))
        return _row_to_profile(row) if row else None

    def find_profile_by_account(self, account: str) -> Optional[BrowserProfile]:
        row = self._query_one(
            "SELECT * FROM profiles WHERE account=? ORDER BY last_used DESC LIMIT 1",
            (account,),
        )
        return _row_to_profile(row) if row else None

    def list_profiles(self, status: Optional[str] = None) -> List[BrowserProfile]:
        if status:
            rows = self._query(
                "SELECT * FROM profiles WHERE status=? ORDER BY created_at", (status,)
            )
        else:
            rows = self._query("SELECT * FROM profiles ORDER BY created_at")
        return [_row_to_profile(r) for r in rows]

    def set_profile_status(self, profile_id: str, status: str) -> None:
        self._write(
            "UPDATE profiles SET status=?, last_used=? WHERE profile_id=?",
            (status, time.time(), profile_id),
        )

    def touch_profile(self, profile_id: str) -> None:
        self._write(
            "UPDATE profiles SET use_count=use_count+1, last_used=? WHERE profile_id=?",
            (time.time(), profile_id),
        )

    def delete_profile(self, profile_id: str) -> None:
        self._write("DELETE FROM profiles WHERE profile_id=?", (profile_id,))

    def release_all_profiles(self) -> int:
        cur = self._write(
            "UPDATE profiles SET status=? WHERE status=?",
            (ProfileStatus.IDLE.value, ProfileStatus.IN_USE.value),
        )
        return cur.rowcount or 0

    # ---------- checkpoints ----------
    def save_checkpoint(self, cp: Checkpoint) -> int:
        cur = self._write(
            """INSERT INTO checkpoints(task_id, account, stage, data, created_at)
               VALUES(?,?,?,?,?)""",
            (
                cp.task_id,
                cp.account,
                cp.stage,
                json.dumps(cp.data, ensure_ascii=False),
                cp.created_at,
            ),
        )
        cp.id = int(cur.lastrowid)
        return cp.id

    def latest_checkpoint(self, task_id: int) -> Optional[Checkpoint]:
        row = self._query_one(
            "SELECT * FROM checkpoints WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if not row:
            return None
        try:
            data = json.loads(row["data"] or "{}")
        except Exception:
            data = {}
        return Checkpoint(
            id=row["id"],
            task_id=row["task_id"],
            account=row["account"],
            stage=row["stage"],
            data=data,
            created_at=row["created_at"],
        )

    def list_checkpoints(self, task_id: int) -> List[Checkpoint]:
        rows = self._query(
            "SELECT * FROM checkpoints WHERE task_id=? ORDER BY id", (task_id,)
        )
        out = []
        for row in rows:
            try:
                data = json.loads(row["data"] or "{}")
            except Exception:
                data = {}
            out.append(
                Checkpoint(
                    id=row["id"],
                    task_id=row["task_id"],
                    account=row["account"],
                    stage=row["stage"],
                    data=data,
                    created_at=row["created_at"],
                )
            )
        return out

    def clear_checkpoints(self, task_id: int) -> None:
        self._write("DELETE FROM checkpoints WHERE task_id=?", (task_id,))

    # ---------- events ----------
    def add_event(
        self,
        flow: str,
        level: str,
        stage: str,
        message: str,
        task_id: Optional[int] = None,
        account: str = "",
    ) -> None:
        self._write(
            """INSERT INTO events(ts, task_id, account, flow, level, stage, message)
               VALUES(?,?,?,?,?,?,?)""",
            (time.time(), task_id, account, flow, level, stage, message),
        )

    def list_events(
        self, task_id: Optional[int] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        if task_id is not None:
            rows = self._query(
                "SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?",
                (task_id, limit),
            )
        else:
            rows = self._query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [{k: r[k] for k in r.keys()} for r in rows]

    # ---------- 统计 ----------
    def stats(self) -> Dict[str, Any]:
        return {
            "accounts": self.count_accounts(),
            "tasks": self.count_tasks(),
            "profiles": len(self.list_profiles()),
            "db_path": str(self.path),
        }


_db_lock = threading.Lock()
_db_instance: Optional[Database] = None


def get_db(path: Optional[str | Path] = None) -> Database:
    """进程级单例。首次调用必须能确定路径（传参或走默认配置）。"""
    global _db_instance
    with _db_lock:
        if _db_instance is None:
            if path is None:
                from config import load_config

                path = load_config().path_of("database.path", "data/app.db")
            _db_instance = Database(path)
        return _db_instance


def reset_db() -> None:
    """关闭单例（测试用）。"""
    global _db_instance
    with _db_lock:
        if _db_instance is not None:
            try:
                _db_instance.close()
            except Exception:
                pass
        _db_instance = None
