"""界面主题：浅色配色 + 状态色 + 字体度量。

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
#
# 浅色底下的取值比深色麻烦：深色主题用的亮色（如 #3fb950 绿）在白底上
# 对比度只有 2.3:1，远低于 WCAG AA 要求的 4.5:1，看着发飘且不易读。
# 这里全部换成压暗后的同色系，实测对比度均 ≥ 4.5:1。
COLOR_OK = "#166f2f"        # 绿。压暗到在选中行底色上也 ≥4.5:1
COLOR_FAIL = "#cf222e"      # 红，对比度 5.0:1
COLOR_WARN = "#8a5c00"      # 琥珀。纯橙在白底上远不达标，只能走深琥珀
COLOR_RUNNING = "#0a6e8a"   # 青蓝，对比度 5.0:1
                            # 刻意偏青：与交互蓝 ACCENT(#0067c0) 拉开距离，
                            # 否则"运行中"状态文字和主色按钮同色，
                            # 用户分不清哪个是可点的
COLOR_IDLE = "#5f6b78"      # 灰。与 TEXT_DIM 刻意接近：都表示"次要"

#: 背景三层：窗口底 < 卡片 < 悬停。浅色下层次靠"压暗"而非"提亮"
BG = "#f5f6f8"              # 窗口底：轻微灰，避免纯白刺眼
BG_ALT = "#ffffff"          # 卡片/输入框：纯白，浮在灰底上形成层次
BG_HOVER = "#eef1f5"        # 悬停：比窗口底再暗一点
BORDER = "#d4d9e0"          # 常规边框
BORDER_STRONG = "#b6bec9"   # 强调边框（悬停、聚焦邻域）

TEXT = "#1f2328"            # 正文：近黑而非纯黑，减少眩光
TEXT_DIM = "#5c6773"        # 次要文字，对比度 5.6:1
TEXT_ON_ACCENT = "#ffffff"  # 主色按钮上的文字

ACCENT = "#0067c0"          # Windows 11 强调蓝
ACCENT_HOVER = "#1a7ad4"
ACCENT_PRESSED = "#005499"
ACCENT_SOFT = "#eef5fd"     # 主色的浅底（选中行、导航高亮）。
                            # 比 #e8f1fb 更浅：作为大面积背景既不抢眼，
                            # 也给状态色文字留出对比度空间

#: 危险操作按钮的浅色底
DANGER_BG = "#fdf0ef"
DANGER_BORDER = "#e5b3ae"
DANGER_HOVER = "#fbe0de"

#: 表格斑马纹与表头
ROW_ALT = "#fafbfc"
HEADER_BG = "#f0f2f5"

#: 滚动条
SCROLL_HANDLE = "#c3cad3"
SCROLL_HANDLE_HOVER = "#a3adba"

#: 禁用态
DISABLED_TEXT = "#a0a8b3"
DISABLED_BG = "#f0f1f3"

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

    按样式表字号量（见 ``_styled_metrics``），避免构造期偏小。
    """
    try:
        metrics = _styled_metrics(widget, font_size)
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


#: 样式表里 QSpinBox 的左右 padding（9px × 2）与上下按钮宽度
_SPIN_PADDING = 18
_SPIN_BUTTON = 20


def _styled_metrics(widget=None, font_size: Optional[int] = None):
    """按**样式表字号**而非构造期字号取字体度量。

    控件在还没 parent、还没被样式表 polish 时，``widget.font()`` 返回的是
    应用默认字体（9pt），比样式表的 14px 窄。构造期用它算宽高会偏小，
    等样式生效后文字就放不下了。所以这里统一取两者的较大值。
    """
    from PySide6.QtGui import QFont, QFontMetrics

    font = QFont(widget.font()) if widget is not None else QFont()
    current = font.pixelSize()
    font.setPixelSize(max(int(font_size or FONT_SIZE), current if current > 0 else 0))
    return QFontMetrics(font)


def text_width(text: str, widget=None, extra: int = 0, font_size: Optional[int] = None) -> int:
    """一段文字的像素宽度（含额外留白）。"""
    try:
        return _styled_metrics(widget, font_size).horizontalAdvance(str(text)) + extra
    except Exception:
        return len(str(text)) * int((font_size or FONT_SIZE) * 0.62) + extra


