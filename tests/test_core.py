"""不依赖浏览器的单元测试：配置、数据库、账号、队列、代理、Profile、检测器、内核定位。"""

from __future__ import annotations

import json
import sys
import tempfile
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
    assert cfg.get("flow.login_url").startswith("https://")
    assert cfg.get("logger.level") in ("DEBUG", "INFO", "WARN", "ERROR")


def test_config_has_no_web_api_section(cfg):
    """桌面版不再有 Web 服务，api.* 配置已移除。"""
    assert cfg.section("api") == {}


def test_config_app_and_data_root(cfg):
    """路径地基：开发模式下两个根目录都存在且为绝对路径。"""
    from config import APP_ROOT, DATA_ROOT

    assert APP_ROOT.is_absolute()
    assert DATA_ROOT.is_absolute()
    assert cfg.root == APP_ROOT
    assert cfg.data_root == DATA_ROOT
    # 用户数据走 DATA_ROOT，程序资源走 APP_ROOT
    assert cfg.resolve("data/x.db").parent.parent == DATA_ROOT
    assert cfg.resolve_app("Chromium/x").parent.parent == APP_ROOT
    # 绝对路径不被重写
    abs_in = Path(tempfile.gettempdir()).resolve() / "oa_abs_probe.db"
    assert cfg.resolve(str(abs_in)) == abs_in
    assert cfg.resolve_app(str(abs_in)) == abs_in


def test_config_dotted_and_resolve(cfg):
    cfg.set("browser.headless", True)
    assert cfg.get("browser.headless") is True
    assert cfg.get("nope.missing", "fallback") == "fallback"
    assert cfg.resolve("data/x.db").is_absolute()
    assert cfg.path_of("database.path").name == "app.db"


def test_detect_account_unblocked_priority():
    """人机验证后的「已取消阻止你的帐户」页必须识别为放行页，而非 unknown/锁定。"""
    from flow.detector import PageDetector

    # 页面同时带安全类文案（帮助我们保护你的帐户），正是之前误判的原因
    texts = [
        "已取消阻止你的帐户",
        "如果认为可能有人访问过你的帐户，请查看你的近期活动",
        "继续",
        "获取有关提高帐户安全性的提示",
    ]
    page = _FakePage(texts)
    det = PageDetector(page)
    assert det.is_account_unblocked() is True
    assert det.detect() == "account_unblocked"

    # 普通页不该误报
    assert PageDetector(_FakePage(["输入密码"])).is_account_unblocked() is False

    # 「帐户恢复已被阻止」含「一些异常活动」字样，必须识别为 recovery_blocked 而非 risk_blocked
    blocked = PageDetector(_FakePage(["帐户恢复已被阻止", "我们检测到一些异常活动，并已阻止恢复此帐户"]))
    assert blocked.is_recovery_blocked() is True
    assert blocked.detect() == "recovery_blocked"
    from account.status import verdict_from_page_state
    assert verdict_from_page_state("recovery_blocked").retryable is False


class _FakePage:
    """最小伪页面：只支持 detector 用到的 get_by_text/locator/url。"""

    def __init__(self, texts):
        self._texts = texts
        self.url = "https://login.live.com/ppsecure/post.srf"

    def get_by_text(self, text, exact=False):
        hit = sum(1 for t in self._texts if text in t)
        return _FakeLocator(hit)

    def locator(self, selector):
        return _FakeLocator(0)

    def frame_locator(self, selector):
        return self


class _FakeLocator:
    def __init__(self, n):
        self._n = n
        self.first = self

    def count(self):
        return self._n

    def is_visible(self):
        return self._n > 0

    def text_content(self, timeout=None):
        return ""


