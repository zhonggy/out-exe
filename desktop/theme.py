"""界面主题：深色配色 + 状态色 + 字体度量。

**行高不写死。** 中文字体的 ``QFontMetrics.height()`` 通常是像素字号的
1.3~1.4 倍（西文约 1.15 倍），再叠加 Windows 的 DPI 缩放（125%/150%/175%），
写死的 28px 行高在高缩放下会把文字裁掉。所以行高由 ``row_height()``
按运行时字体实测值算出。

同理，仪表盘大数字用 26px 字号时需要约 35px 高度，写死 26 必然截断。
凡是"文字高度决定控件高度"的地方都走 ``text_height()``。
"""

from __future__ import annotations

from typing import Dict, Optional

# ---------------------------------------------------------------- 字号
#: 正文像素字号。中文在 13px 下笔画粘连，14px 是清晰度与信息密度的平衡点。
FONT_SIZE = 14
FONT_SIZE_SMALL = 13
FONT_SIZE_TITLE = 19
FONT_SIZE_METRIC = 28

#: 中文字体行高系数。用于在拿不到 QFontMetrics 时的保守估算。
_CJK_LINE_RATIO = 1.45

# ---------------------------------------------------------------- 颜色
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


# ---------------------------------------------------------------- 度量
#: 度量用的探针串：混中文、西文、数字与下伸部（g/p/y），覆盖最大字形范围
_PROBE = "等待验证Ag账号 account@example.com 128.5MB gjpqy"


def text_height(widget=None, font_size: Optional[int] = None) -> int:
    """一行文字实际占用的像素高度。

    优先用 widget 自身字体（样式表的 ``font-size`` 会反映到 ``widget.font()``，
    已实测确认），拿不到就按系数保守估算。
    """
    try:
        from PySide6.QtGui import QFont, QFontMetrics

        if widget is not None and font_size is None:
            metrics = QFontMetrics(widget.font())
        else:
            font = QFont()
            if widget is not None:
                font = QFont(widget.font())
            font.setPixelSize(int(font_size or FONT_SIZE))
            metrics = QFontMetrics(font)
        # boundingRect 比 height() 更保守：某些字体的 height() 不含下伸部余量
        return max(metrics.height(), metrics.boundingRect(_PROBE).height())
    except Exception:
        return int((font_size or FONT_SIZE) * _CJK_LINE_RATIO) + 1


def row_height(widget=None, padding: int = 12, minimum: int = 30) -> int:
    """表格行高 = 文字高度 + 上下留白。

    ``padding`` 默认 12（上下各 6），保证中文不贴边；``minimum`` 兜住
    字体度量异常的平台（如 offscreen 下 height() 退化为等于字号）。
    """
    return max(minimum, text_height(widget) + padding)


def metric_height(widget=None) -> int:
    """仪表盘大数字所需高度。26px 字号写死 26 必然截断。"""
    return text_height(widget, FONT_SIZE_METRIC) + 6


def apply_row_height(table, padding: int = 12, minimum: int = 30) -> None:
    """给 QTableView 设行高与表头高。

    表头也要一起调，否则表头文字在高 DPI 下同样会被裁。
    """
    height = row_height(table, padding=padding, minimum=minimum)
    header = table.verticalHeader()
    header.setDefaultSectionSize(height)
    header.setMinimumSectionSize(height)
    table.horizontalHeader().setFixedHeight(height + 4)


STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: {FONT_SIZE}px;
}}
QLabel {{
    /* 中文字形比西文高，不给上下留白会贴边甚至被裁 */
    padding: 1px 0;
}}
QLabel[role="title"] {{
    font-size: {FONT_SIZE_TITLE}px;
    font-weight: 600;
    padding: 2px 0 4px 0;
}}
QLabel[role="hint"] {{
    color: {TEXT_DIM};
    font-size: {FONT_SIZE_SMALL}px;
    padding: 2px 0;
}}
QLabel[role="metric"] {{
    font-size: {FONT_SIZE_METRIC}px;
    font-weight: 600;
    padding: 2px 0;
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
    padding: 7px 16px;
    min-height: 18px;
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
    padding: 6px 9px;
    min-height: 18px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    width: 20px;
    border: none;
}}
QComboBox QAbstractItemView {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    /* 下拉项同样需要足够行高，否则中文被裁 */
    padding: 4px;
}}
QComboBox QAbstractItemView::item {{
    min-height: 24px;
    padding: 4px 6px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 18px;
}}
QCheckBox {{
    spacing: 7px;
    padding: 2px 0;
    min-height: 20px;
}}

QListWidget[role="nav"] {{
    background: {BG_ALT};
    border: none;
    border-right: 1px solid {BORDER};
    outline: none;
    padding-top: 8px;
}}
QListWidget[role="nav"]::item {{
    padding: 11px 18px;
    border-left: 3px solid transparent;
    min-height: 22px;
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
    padding: 7px 9px;
    font-weight: 600;
}}
QTableView::item {{
    padding: 6px 8px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 11px;
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
    height: 11px;
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
    min-height: 26px;
}}
QStatusBar::item {{
    border: none;
}}
QStatusBar QLabel {{
    padding: 2px 6px;
}}

QProgressBar {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    min-height: 20px;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 5px;
}}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 16px;
    padding: 14px 12px 12px 12px;
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
    padding: 5px 8px;
}}

QSplitter::handle {{
    background: {BORDER};
}}

QDialog {{
    background: {BG};
}}
QDialogButtonBox QPushButton {{
    min-width: 76px;
}}
"""


def apply_theme(app) -> None:
    """应用 Fusion 风格 + 深色样式表。"""
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
