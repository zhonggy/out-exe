"""关于与更新页冒烟：版本比较 + 检查/下载/安装的按钮状态机。

全程不碰网络 —— GitHub API 响应由本地桩伪造。理由有两条：CI 里不该依赖
外网可达，且 GitHub 未认证请求每小时只有 60 次，跑几遍测试就限流了。

重点验证四类容易写错的事：

1. **版本比较不能用字符串。** ``"1.10.0" > "1.9.0"`` 字符串比较是 False，
   会导致有新版却提示「已是最新」。
2. **按钮状态机不留死结。** 检查失败、下载失败后必须回到可重试状态；
   这个项目栽过「回调丢失导致按钮永久禁用」的坑，所以每个失败分支都要断言。
3. **半个安装包不能当成可安装。** 下载中断留下的文件同样存在，
   直接拿去装会报「安装包损坏」，用户很难联想到是上次断网。
4. **同版本不提示更新。** 边界相等时 has_update 必须是 False。
"""

from __future__ import annotations

import json
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

#: 伪造的 Release 响应。字段名与 GitHub API 一致。
FAKE_ASSET_SIZE = 4096


def fake_release(tag: str, with_asset: bool = True) -> dict:
    assets = []
    if with_asset:
        assets = [
            {
                "name": "OutlookAutomation-Setup.exe",
                "browser_download_url": "https://example.invalid/Setup.exe",
                "size": FAKE_ASSET_SIZE,
            }
        ]
    return {
        "tag_name": tag,
        "name": f"Release {tag}",
        "body": "- 新增账号页勾选框\n- 修复若干问题",
        "published_at": "2026-08-27T10:00:00Z",
        "html_url": "https://example.invalid/releases/tag/" + tag,
        "prerelease": False,
        "draft": False,
        "assets": assets,
    }