def test_launch_no_use_before_assignment():
    """静态检查 _launch 里不存在局部变量先用后赋值（曾因 common 提前使用导致启动失败）。"""
    import ast

    src = (ROOT / "browser" / "browser.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_launch"
    )
    first_store: dict[str, int] = {}
    first_load: dict[str, int] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            table = first_store if isinstance(node.ctx, ast.Store) else first_load
            table.setdefault(node.id, node.lineno)
    for name, load_line in first_load.items():
        if name in first_store:
            assert first_store[name] <= load_line, (
                f"_launch 中 {name} 在行 {load_line} 先使用，却到行 {first_store[name]} 才赋值"
            )


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


def test_resin_forward_proxy_url_embeds_credentials():
    """requests 用的代理 URL 必须内嵌凭证（auth= 参数不作用于代理，会得 407）。"""
    r = Resin({"enabled": True, "url": "http://127.0.0.1:2260/my-token", "platform": "Default"})
    assert r.forward_proxy_url("tom@mail.com") == "http://Default.tom:my-token@127.0.0.1:2260"

    # 完整邮箱模式：@ 必须百分号转义，否则 netloc 解析错位
    r2 = Resin({"enabled": True, "url": "http://h:1/t", "platform": "P", "identity_mode": "email"})
    url = r2.forward_proxy_url("tom@mail.com")
    assert url == "http://P.tom%40mail.com:t@h:1"
    from urllib.parse import urlparse, unquote
    assert urlparse(url).hostname == "h"                      # 不会把 mail.com 当主机
    assert unquote(urlparse(url).username) == "P.tom@mail.com"  # 反解后仍是原始凭证
    assert r2.forward_proxy_url("") is None


def test_resin_test_connection_guard():
    """未启用/未配置时测试接口直接返回失败，不发请求。"""
    off = Resin({"enabled": False, "url": "http://h:1/t"})
    r = off.test_connection()
    assert r["ok"] is False and "未启用" in r["detail"]

    no_token = Resin({"enabled": True, "url": "http://127.0.0.1:2260"})
    assert no_token.test_connection()["ok"] is False


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


# ---------- Chromium 内核定位 ----------
def test_kernel_resolve_explicit_absolute(tmp_path, monkeypatch):
    """显式绝对路径存在时原样返回。"""
    from browser.kernel import resolve_executable

    fake = tmp_path / "chrome.exe"
    fake.write_text("x", encoding="utf-8")
    monkeypatch.setenv("OA_BROWSER__EXECUTABLE_PATH", str(fake))
    cfg = load_config(use_cache=False)
    assert resolve_executable(cfg) == str(fake)


def test_kernel_resolve_patchright_keyword(monkeypatch):
    """patchright 关键字 → 返回空串，交给 Playwright 默认查找。"""
    from browser.kernel import resolve_executable

    monkeypatch.setenv("OA_BROWSER__EXECUTABLE_PATH", "patchright")
    cfg = load_config(use_cache=False)
    assert resolve_executable(cfg) == ""


def test_kernel_resolve_missing_path_raises(monkeypatch, tmp_path):
    """显式路径不存在且无内核可回退时报明确错误，而不是静默启动失败。"""
    from browser.kernel import resolve_executable

    monkeypatch.setattr("browser.kernel.find_fingerprint", lambda root: None)
    monkeypatch.setenv("OA_BROWSER__EXECUTABLE_PATH", str(tmp_path / "nope" / "chrome.exe"))
    cfg = load_config(use_cache=False)
    with pytest.raises(FileNotFoundError):
        resolve_executable(cfg)


def test_kernel_resolve_stale_version_path_falls_back(monkeypatch, tmp_path):
    """内核升级后旧的带版本号路径失效，应回退到自动定位而不是直接失败。"""
    from browser.kernel import resolve_executable

    newer = tmp_path / "fingerprint" / "chrome.exe"
    newer.parent.mkdir(parents=True)
    newer.write_text("x", encoding="utf-8")
    monkeypatch.setattr("browser.kernel.find_fingerprint", lambda root: newer)
    monkeypatch.setenv(
        "OA_BROWSER__EXECUTABLE_PATH",
        "browsers/fingerprint-chromium/ungoogled-chromium_1.2.3_windows_x64/chrome.exe",
    )
    cfg = load_config(use_cache=False)
    assert resolve_executable(cfg) == str(newer)


