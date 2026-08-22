"""页面操作层：输入、点击、人类化鼠标轨迹。

只封装"怎么做"，不判断"该不该做"（判断在 detector，编排在 login_flow）。
邮箱/密码输入沿用 OutlookRegister 的多策略回退：fill → type → JS 原生 setter，
因为微软登录页的自动填充与 React 受控组件会吞掉普通 fill。
"""

from __future__ import annotations

import math
import random
from typing import List, Optional

from .detector import (
    EMAIL_NEXT_SELECTORS,
    EMAIL_SELECTORS,
    PASSWORD_SELECTORS,
    PRIMARY_BUTTON,
)

PASSWORD_BYPASS_TEXTS = (
    "使用密码",
    "使用密码登录",
    "Use password instead",
    "Use your password",
    "Sign in with a password",
)
DISMISS_TEXTS = ("取消", "以后再说", "暂时跳过", "跳过", "Cancel", "Not now", "Skip", "Maybe later")

_DISABLE_AUTOFILL_JS = """
() => {
    document.querySelectorAll('input').forEach(el => {
        el.setAttribute('autocomplete', 'off');
        el.setAttribute('autocorrect', 'off');
        el.setAttribute('spellcheck', 'false');
    });
}
"""

_NATIVE_SET_JS = """
(el, value) => {
    el.focus();
    el.removeAttribute('readonly');
    el.removeAttribute('aria-hidden');
    el.setAttribute('autocomplete', 'off');
    el.style.opacity = '1';
    el.style.pointerEvents = 'auto';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
}
"""