def main() -> int:
    if not os.environ.get("OA_DATA_DIR"):
        print("[FAIL] 必须设置 OA_DATA_DIR，避免污染真实数据")
        return 1

    sys.argv = ["main.py"]
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    import desktop.views.about_view as av
    from desktop.bridge.tasks import wait_for_idle
    from desktop.context import AppContext
    from updater import compare_versions, is_newer, parse_version

    # confirm() 直接放行，否则冒烟会卡在模态框上
    av.confirm = lambda *a, **k: True

    failures: list = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        if ok:
            print(f"[OK]   {label:<26} {detail}")
        else:
            failures.append(f"{label}: {detail}")
            print(f"[FAIL] {label:<26} {detail}")

    # ---------- 1. 版本比较 ----------
    print("=== 版本比较 ===")
    check("1.10.0 > 1.9.0", is_newer("1.10.0", "1.9.0"), "字符串比较会判错")
    check("v 前缀等价", compare_versions("v1.2.0", "1.2.0") == 0, "")
    check("补零等价 1.2 == 1.2.0", compare_versions("1.2", "1.2.0") == 0, "")
    check("同版本不算新", not is_newer("1.2.0", "1.2.0"), "")
    check("旧版不算新", not is_newer("1.1.0", "1.2.0"), "")
    check("预发布后缀忽略", compare_versions("1.2.0-build7", "1.2.0") == 0, "")
    check("脏数据视为最旧", parse_version("abc") == (0,), f"{parse_version('abc')}")

    # ---------- 2. 构造页面 ----------
    ctx = AppContext()
    view = av.AboutView(ctx)
    current = view.updater.current_version

    def drain(seconds: float = 3.0) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        wait_for_idle(6000)
        for _ in range(40):
            app.processEvents()
            time.sleep(0.01)

    drain(2.0)

    print()
    print("=== 程序信息卡片 ===")
    check("信息行齐全", len(view.info_rows) >= 10, f"{len(view.info_rows)} 行")
    check(
        "版本行有值",
        view.info_rows["version"].value_text() == current,
        view.info_rows["version"].value_text(),
    )
    check(
        "数据目录行有值",
        str(ctx.cfg.data_root) in view.info_rows["data_dir"].value_text(),
        view.info_rows["data_dir"].value_text(),
    )
    copied = []
    view._on_copy_info()
    drain(0.5)
    check("复制信息不报错", True, "")

    print()
    print("=== 初始按钮状态 ===")
    check("检查更新可点", view.btn_check.isEnabled(), "")
    check("下载禁用", not view.btn_download.isEnabled(), "未检查前不该能下载")
    check("重启更新禁用", not view.btn_apply.isEnabled(), "未下载前不该能安装")
    check("发布页可点", view.btn_release_page.isEnabled(), "")

    # ---------- 3. 检查到新版本 ----------
    print()
    print("=== 检查更新：有新版 ===")
    newer = "9.9.9"
    view.updater._get_json = lambda url: fake_release(f"v{newer}")
    view._on_check()
    drain(3.0)

    check("识别为有新版", view._has_update, f"latest={getattr(view._latest, 'version', None)}")
    check(
        "最新版本行正确",
        view.update_rows["latest"].value_text().startswith(newer),
        view.update_rows["latest"].value_text(),
    )
    check("下载按钮已亮起", view.btn_download.isEnabled(), "")
    check("重启更新仍禁用", not view.btn_apply.isEnabled(), "还没下载")
    check("更新说明已填充", "勾选框" in view.notes.toPlainText(), "")

    # ---------- 4. 下载 ----------
    print()
    print("=== 下载更新 ===")
    target = view.updater.local_installer(view._latest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)

    # 伪造下载：直接落一个大小正确的文件，避免真的走网络
    real_download = view.updater.download

    def stub_download(info, progress=None):
        path = view.updater.local_installer(info)
        path.write_bytes(b"\0" * FAKE_ASSET_SIZE)
        if progress:
            progress(FAKE_ASSET_SIZE, FAKE_ASSET_SIZE, "stub")
        return path

    view.updater.download = stub_download
    view._on_download()
    drain(3.0)

    check("安装包已落地", target.is_file(), str(target))
    check("重启更新已亮起", view.btn_apply.isEnabled(), "")
    check(
        "本地安装包行有值",
        str(target) in view.update_rows["local"].value_text(),
        view.update_rows["local"].value_text(),
    )

    # ---------- 5. 完整/残缺安装包识别 ----------
    print()
    print("=== 安装包完整性 ===")
    check("完整包判定为已下载", view.updater.is_downloaded(view._latest), "")
    target.write_bytes(b"\0" * (FAKE_ASSET_SIZE // 2))
    check(
        "半个包不算已下载",
        not view.updater.is_downloaded(view._latest),
        "断点续传残留不该被当成可安装",
    )
    target.write_bytes(b"\0" * FAKE_ASSET_SIZE)

    # 已存在完整包时不重复下载（用真实实现验证复用逻辑）
    view.updater.download = real_download
    reused = view.updater.download(view._latest)
    check("已存在则复用不重下", reused == target and target.is_file(), str(reused))
    view.updater.download = stub_download

    # ---------- 6. 立即重启并更新 ----------
    print()
    print("=== 立即重启并更新 ===")
    launched: list = []
    view.updater.apply_and_restart = lambda path: launched.append(Path(path))
    quit_called: list = []
    view._quit_for_update = lambda: quit_called.append(True)
    view._on_apply()
    drain(1.0)

    check("安装程序被拉起", len(launched) == 1, f"{launched}")
    check("拉起的是下载好的包", launched and launched[0] == target, f"{launched}")
    check("触发退出流程", bool(quit_called), "安装前必须退出自己，否则文件被占用")

    # ---------- 7. 已是最新 ----------
    print()
    print("=== 检查更新：已是最新 ===")
    view.updater._get_json = lambda url: fake_release(f"v{current}")
    view._installer = None
    view._on_check()
    drain(3.0)

    check("判定为已是最新", not view._has_update, f"current={current}")
    check("下载保持禁用", not view.btn_download.isEnabled(), "")
    check("重启更新保持禁用", not view.btn_apply.isEnabled(), "")
    check("检查按钮已恢复", view.btn_check.isEnabled(), "")

    # ---------- 8. 检查失败不留死结 ----------
    print()
    print("=== 检查失败 ===")

    def boom(url):
        raise OSError("stub network down")

    view.updater._get_json = boom
    view._on_check()
    drain(3.0)

    check("状态显示失败", "失败" in view.update_rows["state"].value_text(),
          view.update_rows["state"].value_text())
    check("检查按钮可重试", view.btn_check.isEnabled(), "失败后必须能再点")
    check("发布页按钮可用", view.btn_release_page.isEnabled(), "手动下载的退路")
    check("下载保持禁用", not view.btn_download.isEnabled(), "")

    # ---------- 9. 没有资产的 Release ----------
    print()
    print("=== Release 无安装包 ===")
    view.updater._get_json = lambda url: fake_release("v9.9.9", with_asset=False)
    view._on_check()
    drain(3.0)
    check("识别为有新版", view._has_update, "")
    check("但下载被禁用", not view.btn_download.isEnabled(), "没有资产不该允许下载")
    check(
        "提示无安装包",
        "安装包" in view.update_rows["state"].value_text()
        or "安装包" in view.notes.toPlainText(),
        view.update_rows["state"].value_text(),
    )

    # ---------- 10. 下载失败不留死结 ----------
    print()
    print("=== 下载失败 ===")
    view.updater._get_json = lambda url: fake_release("v9.9.9")
    view._on_check()
    drain(3.0)

    def download_boom(info, progress=None):
        raise OSError("stub disk full")

    view.updater.download = download_boom
    view._on_download()
    drain(3.0)
    check("状态显示下载失败", "失败" in view.update_rows["state"].value_text(),
          view.update_rows["state"].value_text())
    check("下载按钮可重试", view.btn_download.isEnabled(), "失败后必须能再点")
    check("重启更新仍禁用", not view.btn_apply.isEnabled(), "没有可用安装包")

    # ---------- 收尾 ----------
    view.updater.cleanup_old()
    ctx.shutdown()

    print()
    if failures:
        print(f"更新页冒烟失败：{len(failures)} 项")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("更新页冒烟通过：版本比较、检查/下载/安装链路、失败恢复均正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