#: 样式表给输入类控件的上下 padding（6px × 2）与边框（1px × 2）
_INPUT_VPAD = 14


def input_min_height(widget=None) -> int:
    """输入类控件（QLineEdit / QComboBox / QSpinBox）的最小高度。

    样式表里写的 ``min-height: 18px`` 是错的：真实 Windows 上中文字体的
    ``QFontMetrics.height()`` 约为像素字号的 1.33 倍（14px → 19px），
    加上 padding+border 的 14px 就需要 33px，而 min-height 路径只给
    18+14=32px —— 差 1px，字底部被切掉一条。125% 缩放（18px 字号）差 6px。

    offscreen 平台的 descent 恒为 0（height() == pixelSize），所以这个
    差异在自动化测试里看不出来，只有真机才暴露。因此这里不依赖度量的
    descent，而是对字号取 1.33 系数兜底。
    """
    measured = text_height(widget)
    try:
        px = widget.font().pixelSize() if widget is not None else FONT_SIZE
    except Exception:
        px = FONT_SIZE
    if px <= 0:
        px = FONT_SIZE
    # 取实测与按系数推算的较大值：兼容 offscreen 与真机
    return max(measured, int(px * _CJK_LINE_RATIO)) + _INPUT_VPAD


def fit_input_height(widget) -> None:
    """给输入类控件设最小高度，避免中文被裁掉底部一条。"""
    try:
        widget.setMinimumHeight(input_min_height(widget))
    except Exception:
        pass


def fit_input(
    widget,
    sample: str,
    extra: int = 24,
    cap: Optional[int] = None,
    grow: bool = False,
) -> None:
    """按内容样本给输入类控件设宽度上下限与最小高度。

    原来这些控件写的是 ``setMaximumWidth(260)`` 之类的固定值。固定像素在
    字体变化时不跟着变 —— 中文字体 fallback 或用户单独调大系统字号后，
    260px 装不下原本刚好装下的占位符，表现为「字显示不全」。

    ``grow=True``：该控件应吃满可用宽度（如 URL 这种内容远长于占位符的），
    不设宽度上限，并配合 ``flow_toolbar(expanding=...)`` 拿到整行剩余空间。
    """
    width = text_width(sample, widget, extra)
    if cap is not None:
        width = min(width, cap)
    widget.setMinimumWidth(width)
    if grow:
        # 不设上限：真实内容（如带长 token 的 URL）常比占位符长得多
        widget.setMaximumWidth(16777215)
    else:
        # 上限留 1.6 倍余量：既不让输入框吃掉整行，也不至于卡死内容
        widget.setMaximumWidth(int(width * 1.6))
    fit_input_height(widget)
    # 记下样本，show 之后 refit_widget_tree() 会按真实字体重算
    widget.setProperty("oa_fit_sample", sample)
    widget.setProperty("oa_fit_extra", extra)
    widget.setProperty("oa_fit_grow", grow)


def fit_checkbox(widget, extra: int = 30) -> None:
    """勾选框宽度 = 文字宽 + 指示器与间距。

    Qt 的 sizeHint 用默认间距算，样式表把 spacing 改成 7px 后会差几个像素，
    中文最后一个字被裁掉一小条 —— 不明显但看着别扭。
    """
    widget.setMinimumWidth(text_width(widget.text(), widget, extra))


def fit_all_checkboxes(root, extra: int = 30) -> None:
    """给页面内所有勾选框按文字宽度设最小宽度。

    逐个调 fit_checkbox 太啰嗦（设置页有 10 个），在 _build() 末尾
    统一处理一次即可。
    """
    try:
        from PySide6.QtWidgets import QCheckBox

        for box in root.findChildren(QCheckBox):
            fit_checkbox(box, extra)
    except Exception:
        pass


def fit_label_height(widget, font_size: Optional[int] = None, extra: int = 4) -> None:
    """按样式表字号给 Label 设最小高度。"""
    try:
        widget.setMinimumHeight(_styled_metrics(widget, font_size).height() + extra)
    except Exception:
        pass