class PageAction:
    """页面操作封装。"""

    def __init__(self, page, logger=None):
        self.page = page
        self.log = logger

    # ---------- 基础 ----------
    def wait(self, ms: int) -> None:
        try:
            self.page.wait_for_timeout(ms)
        except Exception:
            pass

    def wait_random(self, low: int, high: int) -> None:
        self.wait(random.randint(low, high))

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> bool:
        try:
            self.page.goto(url, wait_until=wait_until)
            self.wait_random(800, 1800)
            return True
        except Exception as exc:
            if self.log:
                self.log.warn("goto", f"打开 {url} 失败: {exc}")
            return False

    def disable_autofill(self) -> None:
        try:
            self.page.evaluate(_DISABLE_AUTOFILL_JS)
        except Exception:
            pass

    def human_type(self, text: str) -> None:
        """模拟人工逐字输入：随机击键间隔 + 随机短暂停顿。

        要求焦点已在目标输入框上。偶尔“想一下”停顿 0.2~0.5 秒。
        """
        for i, ch in enumerate(text):
            try:
                self.page.keyboard.type(ch, delay=random.randint(15, 60))
            except Exception:
                pass
            self.wait(random.randint(25, 110))
            # 约 8% 概率停顿一下（像人在回忆/看屏幕）
            if random.random() < 0.08:
                self.wait(random.randint(180, 520))

    def clear_input(self) -> None:
        """清空当前聚焦的输入框（全选后退格）。"""
        try:
            self.page.keyboard.press("Control+a")
            self.wait(random.randint(80, 200))
            self.page.keyboard.press("Backspace")
            self.wait(random.randint(100, 250))
        except Exception:
            pass

    def click_if_visible(self, selector: str, timeout_ms: int = 2500) -> bool:
        try:
            loc = self.page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            pass
        return False

    def click_text(self, texts, timeout_ms: int = 3000) -> Optional[str]:
        """按角色/文本依次尝试点击，返回命中的文案。"""
        for text in texts:
            try:
                btn = self.page.get_by_role("button", name=text)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=timeout_ms)
                    return text
            except Exception:
                pass
            try:
                loc = self.page.locator(f'input[type="button"][value="{text}"]')
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=timeout_ms)
                    return text
            except Exception:
                pass
            try:
                el = self.page.get_by_text(text, exact=False)
                if el.count() > 0 and el.first.is_visible():
                    el.first.click(timeout=timeout_ms)
                    return text
            except Exception:
                pass
        return None

    def submit_primary(self) -> bool:
        """点主按钮，失败退回回车。"""
        try:
            self.page.get_by_test_id("primaryButton").click(timeout=5000)
            return True
        except Exception:
            pass
        if self.click_if_visible(PRIMARY_BUTTON, timeout_ms=5000):
            return True
        try:
            self.page.keyboard.press("Enter")
            return True
        except Exception:
            return False

    def dismiss_dialogs(self) -> int:
        """关闭"取消/以后再说/跳过"类拦截弹窗，返回点击次数。"""
        clicked = 0
        for _ in range(3):
            hit = self.click_text(DISMISS_TEXTS, timeout_ms=2000)
            if not hit:
                break
            clicked += 1
            self.wait_random(500, 1200)
        return clicked

    # ---------- 邮箱输入 ----------
    def _email_selector(self) -> Optional[str]:
        """当前页面可见的邮箱输入框选择器（新版 usernameEntry / 旧版 i0116）。"""
        for sel in EMAIL_SELECTORS:
            try:
                loc = self.page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return sel
            except Exception:
                continue
        return None

    def fill_email(self, email: str) -> bool:
        """多策略输入邮箱并提交。优先人工逐字输入，失败再退回机械方式。"""
        strategies = (
            ("human", self._email_human),
            ("type", self._email_type),
            ("js", self._email_js),
            ("js_retry", self._email_js),
        )
        for name, fn in strategies:
            try:
                if self._email_gone():
                    return True
                fn(email)
                self.wait_random(800, 1600)
                if self._email_gone() or self._value_of(self._email_selector()) == email:
                    if self.log:
                        self.log.info("username_input", f"{name} 提交邮箱成功")
                    return True
            except Exception as exc:
                if self.log:
                    self.log.warn("username_input", f"{name} 失败: {exc}")
                if self._email_gone():
                    return True
        return self._email_gone()

    def _email_gone(self) -> bool:
        return self._email_selector() is None

    def _value_of(self, selector: str) -> str:
        try:
            return self.page.eval_on_selector(selector, "(el) => (el.value || '').trim()") or ""
        except Exception:
            return ""

    def _email_human(self, email: str) -> None:
        """人工模式：点击 → 清空 → 逐字输入 → 停顿 → 点下一步。"""
        self.disable_autofill()
        sel = self._email_selector()
        if not sel:
            raise RuntimeError("邮箱输入框不可见")
        loc = self.page.locator(sel).first
        loc.click(timeout=5000)
        self.wait_random(300, 700)
        self.clear_input()
        self.wait_random(200, 600)
        self.human_type(email)
        self.wait_random(400, 900)
        self.page.keyboard.press("Escape")
        self.wait(200)
        self._click_email_next()

    def _email_fill(self, email: str) -> None:
        self.disable_autofill()
        sel = self._email_selector()
        if not sel:
            raise RuntimeError("邮箱输入框不可见")
        loc = self.page.locator(sel).first
        loc.click(timeout=5000)
        self.page.keyboard.press("Escape")
        self.wait(150)
        loc.fill("")
        self.wait(100)
        loc.fill(email, timeout=5000)
        self.wait(300)
        self.page.keyboard.press("Escape")
        self.wait(200)
        self._click_email_next()

    def _email_type(self, email: str) -> None:
        self.disable_autofill()
        sel = self._email_selector()
        if not sel:
            raise RuntimeError("邮箱输入框不可见")
        loc = self.page.locator(sel).first
        loc.click(timeout=5000)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Backspace")
        self.wait(100)
        loc.type(email, delay=random.randint(25, 60), timeout=10000)
        self.wait(250)
        self.page.keyboard.press("Escape")
        self.wait(200)
        self._click_email_next()

    def _email_js(self, email: str) -> None:
        sel = self._email_selector()
        if not sel:
            self.page.wait_for_selector(EMAIL_SELECTORS[0], state="visible", timeout=10000)
            sel = self._email_selector()
            if not sel:
                raise RuntimeError("邮箱输入框不可见")
        self.disable_autofill()
        self.page.eval_on_selector(sel, _NATIVE_SET_JS, email)
        self.wait(500)
        self.page.keyboard.press("Escape")
        self.wait(200)
        self._click_email_next()

    def _click_email_next(self) -> None:
        for sel in EMAIL_NEXT_SELECTORS:
            if self.click_if_visible(sel, timeout_ms=4000):
                return
        self.submit_primary()

    # ---------- 密码输入 ----------
    def click_use_password(self) -> bool:
        """微软可能默认走无密码登录，需要先点"使用密码"。"""
        hit = self.click_text(PASSWORD_BYPASS_TEXTS, timeout_ms=4000)
        if hit:
            self.wait_random(1000, 1800)
            return True
        return False

    def password_locator(self, timeout_ms: int = 15000):
        """等到可见密码框。找不到抛 RuntimeError。"""
        import time as _t

        deadline = _t.time() + timeout_ms / 1000
        while _t.time() < deadline:
            self.click_use_password()
            for sel in PASSWORD_SELECTORS:
                try:
                    loc = self.page.locator(sel)
                    for idx in range(loc.count()):
                        item = loc.nth(idx)
                        if item.is_visible():
                            return item, f"{sel}[{idx}]"
                except Exception:
                    continue
            self.wait(300)
        raise RuntimeError("未找到可见密码框")

    def fill_password(self, password: str) -> bool:
        """写入密码并提交。优先人工逐字输入，JS 原生 setter 只作兑底。"""
        self.click_use_password()
        self.disable_autofill()
        locator, name = self.password_locator(timeout_ms=15000)
        try:
            locator.click(timeout=3000)
            self.wait_random(300, 800)
            self.clear_input()
            self.wait_random(200, 500)
            self.human_type(password)
            self.wait_random(300, 700)
        except Exception as exc:
            # 人工输入失败（元素拦截等）：退回 JS 注入
            if self.log:
                self.log.warn("password_input", f"人工输入失败改用注入: {exc}")
            try:
                locator.evaluate(_NATIVE_SET_JS, password)
            except Exception:
                locator.fill(password, timeout=5000)
        self.wait(200)
        try:
            filled = locator.evaluate("(el) => (el.value || '').length")
            if self.log:
                self.log.info("password_input", f"{name} 已写入密码，长度={filled}")
        except Exception:
            pass
        self.wait_random(300, 700)
        return self.submit_primary()

    # ---------- 人类化鼠标 ----------
    def human_prelude(self) -> None:
        """随机滚动/游荡/停顿，制造真人操作痕迹。"""
        for _ in range(random.randint(1, 4)):
            act = random.random()
            if act < 0.30:
                try:
                    self.page.evaluate(f"window.scrollBy(0, {random.randint(-200, 200)})")
                except Exception:
                    pass
                self.wait_random(200, 800)
            elif act < 0.50:
                self.move_mouse(
                    random.randint(100, 600), random.randint(100, 500), steps=random.randint(3, 8)
                )
                self.wait_random(300, 1200)
            elif act < 0.75:
                self.wait_random(500, 2500)
            else:
                self.move_mouse(
                    400 + random.random() * 100, 300 + random.random() * 100, steps=1
                )
                self.wait_random(100, 400)

    def move_mouse(self, x: float, y: float, steps: int = 1) -> None:
        try:
            self.page.mouse.move(x, y, steps=steps)
        except Exception:
            pass

    def natural_move(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """三段式人类轨迹：Bezier 加速接近 → 随机过冲 → 微调修正。"""
        cpx = (x1 + x2) / 2 + random.uniform(-150, 150)
        cpy = (y1 + y2) / 2 + random.uniform(-120, 120)

        steps = random.randint(8, 18)
        for i in range(steps + 1):
            t = i / steps
            ease = 1 - (1 - t) ** 3
            lin_x = (1 - ease) * x1 + ease * x2
            lin_y = (1 - ease) * y1 + ease * y2
            bez_x = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cpx + t**2 * x2
            bez_y = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cpy + t**2 * y2
            self.move_mouse(lin_x * 0.6 + bez_x * 0.4, lin_y * 0.6 + bez_y * 0.4, steps=1)
            self.wait(random.randint(6, 18))

        # 过冲：手没停稳
        if random.random() < 0.6:
            self.move_mouse(
                x2 + random.uniform(2, 8) * random.choice([-1, 1]),
                y2 + random.uniform(2, 6) * random.choice([-1, 1]),
                steps=1,
            )
            self.wait(random.randint(30, 80))

        self.move_mouse(x2, y2, steps=1)
        self.wait(random.randint(20, 60))

    def circular_tremor(self, x: float, y: float, duration_ms: int) -> None:
        """按住期间的圆周微颤，模拟手指自然颤抖。"""
        steps = max(duration_ms // 50, 5)
        radius = random.uniform(0.3, 2.0)
        for i in range(steps):
            angle = 2 * math.pi * i / steps + random.uniform(-0.3, 0.3)
            self.move_mouse(
                x + math.cos(angle) * radius * random.uniform(0.7, 1.3),
                y + math.sin(angle) * radius * random.uniform(0.7, 1.3),
                steps=1,
            )
            self.wait(random.randint(35, 70))

    def mouse_down(self) -> None:
        try:
            self.page.mouse.down()
        except Exception:
            pass

    def mouse_up(self) -> None:
        try:
            self.page.mouse.up()
        except Exception:
            pass

    def double_tap_then_hold(self, x: float, y: float) -> None:
        """双击 → 松开 → 长按。OutlookRegister 实测通过率较高的按压节奏。"""
        self.mouse_down()
        self.wait(random.randint(25, 55))
        self.mouse_up()
        self.wait(random.randint(80, 220))
        self.mouse_down()
        self.wait(random.randint(25, 55))
        self.mouse_up()
        self.wait(random.randint(120, 380))
        self.mouse_down()

    def screenshot(self, path: str) -> bool:
        try:
            self.page.screenshot(path=path)
            return True
        except Exception:
            return False
