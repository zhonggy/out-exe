"""往数据库塞一个探针账号，供 CI 验证冻结产物的执行进程分支。

执行进程在没有任何账号时会立刻退出，那样就验不到 Worker 启动、
pid 文件写入、停止标志收尾这几条真实路径。

只写入一个 ``.invalid`` 域名的假账号，不会产生真实登录尝试。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROBE_ACCOUNT = "ci-probe@example.invalid"


def main() -> int:
    from config import load_config
    from database import get_db

    cfg = load_config(use_cache=False)
    cfg.ensure_dirs()
    db = get_db(cfg.path_of("database.path", "data/app.db"))
    db.upsert_account(PROBE_ACCOUNT, "not-a-real-password")
    print(f"probe account ready: {PROBE_ACCOUNT}")
    print(f"database: {db.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
