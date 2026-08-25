"""把配色审计固化成测试。

浅色主题的对比度比深色更容易踩坑：深色下"提亮"总能拉开对比，
浅色下压暗过头会发脏、不够又发飘。而且表格有三种底色（白/斑马/选中），
只在白底上验是不够的 —— 实测选中行底色会让状态色掉到 4.0:1。

本测试不依赖渲染，纯算色值，跑得很快。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: WCAG AA：正文 4.5:1，大字与图形 3:1
AA_TEXT = 4.5
AA_GRAPHIC = 3.0

#: 状态色两两之间的最小 RGB 距离（低于此值用户难以区分）
MIN_STATUS_DISTANCE = 40.0

#: 状态色与交互主色的最小距离。撞色会让状态文字看起来像可点的链接
MIN_ACCENT_DISTANCE = 45.0


def _rgb(value: str) -> list:
    value = value.lstrip("#")
    return [int(value[i:i + 2], 16) for i in (0, 2, 4)]


def relative_luminance(value: str) -> float:
    """WCAG 相对亮度。"""
    parts = []
    for channel in _rgb(value):
        srgb = channel / 255
        parts.append(srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4)
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def contrast(foreground: str, background: str) -> float:
    a, b = relative_luminance(foreground), relative_luminance(background)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def color_distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** 0.5


def main() -> int:
    import desktop.theme as theme

    failures = []

    # ---------- 1. 文字对比度 ----------
    print("=== 文字对比度（AA ≥ 4.5:1）===")
    text_checks = [
        ("正文 / 窗口底", theme.TEXT, theme.BG),
        ("正文 / 卡片", theme.TEXT, theme.BG_ALT),
        ("次要文字 / 卡片", theme.TEXT_DIM, theme.BG_ALT),
        ("次要文字 / 窗口底", theme.TEXT_DIM, theme.BG),
        ("次要文字 / 表头", theme.TEXT_DIM, theme.HEADER_BG),
        ("主色按钮文字", theme.TEXT_ON_ACCENT, theme.ACCENT),
        ("导航选中文字", theme.ACCENT, theme.ACCENT_SOFT),
        ("危险按钮文字", theme.COLOR_FAIL, theme.DANGER_BG),
        ("Tooltip 文字", theme.BG_ALT, theme.TEXT),
    ]
    for name, fg, bg in text_checks:
        value = contrast(fg, bg)
        if value < AA_TEXT:
            failures.append(f"{name}: {value:.2f}:1 < {AA_TEXT}")
            print(f"[FAIL] {name:<20} {value:5.2f}:1  {fg} on {bg}")
        else:
            print(f"[OK]   {name:<20} {value:5.2f}:1")

    # ---------- 2. 状态色在表格三种底色上 ----------
    # 只验白底是不够的：选中行底色最暗，实测会让状态色掉到 4.0:1
    print()
    print("=== 状态色 × 表格底色（白行 / 斑马行 / 选中行）===")
    statuses = {
        "成功": theme.COLOR_OK,
        "失败": theme.COLOR_FAIL,
        "警告": theme.COLOR_WARN,
        "运行": theme.COLOR_RUNNING,
        "空闲": theme.COLOR_IDLE,
    }
    backgrounds = {
        "白行": theme.BG_ALT,
        "斑马行": theme.ROW_ALT,
        "选中行": theme.ACCENT_SOFT,
    }
    for name, color in statuses.items():
        worst = min(contrast(color, bg) for bg in backgrounds.values())
        detail = "  ".join(
            f"{bn}={contrast(color, bg):.2f}" for bn, bg in backgrounds.items()
        )
        if worst < AA_TEXT:
            failures.append(f"状态色 {name} 最差 {worst:.2f}:1")
            print(f"[FAIL] {name:<6} {detail}")
        else:
            print(f"[OK]   {name:<6} {detail}")

    # ---------- 3. 状态色可区分 ----------
    print()
    print(f"=== 状态色两两可区分（RGB 距离 > {MIN_STATUS_DISTANCE:.0f}）===")
    names = list(statuses)
    close_pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            distance = color_distance(statuses[a], statuses[b])
            if distance <= MIN_STATUS_DISTANCE:
                close_pairs.append(f"{a}/{b} 距离 {distance:.0f}")
    if close_pairs:
        for item in close_pairs:
            failures.append(f"状态色难区分: {item}")
            print(f"[FAIL] {item}")
    else:
        print("[OK]   五种状态色两两可区分")

    # ---------- 4. 状态色不与交互主色撞 ----------
    print()
    print(f"=== 状态色 vs 交互主色（距离 > {MIN_ACCENT_DISTANCE:.0f}）===")
    # 撞色的后果：表格里的"运行中"文字看起来像可点的链接
    for name, color in statuses.items():
        distance = color_distance(color, theme.ACCENT)
        if distance <= MIN_ACCENT_DISTANCE:
            failures.append(f"{name} 与主色撞色（距离 {distance:.0f}）")
            print(f"[FAIL] {name:<6} 距离 {distance:5.1f}")
        else:
            print(f"[OK]   {name:<6} 距离 {distance:5.1f}")

    # ---------- 5. 主题必须是浅色 ----------
    print()
    print("=== 主题基调 ===")
    for name, value in (("窗口底", theme.BG), ("卡片", theme.BG_ALT)):
        lightness = relative_luminance(value)
        if lightness < 0.7:
            failures.append(f"{name} 亮度 {lightness:.2f} 偏暗，不是浅色主题")
            print(f"[FAIL] {name} {value} 亮度 {lightness:.2f}")
        else:
            print(f"[OK]   {name} {value} 亮度 {lightness:.2f}")

    # 边框要能看见，但不能像文字那样抢眼
    border_contrast = contrast(theme.BORDER, theme.BG_ALT)
    if border_contrast < 1.2:
        failures.append(f"边框对比度 {border_contrast:.2f} 过低，看不见")
        print(f"[FAIL] 边框对比度 {border_contrast:.2f}")
    elif border_contrast > 3.5:
        failures.append(f"边框对比度 {border_contrast:.2f} 过高，抢眼")
        print(f"[FAIL] 边框对比度 {border_contrast:.2f} 过高")
    else:
        print(f"[OK]   边框对比度 {border_contrast:.2f}")

    print()
    if failures:
        print(f"配色审计失败：{len(failures)} 项")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("配色审计通过")
    return 0


# ---------------------------------------------------------------- pytest 用例
#
# main() 是给人看的详细报告；下面是给 pytest 的断言。
# 两者共用上面的计算函数，不重复实现判定逻辑。


def _theme():
    import desktop.theme as theme

    return theme


def test_theme_is_light():
    """主题必须是浅色 —— 防止误把深色值合回来。"""
    theme = _theme()
    assert relative_luminance(theme.BG) >= 0.7, f"窗口底 {theme.BG} 偏暗"
    assert relative_luminance(theme.BG_ALT) >= 0.7, f"卡片 {theme.BG_ALT} 偏暗"


def test_body_text_contrast():
    theme = _theme()
    for name, fg, bg in (
        ("正文/窗口底", theme.TEXT, theme.BG),
        ("正文/卡片", theme.TEXT, theme.BG_ALT),
        ("次要/卡片", theme.TEXT_DIM, theme.BG_ALT),
        ("次要/窗口底", theme.TEXT_DIM, theme.BG),
        ("次要/表头", theme.TEXT_DIM, theme.HEADER_BG),
    ):
        value = contrast(fg, bg)
        assert value >= AA_TEXT, f"{name} 对比度 {value:.2f}:1 低于 {AA_TEXT}"


def test_interactive_text_contrast():
    theme = _theme()
    for name, fg, bg in (
        ("主色按钮", theme.TEXT_ON_ACCENT, theme.ACCENT),
        ("导航选中", theme.ACCENT, theme.ACCENT_SOFT),
        ("危险按钮", theme.COLOR_FAIL, theme.DANGER_BG),
        ("Tooltip", theme.BG_ALT, theme.TEXT),
    ):
        value = contrast(fg, bg)
        assert value >= AA_TEXT, f"{name} 对比度 {value:.2f}:1 低于 {AA_TEXT}"


def test_status_colors_on_all_table_backgrounds():
    """表格有三种底色，只验白底会漏 —— 选中行最暗。"""
    theme = _theme()
    statuses = {
        "成功": theme.COLOR_OK,
        "失败": theme.COLOR_FAIL,
        "警告": theme.COLOR_WARN,
        "运行": theme.COLOR_RUNNING,
        "空闲": theme.COLOR_IDLE,
    }
    backgrounds = (theme.BG_ALT, theme.ROW_ALT, theme.ACCENT_SOFT)
    for name, color in statuses.items():
        worst = min(contrast(color, bg) for bg in backgrounds)
        assert worst >= AA_TEXT, f"状态色 {name} 最差对比度 {worst:.2f}:1"


def test_status_colors_are_distinguishable():
    theme = _theme()
    statuses = {
        "成功": theme.COLOR_OK,
        "失败": theme.COLOR_FAIL,
        "警告": theme.COLOR_WARN,
        "运行": theme.COLOR_RUNNING,
        "空闲": theme.COLOR_IDLE,
    }
    names = list(statuses)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            distance = color_distance(statuses[a], statuses[b])
            assert distance > MIN_STATUS_DISTANCE, (
                f"{a} 与 {b} 太接近（RGB 距离 {distance:.0f}）"
            )


def test_status_colors_do_not_clash_with_accent():
    """状态色撞主色会让状态文字看起来像可点的链接。"""
    theme = _theme()
    for name, color in (
        ("成功", theme.COLOR_OK),
        ("失败", theme.COLOR_FAIL),
        ("警告", theme.COLOR_WARN),
        ("运行", theme.COLOR_RUNNING),
        ("空闲", theme.COLOR_IDLE),
    ):
        distance = color_distance(color, theme.ACCENT)
        assert distance > MIN_ACCENT_DISTANCE, (
            f"{name} 与交互主色撞色（距离 {distance:.0f}）"
        )


def test_border_is_visible_but_not_loud():
    theme = _theme()
    value = contrast(theme.BORDER, theme.BG_ALT)
    assert value >= 1.2, f"边框对比度 {value:.2f} 太低，看不见"
    assert value <= 3.5, f"边框对比度 {value:.2f} 太高，抢眼"


def test_log_level_colors_readable():
    """日志文字直接画在白底上，每个级别都要够清楚。"""
    theme = _theme()
    for level in ("DEBUG", "INFO", "OK", "WARN", "ERROR", "FAIL", "CRITICAL"):
        color = theme.log_level_color(level)
        value = contrast(color, theme.BG_ALT)
        assert value >= AA_TEXT, f"日志级别 {level} 对比度 {value:.2f}:1"


def test_stylesheet_has_no_dark_leftovers():
    """样式表里不应残留深色硬编码值。

    浅色主题里出现一个深色背景就是明显的视觉缺口，而这类值容易在
    改主题时被漏掉（原深色表里散着 #10151c / #1c2128 之类）。
    判定：允许出现的深色只有正文/状态色这些前景色。
    """
    import re

    theme = _theme()
    allowed = {
        theme.TEXT.lower(),
        theme.TEXT_DIM.lower(),
        theme.COLOR_OK.lower(),
        theme.COLOR_FAIL.lower(),
        theme.COLOR_WARN.lower(),
        theme.COLOR_RUNNING.lower(),
        theme.COLOR_IDLE.lower(),
        theme.ACCENT.lower(),
        theme.ACCENT_HOVER.lower(),
        theme.ACCENT_PRESSED.lower(),
    }
    leftovers = []
    for match in re.finditer(r"#[0-9a-fA-F]{6}", theme.STYLESHEET):
        value = match.group(0).lower()
        if value in allowed:
            continue
        if relative_luminance(value) < 0.35:
            leftovers.append(value)
    assert not leftovers, f"样式表残留深色值: {sorted(set(leftovers))}"


if __name__ == "__main__":
    sys.exit(main())
