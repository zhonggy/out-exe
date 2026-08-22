"""不依赖浏览器的单元测试：配置、数据库、账号、队列、代理、Profile、检测器、API。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from account import AccountManager, describe, is_terminal, verdict_from_page_state
from browser import ProfileManager, build_args, build_proxy_option, make_fingerprint_seed
from config import load_config
from database import (
    Account,
    AccountStatus,
    BrowserProfile,
    Checkpoint,
    Database,
    FlowStage,
    Task,
    TaskStatus,
)
from flow import CheckpointManager, list_flows
from flow.captcha import CaptchaSolver, reset_stats, stats_snapshot
from proxy import ProxyConfig, ProxyManager, resolve_geolocation, resolve_timezone
from proxy.resin import Resin, get_resin, reset_resin
from task import TaskQueue


@pytest.fixture()
def db(tmp_path) -> Database:
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture()
def cfg():
    return load_config(use_cache=False)


# ---------- 配置 ----------
def test_config_defaults(cfg):
    assert cfg.get("system.max_workers") >= 1
    assert cfg.get("api.host") == "127.0.0.1"
    assert cfg.get("api.auth_enabled") is True
    assert cfg.get("flow.login_url").startswith("https://")


def test_config_dotted_and_resolve(cfg):
    cfg.set("browser.headless", True)
    assert cfg.get("browser.headless") is True
    assert cfg.get("nope.missing", "fallback") == "fallback"
    assert cfg.resolve("data/x.db").is_absolute()
    assert cfg.path_of("database.path").name == "app.db"


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("OA_SYSTEM__MAX_WORKERS", "7")
    monkeypatch.setenv("OA_BROWSER__HEADLESS", "true")
    c = load_config(use_cache=False)
    assert c.get("system.max_workers") == 7
    assert c.get("browser.headless") is True


def test_config_broken_yaml_falls_back(tmp_path, capsys):
    """配置文件语法错误：回退默认值启动，不崩溃。"""
    bad = tmp_path / "config.yaml"
    bad.write_text("browser:\n  headless: true\n  bad_indent:\n x: 1\n", encoding="utf-8")
    c = load_config(str(bad), use_cache=False)
    assert c.get("system.max_workers") >= 1      # 默认值兜底
    assert c.source_path is None                  # 标记为未加载源文件
    out = capsys.readouterr().out
    assert "配置错误" in out


def test_config_update_deep_merge_and_save(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    from config import Config

    c = Config({"browser": {"headless": False, "timeout": 60000}, "resin": {"enabled": False}}, cfg_file)
    path = c.update({"browser": {"headless": True}, "resin": {"enabled": True, "url": "http://h:1/t/"}})
    assert path.is_file()
    # 深合并：只改了 headless，timeout 保留；resin 整段合入
    assert c.get("browser.timeout") == 60000
    assert c.get("browser.headless") is True
    assert c.get("resin.url") == "http://h:1/t/"
    # 落盘后重新解析一致（程序生成的 YAML 必然合法）
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["browser"]["timeout"] == 60000


# ---------- 数据库 ----------
def test_account_crud(db):
    acc_id = db.upsert_account("a@example.com", "pw1")
    assert acc_id > 0
    acc = db.get_account("a@example.com")
    assert acc and acc.password == "pw1"
    assert acc.status == AccountStatus.NEW.value

    # 重复导入更新密码，不覆盖状态
    db.update_account_status("a@example.com", AccountStatus.OK.value)
    db.upsert_account("a@example.com", "pw2")
    acc = db.get_account("a@example.com")
    assert acc.password == "pw2"
    assert acc.status == AccountStatus.OK.value

    db.update_account_status("a@example.com", AccountStatus.FAILED.value, bump_run=True, bump_fail=True)
    acc = db.get_account("a@example.com")
    assert acc.run_count == 1 and acc.fail_count == 1 and acc.last_run is not None

    db.delete_account("a@example.com")
    assert db.get_account("a@example.com") is None


def test_task_crud_and_payload(db):
    task = Task(type="login", account="b@example.com", payload={"password": "p", "n": 1})
    tid = db.create_task(task)
    assert tid > 0
    loaded = db.get_task(tid)
    assert loaded.payload == {"password": "p", "n": 1}

    db.update_task(tid, status=TaskStatus.RUNNING.value, payload={"k": "v"})
    assert db.get_task(tid).payload == {"k": "v"}
    assert db.count_tasks()[TaskStatus.RUNNING.value] == 1

    assert db.reset_stale_running() == 1
    assert db.get_task(tid).status == TaskStatus.QUEUED.value
    assert len(db.pending_tasks()) == 1


def test_profile_and_checkpoint(db):
    p = BrowserProfile(profile_id="acc_x", path="/tmp/x", account="x@e.com", fingerprint_seed=123)
    db.upsert_profile(p)
    assert db.get_profile("acc_x").fingerprint_seed == 123
    assert db.find_profile_by_account("x@e.com") is not None
    db.touch_profile("acc_x")
    assert db.get_profile("acc_x").use_count == 1

    tid = db.create_task(Task(account="x@e.com"))
    db.save_checkpoint(Checkpoint(task_id=tid, stage=FlowStage.LOGIN_PAGE.value, data={"a": 1}))
    db.save_checkpoint(Checkpoint(task_id=tid, stage=FlowStage.PASSWORD_INPUT.value))
    latest = db.latest_checkpoint(tid)
    assert latest.stage == FlowStage.PASSWORD_INPUT.value
    assert len(db.list_checkpoints(tid)) == 2


def test_events_and_stats(db):
    db.upsert_account("s@e.com", "p")
    tid = db.create_task(Task(account="s@e.com"))
    db.add_event("LOGIN", "INFO", "start", "msg", tid, "s@e.com")
    assert len(db.list_events(task_id=tid)) == 1
    stats = db.stats()
    assert stats["accounts"][AccountStatus.NEW.value] == 1


# ---------- 账号管理 ----------
def test_account_parse_and_import(db, tmp_path):
    am = AccountManager(db, accounts_file=tmp_path / "accounts.txt")
    assert am.parse_line("a@e.com----pw1") == ("a@e.com", "pw1")
    assert am.parse_line("b@e.com,pw2") == ("b@e.com", "pw2")
    assert am.parse_line("c@e.com\tpw3") == ("c@e.com", "pw3")
    assert am.parse_line("# comment") is None
    assert am.parse_line("   ") is None

    (tmp_path / "accounts.txt").write_text(
        "# 注释\na@e.com----pw1\nb@e.com----pw2\n\n乱码行\n", encoding="utf-8"
    )
    result = am.import_file()
    assert result["imported"] == 2
    assert am.stats()["total"] == 2
    assert am.password_of("a@e.com") == "pw1"


def test_account_claim_and_verdict(db, tmp_path):
    am = AccountManager(db, accounts_file=tmp_path / "a.txt")
    for i in range(5):
        am.add(f"u{i}@e.com", f"pw{i}")

    claimed = am.claim_batch(limit=3)
    assert len(claimed) == 3
    assert all(a.status == AccountStatus.PENDING.value for a in claimed)
    assert am.stats()["by_status"][AccountStatus.NEW.value] == 2

    am.apply_verdict("u0@e.com", verdict_from_page_state("mailbox"))
    assert db.get_account("u0@e.com").status == AccountStatus.OK.value

    am.apply_verdict("u1@e.com", verdict_from_page_state("password_wrong"))
    assert db.get_account("u1@e.com").status == AccountStatus.PASSWORD_WRONG.value

    assert am.reset_non_terminal() >= 1
    csv_path = am.export_csv(tmp_path / "out.csv")
    assert csv_path.is_file() and "u0@e.com" in csv_path.read_text(encoding="utf-8-sig")


def test_status_helpers():
    assert verdict_from_page_state("mailbox").success
    assert verdict_from_page_state("captcha").retryable
    assert verdict_from_page_state("risk_blocked").retryable
    assert not verdict_from_page_state("password_wrong").retryable
    assert is_terminal(AccountStatus.OK.value)
    assert is_terminal(AccountStatus.PASSWORD_WRONG.value)
    assert not is_terminal(AccountStatus.WAIT_VERIFY.value)
    assert describe(AccountStatus.OK.value) == "登录成功"


# ---------- 队列 ----------
def test_queue_priority_and_restore(db):
    q = TaskQueue(db)
    q.create("low@e.com", priority=0)
    q.create("high@e.com", priority=10)
    q.create("mid@e.com", priority=5)
    assert q.size() == 3
    assert q.get().account == "high@e.com"
    assert q.get().account == "mid@e.com"
    assert q.get().account == "low@e.com"
    assert q.get(timeout=0.1) is None

    q2 = TaskQueue(db)
    assert q2.restore() == 3  # 三个任务仍是 QUEUED 状态

    q2.close()
    assert q2.get(timeout=0.1) is None


def test_queue_clear(db):
    q = TaskQueue(db)
    q.create("a@e.com")
    q.create("b@e.com")
    assert q.clear() == 2
    assert q.size() == 0
    assert db.count_tasks()[TaskStatus.CANCELLED.value] == 2


# ---------- 断点 ----------
def test_checkpoint_manager(db):
    tid = db.create_task(Task(account="ck@e.com"))
    cm = CheckpointManager(db, task_id=tid, account="ck@e.com")
    cm.save(FlowStage.BROWSER_STARTED)
    cm.save(FlowStage.LOGIN_PAGE, state="login_email")
    cm.save(FlowStage.PASSWORD_INPUT)

    assert cm.current == FlowStage.PASSWORD_INPUT.value
    assert cm.reached(FlowStage.LOGIN_PAGE)
    assert not cm.reached(FlowStage.COMPLETED)
    assert cm.resume_stage() == FlowStage.PASSWORD_INPUT.value
    assert len(cm.timeline()) == 3
    assert db.get_task(tid).stage == FlowStage.PASSWORD_INPUT.value

    cm.clear()
    assert db.latest_checkpoint(tid) is None


# ---------- 代理 ----------
def test_proxy_config_modes():
    direct = ProxyConfig.from_dict({"enabled": False, "host": "127.0.0.1"})
    assert direct.direct and not direct.ports

    single = ProxyConfig.from_dict({"enabled": True, "host": "127.0.0.1", "single_port": 7890})
    assert single.ports == [7890]
    assert single.url_for(7890) == "http://127.0.0.1:7890"

    pool = ProxyConfig.from_dict(
        {"enabled": True, "mode": "pool", "host": "10.0.0.1", "port_start": 100, "port_end": 104}
    )
    assert pool.ports == [100, 101, 102, 103, 104]


def test_proxy_manager_weighting():
    pm = ProxyManager(
        {"enabled": True, "mode": "pool", "host": "127.0.0.1", "port_start": 1, "port_end": 3,
         "max_per_proxy": 2, "ip_info_lookup": False}
    )
    assert not pm.direct
    url = pm.pick()
    assert url.startswith("http://127.0.0.1:")

    # 让 :1 连续失败后被拉黑
    for _ in range(3):
        pm.record("http://127.0.0.1:1", False)
    assert pm._is_blacklisted("127.0.0.1:1")
    for _ in range(30):
        assert pm.pick() != "http://127.0.0.1:1"

    pm.record("http://127.0.0.1:2", True)
    snap = pm.snapshot()
    assert snap["ports"] == 3
    assert any(e["proxy"] == "127.0.0.1:2" and e["win"] == 1 for e in snap["tracker"])

    pm.penalize("http://127.0.0.1:3", penalty=4)
    assert pm._stats_of("127.0.0.1:3")["total"] >= 4

    pm.reset()
    assert not pm.snapshot()["tracker"]


def test_proxy_direct_mode():
    pm = ProxyManager({"enabled": False})
    assert pm.direct
    assert pm.pick() == ""
    assert pm.fresh("anything") == ""
    pm.record("", True)  # 不应报错


def test_timezone_resolution():
    assert resolve_timezone({"country": "JP", "timezone": ""}) == "Asia/Tokyo"
    assert resolve_timezone({"country": "??", "timezone": "Europe/Berlin"}) == "Europe/Berlin"
    assert resolve_timezone({"country": "??", "timezone": "UTC"}) == "Asia/Shanghai"
    assert resolve_timezone(None) == "Asia/Shanghai"
    assert resolve_geolocation({"loc": "35.68,139.69"}) == {"latitude": 35.68, "longitude": 139.69}
    assert resolve_geolocation({"loc": "bad"}) is None


# ---------- Profile ----------
def test_profile_manager_reuse(db, tmp_path):
    pm = ProfileManager(root=tmp_path / "profiles", reuse=True, db=db)
    p1 = pm.acquire(account="a@e.com")
    assert p1.profile_id == "acc_a_e.com"
    assert Path(p1.path).is_dir()
    pm.release(p1)

    p2 = pm.acquire(account="a@e.com")
    assert p2.profile_id == p1.profile_id
    assert p2.use_count == 2
    pm.release(p2)
    assert db.get_account_by_id  # sanity


def test_profile_manager_temporary(db, tmp_path):
    pm = ProfileManager(root=tmp_path / "profiles", reuse=False, cleanup_on_exit=True, db=db)
    p = pm.acquire(account="t@e.com")
    assert p.profile_id.startswith("tmp_")
    assert Path(p.path).is_dir()
    pm.release(p)
    assert not Path(p.path).exists()

    pm2 = ProfileManager(root=tmp_path / "profiles", reuse=False, cleanup_on_exit=False, db=db)
    kept = pm2.acquire(account="k@e.com")
    pm2.release(kept)
    assert Path(kept.path).is_dir()
    assert pm2.clear_temporary() >= 1
    assert not Path(kept.path).exists()


def test_fingerprint_seed():
    seeds = {make_fingerprint_seed("a@e.com") for _ in range(20)}
    assert len(seeds) > 1
    assert all(0 < s <= 0x7FFFFFFF for s in seeds)


# ---------- 浏览器参数 ----------
def test_build_args_and_proxy_option():
    args = build_args(locale="zh-CN", timezone="Asia/Tokyo", fingerprint_seed=42)
    assert "--lang=zh-CN" in args
    assert "--timezone=Asia/Tokyo" in args
    assert "--fingerprint=42" in args
    assert "--disable-blink-features=AutomationControlled" in args
    assert any("WebAuthentication" in a for a in args)

    plain = build_args()
    assert not any(a.startswith("--fingerprint=") for a in plain)

    assert build_proxy_option("") is None
    opt = build_proxy_option("http://127.0.0.1:7890")
    assert opt["server"] == "http://127.0.0.1:7890" and "localhost" in opt["bypass"]


# ---------- 验证码算法（纯函数部分） ----------
def test_captcha_pick_position_within_box():
    box = {"x": 100.0, "y": 200.0, "width": 60.0, "height": 40.0}
    cx, cy = 130.0, 220.0
    names = set()
    for _ in range(400):
        name, x, y = CaptchaSolver._pick_position(box, cx, cy)
        names.add(name.split(".")[0])
        assert 90 <= x <= 190
        assert 190 <= y <= 250
    assert {"center", "edge", "corner", "random"} <= names


def test_captcha_b2_mode_and_stats():
    reset_stats()
    assert CaptchaSolver._pick_b2_mode() in ("click", "dblclick")
    for _ in range(6):
        CaptchaSolver._record_attempt("click")
    snap = stats_snapshot()
    assert snap["b2_modes"]["click"]["attempts"] == 6
    reset_stats()
    assert stats_snapshot()["attempts"] == 0


# ---------- Resin 粘性代理池 ----------
def test_resin_parse_url_and_credentials():
    r = Resin({"enabled": True, "url": "http://127.0.0.1:2260/my-token", "platform": "Default"})
    assert r.usable
    assert r.server == "http://127.0.0.1:2260"
    assert r.token == "my-token"

    # 正向代理认证：Platform.Account:Token（Account 用邮箱前缀，默认）
    opt = r.forward_proxy_option("tom@mail.com")
    assert opt == {
        "server": "http://127.0.0.1:2260",
        "username": "Default.tom",
        "password": "my-token",
    }

    # identity_mode=email：完整邮箱作为 Account（含 @ . 安全，Resin 按第一个.最后一个:分割）
    r2 = Resin({"enabled": True, "url": "http://h:1/t", "platform": "P", "identity_mode": "email"})
    assert r2.forward_proxy_option("tom@mail.com")["username"] == "P.tom@mail.com"


def test_resin_reverse_proxy():
    r = Resin({"enabled": True, "url": "http://127.0.0.1:2260/my-token", "platform": "Default"})
    # 规范：<resin_url>/Platform/protocol/host/path?query
    url = r.reverse_url("https://api.example.com/healthz?q=1", "Tom")
    assert url == "http://127.0.0.1:2260/my-token/Default/https/api.example.com/healthz?q=1"
    assert r.reverse_headers("Tom@mail.com") == {"X-Resin-Account": "Tom"}

    # ws/wss 目标必须映射 http/https
    assert "/Default/https/ws.example.com/chat" in r.reverse_url("wss://ws.example.com/chat", "Tom")
    import pytest
    with pytest.raises(ValueError):
        r.reverse_url("ftp://x.com/a", "Tom")


def test_resin_disabled_and_identity_stability():
    off = Resin({"enabled": False, "url": "http://h:1/t"})
    assert not off.usable
    assert off.forward_proxy_option("a@b.com") is None

    r = Resin({"enabled": True, "url": "http://h:1/t", "identity_mode": "email_prefix"})
    # 同一账号标识稳定（多次调用一致）
    assert r.account_identity("Tom@mail.com") == r.account_identity("Tom@mail.com") == "Tom"
    # 无 @ 的账号原样使用
    assert r.account_identity("plainname") == "plainname"
    # 非法 identity_mode 回退默认
    r2 = Resin({"enabled": True, "url": "http://h:1/t", "identity_mode": "bad"})
    assert r2.identity_mode == "email_prefix"


def test_resin_inherit_lease_request():
    r = Resin({"enabled": True, "url": "http://127.0.0.1:2260/my-token", "platform": "Default"})
    url, body = r.build_inherit_request("temp-abc", "tom")
    assert url == "http://127.0.0.1:2260/my-token/api/v1/Default/actions/inherit-lease"
    assert body == {"parent_account": "temp-abc", "new_account": "tom"}


def test_resin_singleton_and_snapshot(tmp_path):
    reset_resin()
    r = get_resin({"enabled": True, "url": "http://h:1/token", "platform": "MyPlat"})
    assert get_resin() is r  # 单例
    snap = r.snapshot()
    assert snap["enabled"] is True and snap["platform"] == "MyPlat"
    assert "token" not in json.dumps(snap)  # 不泄露 token
    reset_resin()


# ---------- 流程注册 ----------
def test_flow_registry():
    flows = list_flows()
    assert "login" in flows
    assert flows["login"].name == "login"


def test_flow_stage_order():
    assert FlowStage.CREATED.index() == 0
    assert FlowStage.COMPLETED.index() > FlowStage.WAIT_VERIFY.index()
    assert FlowStage.FAILED.index() == -1


# ---------- API ----------
def test_api_auth_and_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OA_DATABASE__PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("OA_LOGGER__DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("OA_PROFILE__ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv("OA_SYSTEM__ACCOUNTS_FILE", str(tmp_path / "accounts.txt"))

    import database
    import proxy as proxy_pkg
    import browser as browser_pkg
    import task as task_pkg

    database.reset_db()
    proxy_pkg.reset_proxy_manager()
    browser_pkg.reset_profile_manager()
    browser_pkg.reset_browser_manager()
    task_pkg.reset_task_manager()

    from api import create_app

    cfg = load_config(use_cache=False)
    cfg.set("api.token", "test-token-123")
    app = create_app(cfg)
    client = TestClient(app)

    # meta 免认证
    assert client.get("/api/meta").json()["auth_enabled"] is True
    # 缺 token → 401
    assert client.get("/api/stats").status_code == 401
    assert client.get("/api/stats", headers={"X-API-Token": "wrong"}).status_code == 401

    h = {"X-API-Token": "test-token-123"}
    assert client.get("/api/stats", headers=h).status_code == 200

    r = client.post("/api/accounts", json={"account": "api@e.com", "password": "pw"}, headers=h)
    assert r.status_code == 200 and r.json()["ok"]

    r = client.post("/api/accounts/import", json={"text": "x@e.com----p1\ny@e.com----p2"}, headers=h)
    assert r.json()["imported"] == 2

    accounts = client.get("/api/accounts", headers=h).json()
    assert accounts["total"] == 3
    assert any(a["account"] == "api@e.com" for a in accounts["items"])
    assert all(a["password"] == "***" for a in accounts["items"])

    r = client.post("/api/tasks", json={"account": "api@e.com", "type": "login"}, headers=h)
    task_id = r.json()["task"]["id"]
    assert client.get(f"/api/tasks/{task_id}", headers=h).status_code == 200
    assert client.post(f"/api/tasks/{task_id}/cancel", headers=h).json()["ok"]
    assert client.get("/api/tasks", headers=h).json()["items"]

    assert client.get("/api/queue", headers=h).status_code == 200
    # 浏览器明细在独立执行进程内，面板返回占位符
    assert client.get("/api/browsers", headers=h).json()["active"] == "-"
    assert client.get("/api/profiles", headers=h).status_code == 200
    assert client.get("/api/proxy", headers=h).json()["direct"] is True
    assert client.get("/api/logs", headers=h).status_code == 200
    # 配置接口不得泄露 token
    assert client.get("/api/config", headers=h).json()["api"]["token"] == "***"
    # 面板首页可访问
    assert client.get("/").status_code == 200

    task_pkg.reset_task_manager()
    database.reset_db()
