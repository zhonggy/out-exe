"""浏览器页：内核选择 + 环境自检。

"检测环境"复用 CLI ``doctor`` 的检查项，但改成 GUI 列表展示。
检查项里最容易在打包版出问题的是 ``patchright/driver/node.exe``——
PyInstaller 自动分析抓不到它，必须在 build.spec 里显式声明为 datas。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from browser import KERNEL_FINGERPRINT, KERNEL_PATCHRIGHT, describe_kernel

from ..bridge.tasks import run_async
from ..theme import COLOR_FAIL, COLOR_OK, COLOR_WARN, TEXT_DIM, fit_all_checkboxes
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

#: 内核下拉项：(显示文本, 配置值)
_KERNEL_CHOICES = [
    ("指纹内核 fingerprint-chromium（推荐）", KERNEL_FINGERPRINT),
    ("patchright 自带 Chromium（备用）", KERNEL_PATCHRIGHT),
]


class BrowserView(QWidget):
    def __init__(self, context, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = context
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(title_label("浏览器"))

        # ---- 内核 ----
        kernel_box = QGroupBox("内核")
        kernel_layout = QVBoxLayout(kernel_box)

        self.kernel_combo = QComboBox()
        for text, value in _KERNEL_CHOICES:
            self.kernel_combo.addItem(text, value)
        self.btn_apply_kernel = button("应用", "primary")
        self.btn_apply_kernel.clicked.connect(self._on_apply_kernel)
        kernel_layout.addWidget(
            toolbar(QLabel("使用内核"), self.kernel_combo, self.btn_apply_kernel, stretch_at=2)
        )

        self.rows: Dict[str, KeyValueRow] = {
            "active": KeyValueRow("当前生效", elide=True),
            "fingerprint": KeyValueRow("指纹内核", elide=True),
            "patchright": KeyValueRow("备用内核", elide=True),
            "fp_enabled": KeyValueRow("指纹伪装"),
            "headless": KeyValueRow("无头模式"),
            "instances": KeyValueRow("浏览器实例"),
        }
        for row in self.rows.values():
            kernel_layout.addWidget(row)

        kernel_layout.addWidget(
            hint_label(
                "指纹伪装（<code>--fingerprint</code> 系列参数）只有 fingerprint-chromium "
                "内核识别，切到备用内核后该功能自动失效。<br>"
                "切换内核需要重启执行进程才生效。"
            )
        )
        layout.addWidget(kernel_box)

        # ---- 自检 ----
        check_box = QGroupBox("环境自检")
        check_layout = QVBoxLayout(check_box)

        self.btn_check = button("检测环境", "primary")
        self.btn_close_all = button("关闭全部浏览器", "danger")
        self.btn_check.clicked.connect(self._on_check)
        self.btn_close_all.clicked.connect(self._on_close_all)
        check_layout.addWidget(toolbar(self.btn_check, self.btn_close_all, stretch_at=1))

        self.check_output = QPlainTextEdit()
        self.check_output.setReadOnly(True)
        self.check_output.setMinimumHeight(180)
        self.check_output.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace;"
        )
        self.check_output.setPlainText("点击「检测环境」开始检查。")
        check_layout.addWidget(self.check_output, 1)
        layout.addWidget(check_box, 1)

        fit_all_checkboxes(self)

    # ---------- 刷新 ----------
    def refresh(self) -> None:
        self.update_state(self.ctx.stats_snapshot())

    def update_state(self, snapshot: Dict[str, Any]) -> None:
        kernel = snapshot.get("kernel") or describe_kernel(self.ctx.cfg)

        configured = str(kernel.get("configured") or "").lower()
        target = KERNEL_PATCHRIGHT if configured == KERNEL_PATCHRIGHT else KERNEL_FINGERPRINT
        index = self.kernel_combo.findData(target)
        if index >= 0 and self.kernel_combo.currentIndex() != index:
            self.kernel_combo.blockSignals(True)
            self.kernel_combo.setCurrentIndex(index)
            self.kernel_combo.blockSignals(False)

        if kernel.get("error"):
            self.rows["active"].set_value(str(kernel["error"]), COLOR_FAIL)
        else:
            label = {
                KERNEL_FINGERPRINT: "指纹内核",
                KERNEL_PATCHRIGHT: "patchright 自带内核",
                "custom": "自定义路径",
            }.get(str(kernel.get("active_kernel")), "-")
            path = kernel.get("active_path") or "（由 Playwright 默认查找）"
            self.rows["active"].set_value(f"{label}  ·  {path}", COLOR_OK)

        self.rows["fingerprint"].set_value(
            str(kernel.get("fingerprint_path") or "未找到"),
            COLOR_OK if kernel.get("fingerprint_available") else COLOR_WARN,
        )
        self.rows["patchright"].set_value(
            str(kernel.get("patchright_path") or "未随包携带（走系统安装位置）"),
            COLOR_OK if kernel.get("patchright_bundled") else TEXT_DIM,
        )

        fp_on = bool(self.ctx.cfg.get("browser.fingerprint_enabled", False))
        supported = str(kernel.get("active_kernel")) == KERNEL_FINGERPRINT
        if fp_on and not supported:
            self.rows["fp_enabled"].set_value("已开启，但当前内核不支持", COLOR_WARN)
        else:
            self.rows["fp_enabled"].set_value("开启" if fp_on else "关闭")

        self.rows["headless"].set_value(
            "是" if self.ctx.cfg.get("browser.headless", False) else "否"
        )
        max_workers = int(self.ctx.cfg.get("system.max_workers", 1) or 1)
        self.rows["instances"].set_value(
            f"{snapshot.get('browsers', 0)} / 上限 {max_workers}（= 并发线程数）"
        )

    # ---------- 操作 ----------
    def _on_apply_kernel(self) -> None:
        value = str(self.kernel_combo.currentData())
        running = bool((self.ctx.stats_snapshot().get("worker") or {}).get("running"))

        def work():
            self.ctx.cfg.update({"browser": {"executable_path": value}}, save=True)
            return value


        def done(_):
            self.refresh()
            if running:
                info(
                    self,
                    "内核已切换",
                    "配置已保存。执行进程正在运行，需重启后生效"
                    "（任务管理页 → 重启执行进程）。",
                )
                notify(self, f"内核已切换为 {value}（需重启执行进程）", "warn")
            else:
                info(self, "内核已切换", "配置已保存，下次启动生效。")
                notify(self, f"内核已切换为 {value}")

        run_async(work, on_result=done, on_error=lambda msg: error(self, "保存失败", msg))

    def _on_check(self) -> None:
        self.btn_check.setEnabled(False)
        self.check_output.setPlainText("检测中…")
        run_async(
            self._collect_checks,
            on_result=self._render_checks,
            on_error=lambda msg: error(self, "检测失败", msg),
            on_done=lambda: self.btn_check.setEnabled(True),
        )

    def _collect_checks(self) -> List[Tuple[str, str, str]]:
        """返回 [(级别, 项目, 详情)]。级别: OK / WARN / FAIL。"""
        results: List[Tuple[str, str, str]] = []
        cfg = self.ctx.cfg

        results.append(("OK", "Python", sys.version.split()[0]))
        results.append(("OK", "程序目录", str(cfg.root)))
        results.append(("OK", "数据目录", str(cfg.data_root)))
        results.append(
            ("OK", "配置文件", str(cfg.source_path or "（使用内置默认值）"))
        )

        for module in ("patchright", "yaml", "PySide6"):
            try:
                __import__(module)
                results.append(("OK", f"依赖 {module}", "已安装"))
            except ImportError as exc:
                results.append(("FAIL", f"依赖 {module}", str(exc)))

        try:
            import apscheduler  # noqa: F401

            results.append(("OK", "依赖 apscheduler", "已安装"))
        except ImportError:
            results.append(("WARN", "依赖 apscheduler", "未安装，定时任务不可用"))

        # Playwright driver：打包版最常见的缺失项
        try:
            import patchright

            driver = Path(patchright.__file__).parent / "driver"
            node = driver / ("node.exe" if sys.platform == "win32" else "node")
            if node.is_file():
                results.append(("OK", "Playwright driver", str(node)))
            else:
                results.append(
                    (
                        "FAIL",
                        "Playwright driver",
                        f"缺少 {node.name}，浏览器无法启动"
                        "（打包时需把 patchright/driver 整目录包含进去）",
                    )
                )
        except Exception as exc:
            results.append(("FAIL", "Playwright driver", str(exc)))

        # 浏览器内核
        kernel = describe_kernel(cfg)
        if kernel.get("error"):
            results.append(("FAIL", "浏览器内核", str(kernel["error"])))
        else:
            results.append(
                (
                    "OK",
                    "浏览器内核",
                    kernel.get("active_path") or "patchright 默认查找",
                )
            )
        if not kernel.get("fingerprint_available"):
            results.append(
                ("WARN", "指纹内核", "未找到，指纹伪装不可用（可切换到备用内核）")
            )

        # 目录可写性：Program Files 下最容易踩
        for label, path in [
            ("数据库目录", cfg.path_of("database.path", "data/app.db").parent),
            ("日志目录", cfg.path_of("logger.dir", "logs")),
            ("Profile 目录", cfg.path_of("profile.root", "profiles")),
        ]:
            results.append(self._check_writable(label, path))

        accounts_file = cfg.path_of("system.accounts_file", "accounts.txt")
        results.append(
            (
                "OK" if accounts_file.is_file() else "WARN",
                "账号文件",
                str(accounts_file) if accounts_file.is_file() else f"{accounts_file}（不存在）",
            )
        )

        # 云盘同步会锁住 SQLite WAL
        data_root = str(cfg.data_root).lower()
        for marker in ("onedrive", "dropbox", "google drive", "坚果云"):
            if marker in data_root:
                results.append(
                    (
                        "WARN",
                        "云盘同步",
                        f"数据目录位于 {marker} 同步范围内，"
                        "SQLite WAL 可能被锁导致 database is locked",
                    )
                )
                break

        return results

    @staticmethod
    def _check_writable(label: str, path) -> Tuple[str, str, str]:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".oa_write_probe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return ("FAIL", label, f"{path} 不可写：{exc}")
        return ("OK", label, str(path))

    def _render_checks(self, results: List[Tuple[str, str, str]]) -> None:
        lines: List[str] = []
        fails = warns = 0
        for level, item, detail in results:
            if level == "FAIL":
                fails += 1
            elif level == "WARN":
                warns += 1
            lines.append(f"[{level:<4}] {item:<20} {detail}")
        lines.append("")
        if fails:
            summary = f"环境检测：{fails} 项失败，{warns} 项警告"
            lines.append(f"结论：{summary} —— 需先修复失败项")
            level = "error"
        elif warns:
            summary = f"环境检测：{warns} 项警告，可以运行"
            lines.append(f"结论：{summary}")
            level = "warn"
        else:
            summary = "环境检测：全部通过"
            lines.append("结论：全部通过")
            level = "ok"
        self.check_output.setPlainText("\n".join(lines))
        notify(self, summary, level)

    def _on_close_all(self) -> None:
        if not confirm(
            self,
            "关闭全部浏览器",
            "浏览器由执行进程持有，本操作会结束执行进程。\n\n"
            "正在执行的任务会保留断点。继续？",
            danger=True,
        ):
            return
        def done(result):
            self.refresh()
            stopped = (result or {}).get("stopped") or []
            if stopped:
                notify(self, f"已结束执行进程（PID {', '.join(map(str, stopped))}）")
            else:
                notify(self, "没有正在运行的执行进程", "warn")

        run_async(
            self.ctx.wpm.stop,
            on_result=done,
            on_error=lambda msg: error(self, "操作失败", msg),
        )
