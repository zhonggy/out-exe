"""界面主题：深色配色 + 状态色。

只用 Qt 内置能力（Fusion + 样式表），不引入第三方主题包——安装包已经很大了。
状态颜色集中在这里，各页面统一取用，避免同一状态在不同页面显示成不同颜色。
"""

from __future__ import annotations

from typing import Dict

# 状态色。语义：绿=成功，红=失败，橙=需人工介入，蓝=进行中，灰=未开始
COLOR_OK = "#3fb950"
COLOR_FAIL = "#f85149"
COLOR_WARN = "#d29922"
COLOR_RUNNING = "#58a6ff"
COLOR_IDLE = "#8b949e"

BG = "#0d1117"
BG_ALT = "#161b22"
BG_HOVER = "#1f2733"
BORDER = "#30363d"
TEXT = "#e6edf3"
TEXT_DIM = "#8b949e"
ACCENT = "#1f6feb"

#: 账号状态 → 颜色。键与 database.models.AccountStatus 一致
ACCOUNT_STATUS_COLORS: Dict[str, str] = {
    "NEW": COLOR_IDLE,
    "PENDING": COLOR_IDLE,
    "RUNNING": COLOR_RUNNING,
    "OK": COLOR_OK,
    "WAIT_VERIFY": COLOR_WARN,
    "PASSWORD_WRONG": COLOR_FAIL,
    "LOCKED": COLOR_FAIL,
    "NOT_FOUND": COLOR_FAIL,
    "FAILED": COLOR_FAIL,
    "SKIPPED": COLOR_IDLE,
}

#: 任务状态 → 颜色
TASK_STATUS_COLORS: Dict[str, str] = {
    "CREATED": COLOR_IDLE,
    "QUEUED": COLOR_IDLE,
    "RUNNING": COLOR_RUNNING,
    "PAUSED": COLOR_WARN,
    "COMPLETED": COLOR_OK,
    "FAILED": COLOR_FAIL,
    "CANCELLED": COLOR_IDLE,
}

#: 日志级别 → 颜色
LOG_LEVEL_COLORS: Dict[str, str] = {
    "DEBUG": TEXT_DIM,
    "INFO": TEXT,
    "OK": COLOR_OK,
    "WARN": COLOR_WARN,
    "WARNING": COLOR_WARN,
    "ERROR": COLOR_FAIL,
    "FAIL": COLOR_FAIL,
    "CRITICAL": COLOR_FAIL,
}


def account_status_color(status: str) -> str:
    return ACCOUNT_STATUS_COLORS.get((status or "").upper(), TEXT)


def task_status_color(status: str) -> str:
    return TASK_STATUS_COLORS.get((status or "").upper(), TEXT)


def log_level_color(level: str) -> str:
    return LOG_LEVEL_COLORS.get((level or "").upper(), TEXT)


STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QLabel[role="title"] {{
    font-size: 18px;
    font-weight: 600;
}}
QLabel[role="hint"] {{
    color: {TEXT_DIM};
}}
QLabel[role="metric"] {{
    font-size: 26px;
    font-weight: 600;
}}
QFrame[role="card"] {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame[role="separator"] {{
    background: {BORDER};
    max-height: 1px;
}}

QPushButton {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background: {BG_HOVER};
    border-color: #484f58;
}}
QPushButton:pressed {{
    background: #10151c;
}}
QPushButton:disabled {{
    color: #484f58;
    background: #10151c;
}}
QPushButton[variant="primary"] {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background: #388bfd;
}}
QPushButton[variant="danger"] {{
    background: #21262d;
    border-color: #6e2c2c;
    color: {COLOR_FAIL};
}}
QPushButton[variant="danger"]:hover {{
    background: #3d1d1d;
}}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}
QComboBox QAbstractItemView {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
}}
QCheckBox {{
    spacing: 6px;
}}

QListWidget[role="nav"] {{
    background: {BG_ALT};
    border: none;
    border-right: 1px solid {BORDER};
    outline: none;
    padding-top: 8px;
}}
QListWidget[role="nav"]::item {{
    padding: 10px 18px;
    border-left: 3px solid transparent;
}}
QListWidget[role="nav"]::item:selected {{
    background: {BG_HOVER};
    border-left-color: {ACCENT};
    color: #ffffff;
}}
QListWidget[role="nav"]::item:hover {{
    background: {BG_HOVER};
}}

QTableView {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER};
    selection-background-color: #1c3d5a;
    selection-color: {TEXT};
    alternate-background-color: #12171f;
}}
QHeaderView::section {{
    background: #1c2128;
    color: {TEXT_DIM};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: 600;
}}
QTableView::item {{
    padding: 4px 6px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #30363d;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #484f58;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: #30363d;
    border-radius: 5px;
    min-width: 30px;
}}

QStatusBar {{
    background: {BG_ALT};
    border-top: 1px solid {BORDER};
    color: {TEXT_DIM};
}}
QStatusBar::item {{
    border: none;
}}

QProgressBar {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    height: 18px;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 5px;
}}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {TEXT_DIM};
    font-weight: 600;
}}

QToolTip {{
    background: #1c2128;
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
}}

QSplitter::handle {{
    background: {BORDER};
}}
"""


def apply_theme(app) -> None:
    """应用 Fusion 风格 + 深色样式表。"""
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
