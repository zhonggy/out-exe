"""验证码自动按压模块。

从 D:\\out\\OutlookRegister\\controllers\\outlook_controller.py 的
_captcha_hold / _wait_for_captcha_frame / _find_target / _pick_position /
_hold_and_wait / _execute_b2 / _check_captcha_result 移植重构而来：
- 去掉注册流程专用耦合（注册页文案、辅助邮箱绑定、IP 统计副作用）
- 人类化算法（Bezier 轨迹、圆周微颤、按压位置分布、btn2 模式加权）完整保留
- 统计与 IP 惩罚通过回调交给上层（proxy.ProxyManager），本模块只判定成败

流程：
    等待 iframe → 人类化前奏 → 找目标 → 移动 → 双击后长按
    → 微颤等待"再次按下" → click/dblclick → 检查结果
"""

from __future__ import annotations

import os
import random
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from .action import PageAction
from .detector import (
    CAPTCHA_BTN2_SELECTORS,
    CAPTCHA_TARGET_SELECTORS,
    PageDetector,
)

# btn2 两种点击模式的全局表现统计（跨线程共享，用于加权选择）
_stats_lock = threading.Lock()
_b2_attempts: Dict[str, int] = {"click": 0, "dblclick": 0}
_b2_success: Dict[str, int] = {"click": 0, "dblclick": 0}
_attempts = 0
_success = 0


def stats_snapshot() -> Dict[str, Any]:
    with _stats_lock:
        total = max(_attempts, 1)
        modes = {}
        for mode in ("click", "dblclick"):
            att = _b2_attempts.get(mode, 0)
            win = _b2_success.get(mode, 0)
            modes[mode] = {
                "attempts": att,
                "success": win,
                "rate": round(win / att * 100, 1) if att else None,
            }
        return {
            "attempts": _attempts,
            "success": _success,
            "rate": round(_success / total * 100, 1),
            "b2_modes": modes,
        }


def reset_stats() -> None:
    global _attempts, _success
    with _stats_lock:
        _attempts = 0
        _success = 0
        for d in (_b2_attempts, _b2_success):
            for k in d:
                d[k] = 0


class CaptchaResult:
    """按压结果。"""

    def __init__(self, success: bool, reason: str = "", frame_seen: bool = False, btn2_seen: bool = False):
        self.success = success
        self.reason = reason
        self.frame_seen = frame_seen
        self.btn2_seen = btn2_seen

    @property
    def ip_should_be_penalized(self) -> bool:
        """iframe 或 btn2 从未出现，通常是出口 IP 被风控，值得重罚。"""
        return not self.frame_seen or not self.btn2_seen

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CaptchaResult success={self.success} reason={self.reason!r}>"