def test_kernel_describe_shape(monkeypatch):
    """GUI 浏览器页依赖的快照字段必须齐全。"""
    from browser.kernel import describe

    monkeypatch.setenv("OA_BROWSER__EXECUTABLE_PATH", "patchright")
    cfg = load_config(use_cache=False)
    snap = describe(cfg)
    for key in (
        "configured",
        "active_kernel",
        "active_path",
        "fingerprint_available",
        "patchright_bundled",
        "error",
    ):
        assert key in snap


def test_bundled_patchright_layout(tmp_path):
    """随包 patchright 内核支持 chrome-win/ 与 chromium-XXXX/chrome-win/ 两种布局。"""
    from browser.kernel import bundled_patchright

    root = tmp_path / "Chromium" / "patchright" / "chromium-1169" / "chrome-win"
    root.mkdir(parents=True)
    exe = root / ("chrome.exe" if sys.platform == "win32" else "chrome")
    exe.write_text("x", encoding="utf-8")
    assert bundled_patchright(tmp_path) == exe


# ---------- 桌面层：执行进程桥接 ----------
def test_worker_command_frozen_uses_argv_flag(monkeypatch):
    """打包后 sys.executable 是 EXE 自身，必须走 --exec-worker 而不是 main.py。"""
    from desktop.bridge.worker_proc import build_worker_command

    monkeypatch.setattr("desktop.bridge.worker_proc.FROZEN", True)
    cfg = load_config(use_cache=False)
    cmd = build_worker_command(cfg, workers=4, executable="C:/app/OutlookAutomation.exe")
    assert cmd[0] == "C:/app/OutlookAutomation.exe"
    assert "--exec-worker" in cmd
    assert "main.py" not in " ".join(cmd)
    assert cmd[cmd.index("--workers") + 1] == "4"


def test_worker_command_dev_uses_main_py(monkeypatch):
    """开发模式仍然走 python main.py work，保持现有可调试性。"""
    from desktop.bridge.worker_proc import build_worker_command

    monkeypatch.setattr("desktop.bridge.worker_proc.FROZEN", False)
    cfg = load_config(use_cache=False)
    cmd = build_worker_command(cfg, workers=2, executable="python.exe")
    assert cmd[0] == "python.exe"
    assert cmd[1].endswith("main.py")
    assert cmd[2] == "work"
    assert "--exec-worker" not in cmd


def test_ipc_message_roundtrip():
    """IPC 用行分隔 JSON，日志内容含换行也不能破坏分帧。"""
    from desktop.bridge.ipc import decode_stream, encode_message

    payload = encode_message({"kind": "log", "message": "line1\nline2"})
    payload += encode_message({"kind": "stats", "processed": 3})
    messages, rest = decode_stream(payload)
    assert rest == b""
    assert [m["kind"] for m in messages] == ["log", "stats"]
    assert messages[0]["message"] == "line1\nline2"


def test_ipc_partial_frame_is_buffered():
    """半条消息必须留在缓冲区，等下一批数据到齐再解析。"""
    from desktop.bridge.ipc import decode_stream, encode_message

    full = encode_message({"kind": "log", "message": "hello"})
    head, tail = full[:5], full[5:]
    messages, rest = decode_stream(head)
    assert messages == [] and rest == head
    messages, rest = decode_stream(rest + tail)
    assert rest == b"" and messages[0]["message"] == "hello"


def test_ipc_ignores_corrupt_line():
    """坏行被丢弃，不能让后续正常消息一起丢失。"""
    from desktop.bridge.ipc import decode_stream, encode_message

    data = b"{not json}\n" + encode_message({"kind": "log", "message": "ok"})
    messages, rest = decode_stream(data)
    assert rest == b""
    assert len(messages) == 1 and messages[0]["message"] == "ok"
