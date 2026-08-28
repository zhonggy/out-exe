"""关于与更新页。

两张卡片：

- **程序信息** —— 版本、运行模式、内核、Python/Qt、路径、数据库体积。
  排查用户问题时第一句话总是"你什么版本、装哪了"，所以这些值都可以选中复制。
- **更新** —— 检查 / 下载 / 重启安装 / 打开发布页。

四个按钮的状态机是这页唯一复杂的地方，规则：

    初始           → 只有「检查更新」和「打开发布页」可点
    检查到新版本   → 「下载更新」亮起
    下载完成       → 「立即重启并更新」亮起
    已是最新       → 下载与重启保持禁用

任何一步失败都回到"可重试"状态，不留死结。为什么不做自动静默更新：
程序装在 Program Files，覆盖文件需要管理员权限，且执行进程可能正跑着任务
—— 什么时候重启必须由用户决定。
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QGroupBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from browser import describe_kernel
from config import APP_NAME, APP_VERSION, FROZEN
from updater import get_update_manager, human_bytes

from ..bridge.tasks import run_async
from ..theme import COLOR_FAIL, COLOR_OK, COLOR_WARN, TEXT_DIM
from .widgets import (
    KeyValueRow,
    button,
    confirm,
    error,
    hint_label,
    info,
    notify,
    title_label,
    toolbar,
)


class AboutView(QWidget):
    """关于与更新。"""

    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self.updater = get_update_manager(context.cfg, logger=getattr(context, "log", None))
        #: 最近一次检查到的 Release，下载与安装都依赖它
        self._latest = None
        #: 上次检查的结论：是否有新版本
        self._has_update = False
        #: 已下载到本地的安装包路径
        self._installer: Optional[Path] = None
        self._busy = False
        self._build()
        self.refresh()

    # ---------- 构建 ----------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)
        outer.addWidget(title_label("关于与更新"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addWidget(self._build_info_card())
        layout.addWidget(self._build_update_card())
        layout.addStretch(1)

        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

    def _build_info_card(self) -> QWidget:
        box = QGroupBox("程序信息")
        layout = QVBoxLayout(box)

        self.info_rows: Dict[str, KeyValueRow] = {
            "name": KeyValueRow("程序名称"),
            "version": KeyValueRow("当前版本"),
            "mode": KeyValueRow("运行模式"),
            "kernel": KeyValueRow("浏览器内核", elide=True),
            "python": KeyValueRow("Python"),
            "qt": KeyValueRow("Qt / PySide6"),
            "os": KeyValueRow("操作系统"),
            "app_dir": KeyValueRow("程序目录", elide=True),
            "data_dir": KeyValueRow("数据目录", elide=True),
            "config": KeyValueRow("配置文件", elide=True),
            "db": KeyValueRow("数据库", elide=True),
            "accounts": KeyValueRow("账号总数"),
            "repo": KeyValueRow("发布仓库", elide=True),
        }
        for row in self.info_rows.values():
            layout.addWidget(row)

        self.btn_copy_info = button("复制信息", tooltip="把上面这些信息复制到剪贴板，便于反馈问题")
        self.btn_open_data_dir = button("打开数据目录")
        self.btn_copy_info.clicked.connect(self._on_copy_info)
        self.btn_open_data_dir.clicked.connect(self._on_open_data_dir)
        layout.addWidget(toolbar(self.btn_copy_info, self.btn_open_data_dir, stretch_at=1))

        layout.addWidget(
            hint_label(
                "反馈问题时请附上「复制信息」的内容与「运行日志」页导出的日志，"
                "能省掉大量来回确认。"
            )
        )
        return box

    def _build_update_card(self) -> QWidget:
        box = QGroupBox("更新")
        layout = QVBoxLayout(box)

        self.update_rows: Dict[str, KeyValueRow] = {
            "state": KeyValueRow("状态"),
            "latest": KeyValueRow("最新版本"),
            "published": KeyValueRow("发布时间"),
            "size": KeyValueRow("安装包"),
            "local": KeyValueRow("本地安装包", elide=True),
        }
        for row in self.update_rows.values():
            layout.addWidget(row)

        self.btn_check = button("检查更新", "primary")
        self.btn_download = button("下载更新", "outline", enabled=False)
        self.btn_apply = button("立即重启并更新", "danger", enabled=False)
        self.btn_release_page = button("打开发布页")

        self.btn_check.clicked.connect(self._on_check)
        self.btn_download.clicked.connect(self._on_download)
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_release_page.clicked.connect(self._on_release_page)

        layout.addWidget(
            toolbar(
                self.btn_check,
                self.btn_download,
                self.btn_apply,
                self.btn_release_page,
                stretch_at=3,
            )
        )

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMinimumHeight(140)
        self.notes.setPlainText("点击「检查更新」查询最新版本。")
        layout.addWidget(self.notes, 1)

        layout.addWidget(
            hint_label(
                "更新方式：下载官方安装包后覆盖安装，用户数据（账号、数据库、"
                "登录态 Profile）不会被清除。<br>"
                "安装需要管理员权限；若执行进程正在跑任务，安装程序会提示先结束它。"
            )
        )
        return box

    # ---------- 刷新 ----------
    def refresh(self) -> None:
        cfg = self.ctx.cfg

        self.info_rows["name"].set_value(APP_NAME)
        self.info_rows["version"].set_value(APP_VERSION)
        self.info_rows["mode"].set_value(
            "安装版（打包产物）" if FROZEN else "开发模式（源码运行）"
        )

        try:
            kernel = describe_kernel(cfg)
            label = str(kernel.get("active_kernel") or "-")
            path = kernel.get("active_path") or "（由 Playwright 默认查找）"
            self.info_rows["kernel"].set_value(f"{label}  ·  {path}")
        except Exception as exc:  # 内核信息拿不到不该让整页崩
            self.info_rows["kernel"].set_value(f"读取失败：{exc}", COLOR_WARN)

        self.info_rows["python"].set_value(platform.python_version())
        try:
            from PySide6 import __version__ as pyside_version
            from PySide6.QtCore import qVersion

            self.info_rows["qt"].set_value(f"Qt {qVersion()} / PySide6 {pyside_version}")
        except Exception:
            self.info_rows["qt"].set_value("-")

        self.info_rows["os"].set_value(f"{platform.system()} {platform.release()}")
        self.info_rows["app_dir"].set_value(str(cfg.root))
        self.info_rows["data_dir"].set_value(str(cfg.data_root))
        self.info_rows["config"].set_value(str(cfg.source_path or "（内置默认值）"))

        db_path = cfg.path_of("database.path", "data/app.db")
        if db_path.is_file():
            self.info_rows["db"].set_value(
                f"{db_path}  ·  {human_bytes(db_path.stat().st_size)}"
            )
        else:
            self.info_rows["db"].set_value(f"{db_path}（未创建）", TEXT_DIM)

        repo = self.updater.repo
        self.info_rows["repo"].set_value(
            f"github.com/{repo}" if repo else "未配置", COLOR_WARN if not repo else None
        )

        # 账号总数走后台：库大时 count 也要几十毫秒
        run_async(
            lambda: self.ctx.am.stats(),
            on_result=lambda s: self.info_rows["accounts"].set_value(
                f"{s.get('total', 0)} 个"
            ),
            on_error=lambda _msg: self.info_rows["accounts"].set_value("-"),
        )

        self._sync_update_rows()

    def update_state(self, snapshot: Dict[str, Any]) -> None:
        """主窗口定时器调用。这页没有需要秒级刷新的内容，故意留空实现。

        存在这个方法只是为了满足主窗口的分发约定（hasattr 检查）。
        """
        return

    def _sync_update_rows(self) -> None:
        if self._latest is None:
            self.update_rows["state"].set_value("尚未检查", TEXT_DIM)
            self.update_rows["latest"].set_value("-")
            self.update_rows["published"].set_value("-")
            self.update_rows["size"].set_value("-")
            self.update_rows["local"].set_value("-")
            return

        info_obj = self._latest
        if self._installer is not None:
            self.update_rows["local"].set_value(str(self._installer), COLOR_OK)
        elif self.updater.is_downloaded(info_obj):
            self._installer = self.updater.local_installer(info_obj)
            self.update_rows["local"].set_value(str(self._installer), COLOR_OK)
        else:
            self.update_rows["local"].set_value("未下载", TEXT_DIM)

        self.update_rows["latest"].set_value(
            info_obj.version + ("（预发布）" if info_obj.prerelease else "")
        )
        self.update_rows["published"].set_value(
            info_obj.published_at.replace("T", " ").replace("Z", " UTC") or "-"
        )
        self.update_rows["size"].set_value(
            f"{info_obj.asset_name or '无资产'}  ·  {info_obj.size_text}"
        )

    # ---------- 按钮状态 ----------
    def _set_busy(self, busy: bool, message: str = "") -> None:
        """统一收口按钮禁用状态。

        每个回调各自 setEnabled 很容易漏掉失败分支，导致按钮永久禁用
        （这个项目在别处栽过同样的坑）。所以只在这一处切换。
        """
        self._busy = busy
        self.btn_check.setEnabled(not busy)
        self.btn_release_page.setEnabled(not busy)

        has_update = bool(self._latest is not None and self._has_update)
        downloadable = has_update and bool(self._latest and self._latest.has_asset)
        self.btn_download.setEnabled(not busy and downloadable and self._installer is None)
        self.btn_apply.setEnabled(not busy and self._installer is not None)

        if message:
            self.update_rows["state"].set_value(message, COLOR_WARN if busy else None)

    # ---------- 检查更新 ----------
    def _on_check(self) -> None:
        self._set_busy(True, "正在检查…")
        self.notes.setPlainText("正在向 GitHub 查询最新版本…")

        run_async(
            self.updater.check,
            on_result=self._on_checked,
            on_error=self._on_check_failed,
        )

    def _on_checked(self, result) -> None:
        if not result.ok:
            self._on_check_failed(result.error)
            return

        self._latest = result.latest
        self._has_update = result.has_update
        self._installer = None
        self._sync_update_rows()

        if result.has_update:
            self.update_rows["state"].set_value(
                f"发现新版本 {result.latest.version}", COLOR_WARN
            )
            body = result.latest.notes.strip() or "（该版本没有填写更新说明）"
            self.notes.setPlainText(
                f"当前版本：{result.current}\n"
                f"最新版本：{result.latest.version}\n"
                f"{'-' * 48}\n{body}"
            )
            notify(self, f"发现新版本 {result.latest.version}", "warn")
            if not result.latest.has_asset:
                self.update_rows["state"].set_value(
                    "有新版本，但该 Release 未附带安装包", COLOR_WARN
                )
                self.notes.appendPlainText(
                    "\n\n注意：这个 Release 没有可下载的安装包，"
                    "可能构建尚未完成。请稍后再试或到发布页查看。"
                )
        else:
            self.update_rows["state"].set_value("已是最新版本", COLOR_OK)
            self.notes.setPlainText(
                f"当前版本 {result.current} 已是最新。\n\n"
                f"最新发布：{result.latest.version}"
                f"（{result.latest.published_at.replace('T', ' ').replace('Z', ' UTC')}）"
            )
            notify(self, "已是最新版本")

        self._set_busy(False)

    def _on_check_failed(self, message: str) -> None:
        self._latest = None
        self._has_update = False
        self.update_rows["state"].set_value("检查失败", COLOR_FAIL)
        self.notes.setPlainText(
            f"检查更新失败：{message}\n\n"
            "常见原因：本机无法访问 GitHub、代理未生效、GitHub API 限流"
            "（未认证请求每小时 60 次）。\n"
            "可以点「打开发布页」手动查看最新版本。"
        )
        self._set_busy(False)
        notify(self, f"检查更新失败：{message}", "error")

    # ---------- 下载 ----------
    def _on_download(self) -> None:
        if self._latest is None or not self._latest.has_asset:
            info(self, "下载更新", "请先检查更新，并确认该版本附带安装包。")
            return

        target = self._latest
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._set_busy(True, "正在下载…")
        self.notes.appendPlainText(
            f"\n开始下载 {target.asset_name}（{target.size_text}）…\n"
            f"保存到：{self.updater.download_dir()}"
        )

        def work(progress=None):
            return self.updater.download(target, progress=progress)

        run_async(
            work,
            on_result=self._on_downloaded,
            on_error=self._on_download_failed,
            on_progress=self._on_download_progress,
        )

    def _on_download_progress(self, current: int, total: int, text: str) -> None:
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(current * 100 / total))
        else:
            # 拿不到 Content-Length 时用忙碌态而不是假装有进度
            self.progress.setRange(0, 0)
        self.update_rows["state"].set_value(f"正在下载：{text}", COLOR_WARN)

    def _on_downloaded(self, path) -> None:
        self._installer = Path(path)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.update_rows["state"].set_value("下载完成，可以安装", COLOR_OK)
        self._sync_update_rows()
        self.notes.appendPlainText(f"\n下载完成：{path}")
        self._set_busy(False)
        notify(self, "更新包下载完成")
        info(
            self,
            "下载完成",
            f"安装包已保存到：\n{path}\n\n"
            "点「立即重启并更新」会关闭本程序并启动安装程序。\n"
            "也可以稍后手动双击这个文件安装。",
        )

    def _on_download_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self._installer = None
        self.update_rows["state"].set_value("下载失败", COLOR_FAIL)
        self.notes.appendPlainText(f"\n下载失败：{message}")
        self._set_busy(False)
        error(
            self,
            "下载失败",
            f"{message}\n\n可以重试，或点「打开发布页」手动下载安装包。",
        )

    # ---------- 应用更新 ----------
    def _on_apply(self) -> None:
        if self._installer is None or not self._installer.is_file():
            info(self, "更新", "请先下载更新包。")
            return

        running = bool((self.ctx.stats_snapshot().get("worker") or {}).get("running"))
        message = (
            f"将关闭 {APP_NAME} 并启动安装程序：\n{self._installer}\n\n"
            "安装过程需要管理员权限，用户数据不会被删除。\n"
        )
        if running:
            message += (
                "\n⚠ 执行进程正在运行，安装程序会要求先结束它，"
                "正在执行的登录任务会中断（已保存的断点不丢）。\n"
            )
        message += "\n确认现在更新？"

        if not confirm(self, "立即重启并更新", message, danger=True):
            return

        try:
            self.updater.apply_and_restart(self._installer)
        except Exception as exc:
            error(self, "启动安装程序失败", str(exc))
            return

        notify(self, "安装程序已启动，正在退出…", "warn")
        self._quit_for_update()

    def _quit_for_update(self) -> None:
        """退出当前进程，把文件锁让给安装程序。

        必须先停 IPC 与定时器再退：安装器会检测残留进程，
        IPC 命名管道没释放的话它可能判定程序仍在运行。
        """
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        window = self.window()
        try:
            if hasattr(window, "_state_timer"):
                window._state_timer.stop()
            if hasattr(window, "_log_timer"):
                window._log_timer.stop()
        except Exception:
            pass
        try:
            self.ctx.shutdown()
        except Exception:
            pass

        # 延迟退出：给安装器进程一点启动时间，也让状态栏提示能被看到
        QTimer.singleShot(800, QApplication.quit)

    # ---------- 其他按钮 ----------
    def _on_release_page(self) -> None:
        url = self.updater.open_release_page()
        notify(self, f"已在浏览器打开：{url}")

    def _on_copy_info(self) -> None:
        from PySide6.QtWidgets import QApplication

        lines = [f"{APP_NAME} 程序信息"]
        for row in self.info_rows.values():
            lines.append(f"{row.key_text()}: {row.value_text()}")
        text = "\n".join(lines)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
            notify(self, "程序信息已复制到剪贴板")
        else:
            info(self, "程序信息", text)

    def _on_open_data_dir(self) -> None:
        import os
        import subprocess

        path = self.ctx.cfg.data_root
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606 - 用户自己的数据目录
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            notify(self, "已打开数据目录")
        except OSError:
            info(self, "数据目录", str(path))