def fit_spinbox(spin, extra: int = 10) -> None:
    """按最大值 + 后缀的文字宽度给 QSpinBox 设最小宽度。

    两个坑：

    1. Qt 算 sizeHint 时不知道样式表注入的 padding，也不知道我们把上下
       按钮设成了 20px，算出来的内部编辑器宽度刚好等于文字宽度 —— 零余量，
       光标进去就把数字挤掉一截。
    2. **构造期的字体不是最终字体。** 控件还没 parent、还没被样式表 polish
       时，``spin.font()`` 是应用默认字体（9pt），比样式表的 14px 窄。
       用它量出来的宽度偏小，约束等于没设。所以这里显式按 FONT_SIZE 量。
    """
    try:
        widest = f"{spin.prefix()}{spin.maximum()}{spin.suffix()}"
        width = _styled_metrics(spin).horizontalAdvance(widest)
        spin.setMinimumWidth(width + _SPIN_PADDING + _SPIN_BUTTON + extra)
    except Exception:
        pass


def refit_widget_tree(root) -> None:
    """按**当前真实字体**重算整棵控件树的尺寸约束。

    为什么需要这一步：构造期只能按样式表标称字号（FONT_SIZE）估算，
    而 Qt 会按系统 DPI 把样式表里的 px 缩放（125% → 18px，150% → 21px）。
    缩放后的字比估算时宽，构造期设的最小宽度就不够了。

    应在窗口 show 之后调用 —— 那时样式表已 polish，字体是最终值。
    幂等，可以重复调（比如系统 DPI 变化时）。
    """
    try:
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QLabel,
            QLineEdit,
            QSpinBox,
            QTableView,
        )
    except Exception:
        return

    for spin in root.findChildren(QSpinBox):
        fit_spinbox(spin)
        fit_input_height(spin)

    for combo in root.findChildren(QComboBox):
        fit_input_height(combo)

    for box in root.findChildren(QCheckBox):
        fit_checkbox(box)

    for edit in root.findChildren(QLineEdit):
        sample = edit.property("oa_fit_sample")
        if sample:
            fit_input(
                edit,
                str(sample),
                int(edit.property("oa_fit_extra") or 24),
                grow=bool(edit.property("oa_fit_grow")),
            )
        else:
            # 没登记样本的（含 QSpinBox 内部编辑器）也要补高度
            fit_input_height(edit)

    for label in root.findChildren(QLabel):
        # 只补高度：宽度由布局与省略策略处理
        text = label.text()
        if not text:
            continue
        role = label.property("role")
        size = {
            "title": FONT_SIZE_TITLE,
            "metric": FONT_SIZE_METRIC,
            "hint": FONT_SIZE_SMALL,
        }.get(str(role) if role else "", None)
        needed = text_height(label, size)
        if label.minimumHeight() < needed:
            label.setMinimumHeight(needed + (6 if role == "metric" else 2))

    for table in root.findChildren(QTableView):
        apply_row_height(table)


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
    background: transparent;
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
    min-height: {int(FONT_SIZE * _CJK_LINE_RATIO)}px;
}}
QPushButton:hover {{
    background: {BG_HOVER};
    border-color: {BORDER_STRONG};
}}
QPushButton:pressed {{
    background: {BORDER};
}}
QPushButton:disabled {{
    color: {DISABLED_TEXT};
    background: {DISABLED_BG};
    border-color: {BORDER};
}}
QPushButton[variant="primary"] {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: {TEXT_ON_ACCENT};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton[variant="primary"]:pressed {{
    background: {ACCENT_PRESSED};
    border-color: {ACCENT_PRESSED};
}}
QPushButton[variant="primary"]:disabled {{
    background: {DISABLED_BG};
    border-color: {BORDER};
    color: {DISABLED_TEXT};
}}
QPushButton[variant="danger"] {{
    background: {DANGER_BG};
    border-color: {DANGER_BORDER};
    color: {COLOR_FAIL};
    font-weight: 600;
}}
QPushButton[variant="danger"]:hover {{
    background: {DANGER_HOVER};
    border-color: {COLOR_FAIL};
}}
QPushButton[variant="danger"]:disabled {{
    background: {DISABLED_BG};
    border-color: {BORDER};
    color: {DISABLED_TEXT};
}}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 9px;
    /* 中文字体的 height() 约为字号的 1.33 倍，写 18px 会裁掉底部一条。
       真实高度由 theme.input_min_height() 按运行时字体算，这里只兜首帧。 */
    min-height: {int(FONT_SIZE * _CJK_LINE_RATIO)}px;
    selection-background-color: {ACCENT};
    selection-color: {TEXT_ON_ACCENT};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
    border-color: {BORDER_STRONG};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {DISABLED_BG};
    color: {DISABLED_TEXT};
}}
QComboBox::drop-down {{
    width: 20px;
    border: none;
}}
QComboBox QAbstractItemView {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    /* 下拉项同样需要足够行高，否则中文被裁 */
    padding: 4px;
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    min-height: {int(FONT_SIZE * _CJK_LINE_RATIO) + 8}px;
    padding: 4px 6px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 18px;
    background: transparent;
    border: none;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {BG_HOVER};
}}
QCheckBox {{
    spacing: 7px;
    padding: 2px 0;
    background: transparent;
    min-height: {int(FONT_SIZE * _CJK_LINE_RATIO) + 4}px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 3px;
    background: {BG_ALT};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    /* 内嵌 SVG 对勾：不依赖外部资源文件，打包时无需额外 datas */
    image: url("data:image/svg+xml;utf8,\
<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'>\
<path d='M2.5 6.2l2.3 2.3 4.7-4.9' fill='none' stroke='white' \
stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/></svg>");
}}
QCheckBox:disabled {{
    color: {DISABLED_TEXT};
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
    color: {TEXT_DIM};
    min-height: {int(FONT_SIZE * _CJK_LINE_RATIO) + 4}px;
}}
QListWidget[role="nav"]::item:selected {{
    background: {ACCENT_SOFT};
    border-left-color: {ACCENT};
    color: {ACCENT};
    font-weight: 600;
}}
QListWidget[role="nav"]::item:hover {{
    background: {BG_HOVER};
    color: {TEXT};
}}
QListWidget[role="nav"]::item:selected:hover {{
    background: {ACCENT_SOFT};
    color: {ACCENT};
}}