class CaptchaSolver:
    """微软验证码按压器。

    strategy: 0 = 全自动按压，1 = 半自动（暂停等人工按压，只轮询结果）
    """

    def __init__(
        self,
        page,
        logger=None,
        max_retries: int = 3,
        strategy: int = 0,
        screenshot_dir: Optional[str] = None,
        screenshot_on_fail: bool = True,
        success_check: Optional[Callable[[], bool]] = None,
        manual_timeout: int = 300,
    ):
        self.page = page
        self.log = logger
        self.max_retries = max(0, int(max_retries))
        self.strategy = int(strategy)
        self.screenshot_dir = screenshot_dir
        self.screenshot_on_fail = screenshot_on_fail
        self.manual_timeout = manual_timeout
        self.action = PageAction(page, logger=logger)
        self.detector = PageDetector(page, logger=logger)
        # 上层注入的"已通过"判据（例如已进入邮箱页）
        self.success_check = success_check or self.detector.is_mailbox

    # ---------- 入口 ----------
    def solve(self) -> CaptchaResult:
        if self.strategy == 1:
            return self._manual()
        return self._auto_hold()

    # ---------- 半自动 ----------
    def _manual(self) -> CaptchaResult:
        self._log("warn", "captcha_manual", "请手动完成验证码按压，等待进入下一步...")
        for _ in range(self.manual_timeout):
            self.action.wait(1000)
            try:
                if self.success_check():
                    self.action.wait(2000)
                    self._log("ok", "captcha_manual", "已通过验证")
                    return CaptchaResult(True, "manual_pass", frame_seen=True, btn2_seen=True)
            except Exception:
                pass
        self._log("fail", "captcha_manual", f"超时（{self.manual_timeout}s）未通过")
        return CaptchaResult(False, "manual_timeout", frame_seen=True)

    # ---------- 全自动按压 ----------
    def _auto_hold(self) -> CaptchaResult:
        global _attempts, _success

        if not self._wait_for_frame():
            self._log("fail", "captcha_frame", "未检测到验证码 iframe")
            self._save_screenshot("no_frame")
            return CaptchaResult(False, "iframe_never_appeared")

        frame1, frame2 = self.detector.captcha_frame()
        if frame2 is None:
            return CaptchaResult(False, "iframe_lost")

        self.action.human_prelude()
        btn2_seen = False

        for attempt in range(self.max_retries + 1):
            self._log("info", "captcha_hold", f"第 {attempt + 1}/{self.max_retries + 1} 次按压")
            self.action.wait_random(200, 600)

            # ① 定位目标
            box, label = self._find_target(frame2, attempt)
            if not box:
                self._log("warn", "captcha_hold", "未找到可按压目标，重试")
                continue

            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            pos_name, x, y = self._pick_position(box, cx, cy)
            self._log("info", "captcha_hold", f"target={label} pos={pos_name}")

            # ② 从远处人类化移动到目标
            from_x = x + random.uniform(-250, 250)
            from_y = y + random.uniform(-250, 250)
            self.action.move_mouse(from_x, from_y, steps=1)
            self.action.wait_random(40, 150)
            self.action.natural_move(from_x, from_y, x, y)

            # ③ 双击 → 长按
            self.action.double_tap_then_hold(x, y)

            # ④ 按住微颤，等 "再次按下" 出现
            if not self._hold_and_wait(frame2, x, y):
                self.action.mouse_up()
                self._log("warn", "captcha_hold", "btn2 未出现，重试")
                continue
            btn2_seen = True

            # ⑤ click / dblclick 加权轮换
            mode = self._pick_b2_mode()
            self._record_attempt(mode)
            if not self._execute_b2(frame2, mode):
                self._log("warn", "captcha_hold", f"btn2 操作失败 mode={mode}")
                continue

            # ⑥ 判定结果
            ok, retry = self._check_result(frame1, frame2)
            if not ok:
                break
            if not retry:
                with _stats_lock:
                    _attempts += 1
                    _success += 1
                    _b2_success[mode] = _b2_success.get(mode, 0) + 1
                self._log("ok", "captcha_pass", f"验证码通过 mode={mode} {self._stats_line()}")
                return CaptchaResult(True, "passed", frame_seen=True, btn2_seen=True)
            self._log("warn", "captcha_hold", "验证码重置，继续重试")

        with _stats_lock:
            _attempts += 1
        self._save_screenshot("final_fail")
        reason = "btn2_appeared_but_failed" if btn2_seen else "btn2_never_appeared"
        self._log("fail", "captcha_fail", f"验证码失败 reason={reason} {self._stats_line()}")
        return CaptchaResult(False, reason, frame_seen=True, btn2_seen=btn2_seen)

    # ---------- 子步骤 ----------
    def _wait_for_frame(self, timeout_s: int = 15) -> bool:
        """轮询等待验证码 iframe 内部元素就绪。"""
        for _ in range(timeout_s):
            _f1, f2 = self.detector.captcha_frame()
            if f2 is not None:
                for sel in CAPTCHA_TARGET_SELECTORS:
                    try:
                        if f2.locator(sel).count() > 0:
                            box = f2.locator(sel).first.bounding_box()
                            if box and box["width"] > 5:
                                self._log("info", "captcha_frame", f"iframe 就绪: {sel}")
                                self.action.wait_random(500, 1500)
                                return True
                    except Exception:
                        continue
            self.action.wait(1000)
        return False

    def _find_target(self, frame2, attempt: int) -> Tuple[Optional[Dict[str, float]], str]:
        """遍历候选选择器，返回尺寸 >8px 的目标 bounding_box。"""
        for sel in CAPTCHA_TARGET_SELECTORS:
            try:
                candidates = frame2.locator(sel)
                count = candidates.count()
                if count <= 0:
                    continue
                idx = attempt % min(count, 3)
                box = candidates.nth(idx).bounding_box()
                if box and box["width"] > 8 and box["height"] > 8:
                    return box, f"{sel}[{idx}/{count}]"
            except Exception:
                continue
        return None, ""

    @staticmethod
    def _pick_position(box: Dict[str, float], cx: float, cy: float) -> Tuple[str, float, float]:
        """按压点分布：中心 12% / 边缘 18% / 角落 18% / 随机偏移 52%。"""
        w, h = box["width"], box["height"]
        r = random.random()
        if r < 0.12:
            return "center", cx + random.uniform(-3, 3), cy + random.uniform(-3, 3)
        if r < 0.30:
            edge = random.choice(["t", "b", "l", "r"])
            if edge == "t":
                return f"edge.{edge}", cx + random.uniform(-w * 0.3, w * 0.3), box["y"] + random.uniform(1, 5)
            if edge == "b":
                return f"edge.{edge}", cx + random.uniform(-w * 0.3, w * 0.3), box["y"] + h - random.uniform(1, 5)
            if edge == "l":
                return f"edge.{edge}", box["x"] + random.uniform(1, 5), cy + random.uniform(-h * 0.3, h * 0.3)
            return f"edge.{edge}", box["x"] + w - random.uniform(1, 5), cy + random.uniform(-h * 0.3, h * 0.3)
        if r < 0.48:
            corner = random.choice(["tl", "tr", "bl", "br"])
            dx = random.uniform(2, 8)
            dy = random.uniform(2, 8)
            if corner == "tl":
                return f"corner.{corner}", box["x"] + dx, box["y"] + dy
            if corner == "tr":
                return f"corner.{corner}", box["x"] + w - dx, box["y"] + dy
            if corner == "bl":
                return f"corner.{corner}", box["x"] + dx, box["y"] + h - dy
            return f"corner.{corner}", box["x"] + w - dx, box["y"] + h - dy
        return "random", cx + random.uniform(-w * 0.4, w * 0.4), cy + random.uniform(-h * 0.4, h * 0.4)

    def _hold_and_wait(self, frame2, x: float, y: float) -> bool:
        """按住微颤，等待"再次按下"出现；出现后延续按压 1.5-4.5s。"""
        self.action.circular_tremor(x, y, duration_ms=random.randint(600, 1800))
        appeared = False
        for sel in CAPTCHA_BTN2_SELECTORS:
            try:
                frame2.locator(sel).wait_for(state="visible", timeout=10000)
                appeared = True
                break
            except Exception:
                continue
        if appeared:
            extra = random.randint(1500, 4500)
            self._log("info", "captcha_hold", f"btn2 出现，延续按压 {extra}ms")
            self.action.circular_tremor(x, y, duration_ms=extra)
        return appeared

    @staticmethod
    def _pick_b2_mode() -> str:
        """按历史通过率加权选择 click / dblclick，保留探索空间。"""
        with _stats_lock:
            attempts = dict(_b2_attempts)
            wins = dict(_b2_success)
        weights = {}
        for mode in ("click", "dblclick"):
            att = attempts.get(mode, 0)
            win = wins.get(mode, 0)
            if att >= 10:
                rate = win / max(att, 1)
                weights[mode] = rate**2 * 10 if rate >= 0.30 else max(0.05, rate)
            elif att >= 5:
                weights[mode] = max(0.1, win / max(att, 1))
            else:
                weights[mode] = 1.0
        return random.choices(list(weights), weights=list(weights.values()), k=1)[0]

    @staticmethod
    def _record_attempt(mode: str) -> None:
        with _stats_lock:
            _b2_attempts[mode] = _b2_attempts.get(mode, 0) + 1

    def _execute_b2(self, frame2, mode: str) -> bool:
        """操作"再次按下"。

        用 locator.click(position=...) 而非裸坐标 mouse.click：
        Playwright 会处理嵌套 iframe 坐标转换与命中检测，
        避免 bounding_box 坐标在部分平台落空。
        """
        self.action.wait_random(300, 900)
        el = None
        for sel in CAPTCHA_BTN2_SELECTORS:
            try:
                loc = frame2.locator(sel)
                if loc.count() > 0:
                    el = loc.first
                    break
            except Exception:
                continue
        if el is None:
            return False

        try:
            el.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        box = None
        try:
            box = el.bounding_box()
        except Exception:
            pass
        if not box:
            return False

        off_x = random.uniform(-box["width"] * 0.35, box["width"] * 0.35)
        off_y = random.uniform(-box["height"] * 0.35, box["height"] * 0.35)
        rel_x = box["width"] / 2 + off_x
        rel_y = box["height"] / 2 + off_y

        # 先人类化移动过去，再做元素级点击
        self.action.move_mouse(
            box["x"] + box["width"] / 2 + off_x,
            box["y"] + box["height"] / 2 + off_y,
            steps=random.randint(3, 10),
        )
        self.action.wait_random(50, 180)

        try:
            el.click(position={"x": rel_x, "y": rel_y}, timeout=5000)
        except Exception:
            return False

        if mode == "dblclick":
            self.action.wait_random(80, 200)
            try:
                el.click(
                    position={
                        "x": rel_x + random.uniform(-3, 3),
                        "y": rel_y + random.uniform(-3, 3),
                    },
                    timeout=5000,
                )
            except Exception:
                pass
        return True

    def _check_result(self, frame1, frame2) -> Tuple[bool, bool]:
        """判定验证码结果。

        返回 (ok, retry)：
            (True, False)  → 通过
            (True, True)   → 验证码重置，需重试
            (False, False) → 失败 / IP 被风控
        """

        def passed() -> bool:
            try:
                if self.success_check():
                    return True
            except Exception:
                pass
            # 通过后常见的下一步：辅助邮箱输入框
            try:
                if self.page.locator("#EmailAddress").count() > 0:
                    return True
            except Exception:
                pass
            return False

        def log_r(tag: str, msg: str) -> None:
            self._log("info", "captcha_result", f"{tag} | {msg}")

        try:
            # 加载动画消失代表提交已被处理
            self.page.locator(".draw").wait_for(state="detached")
            log_r("step1", ".draw 已消失")
            try:
                self.page.locator('[role="status"][aria-label="正在加载..."]').wait_for(timeout=5000)
                log_r("step2", "检测到加载状态，等待 8s")
                self.action.wait(8000)

                if self.detector.is_risk_blocked():
                    log_r("step2", "风控拦截：异常活动/维护")
                    return False, False

                leftover = False
                for sel in ('[aria-label="可访问性挑战"]', '[aria-label="Accessibility challenge"]'):
                    try:
                        if frame2.locator(sel).count() > 0:
                            leftover = True
                            break
                    except Exception:
                        continue
                if leftover:
                    if passed():
                        log_r("step2", "挑战元素残留但已进入下一步 → 通过")
                        return True, False
                    log_r("step2", "验证码重置 → 重试")
                    return True, True

                log_r("step2", "无异常无重置 → 通过")
                return True, False
            except Exception:
                if self._has_cancel_button():
                    log_r("step2-ex", "出现取消按钮 → 通过")
                    return True, False
                if passed():
                    log_r("step2-ex", "已进入下一步 → 通过")
                    return True, False
                try:
                    frame1.get_by_text("请再试一次").wait_for(timeout=15000)
                    log_r("step2-ex", "提示请再试一次 → 重试")
                    return True, True
                except Exception:
                    log_r("step2-ex", "无重试提示 → 失败")
                    return False, False
        except Exception:
            if self._has_cancel_button():
                log_r("outer-ex", "出现取消按钮 → 通过")
                return True, False
            if passed():
                log_r("outer-ex", ".draw 异常但已进入下一步 → 通过")
                return True, False
            log_r("outer-ex", ".draw 未消失 → 失败")
            return False, False

    def _has_cancel_button(self) -> bool:
        for text in ("取消", "Cancel"):
            try:
                if self.page.get_by_text(text, exact=False).count() > 0:
                    return True
            except Exception:
                continue
        return False

    # ---------- 辅助 ----------
    def _save_screenshot(self, tag: str) -> None:
        if not self.screenshot_on_fail or not self.screenshot_dir:
            return
        try:
            os.makedirs(self.screenshot_dir, exist_ok=True)
            path = os.path.join(self.screenshot_dir, f"captcha_{tag}_{int(time.time())}.png")
            if self.action.screenshot(path):
                self._log("info", "captcha_shot", f"已保存截图 {path}")
        except Exception as exc:
            self._log("warn", "captcha_shot", f"截图失败: {exc}")

    @staticmethod
    def _stats_line() -> str:
        snap = stats_snapshot()
        parts = [f"total={snap['success']}/{snap['attempts']}={snap['rate']}%"]
        for mode, data in snap["b2_modes"].items():
            if data["attempts"]:
                parts.append(f"{mode}={data['success']}/{data['attempts']}")
        return " ".join(parts)

    def _log(self, level: str, stage: str, message: str) -> None:
        if not self.log:
            return
        fn = getattr(self.log, level, None) or getattr(self.log, "info")
        fn(stage, message)