QTableView {{
    background: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    alternate-background-color: {ROW_ALT};
    outline: none;
}}
QHeaderView::section {{
    background: {HEADER_BG};
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
QTableCornerButton::section {{
    background: {HEADER_BG};
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {SCROLL_HANDLE};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {SCROLL_HANDLE_HOVER};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
}}
QScrollBar::handle:horizontal {{
    background: {SCROLL_HANDLE};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {SCROLL_HANDLE_HOVER};
}}

QStatusBar {{
    background: {BG_ALT};
    border-top: 1px solid {BORDER};
    color: {TEXT_DIM};
    min-height: {int(FONT_SIZE * _CJK_LINE_RATIO) + 10}px;
}}
QStatusBar::item {{
    border: none;
}}
QStatusBar QLabel {{
    padding: 2px 6px;
    background: transparent;
}}

QProgressBar {{
    background: {BG_HOVER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    min-height: {int(FONT_SIZE * _CJK_LINE_RATIO) + 4}px;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 5px;
}}

QGroupBox {{
    background: {BG_ALT};
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
    background: {BG_ALT};
    font-weight: 600;
}}

QToolTip {{
    background: {TEXT};
    color: {BG_ALT};
    border: 1px solid {TEXT};
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
    """应用 Fusion 风格 + 浅色样式表。

    同时设置 palette：Qt 的原生绘制路径（如 QTableView 的空白区、
    QComboBox 的下拉阴影）不完全走样式表，只改样式表会留下深色残块。
    """
    app.setStyle("Fusion")
    _apply_palette(app)
    app.setStyleSheet(STYLESHEET)


def _apply_palette(app) -> None:
    """把浅色值写进 QPalette，兜住样式表覆盖不到的原生绘制。"""
    try:
        from PySide6.QtGui import QColor, QPalette
    except Exception:
        return

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(BG_ALT))
    palette.setColor(QPalette.AlternateBase, QColor(ROW_ALT))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(BG_ALT))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor(TEXT_ON_ACCENT))
    palette.setColor(QPalette.ToolTipBase, QColor(TEXT))
    palette.setColor(QPalette.ToolTipText, QColor(BG_ALT))
    palette.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    palette.setColor(QPalette.Link, QColor(ACCENT))
    for group in (QPalette.Disabled,):
        palette.setColor(group, QPalette.Text, QColor(DISABLED_TEXT))
        palette.setColor(group, QPalette.ButtonText, QColor(DISABLED_TEXT))
        palette.setColor(group, QPalette.WindowText, QColor(DISABLED_TEXT))
    app.setPalette(palette)
