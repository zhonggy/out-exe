"""日志系统：控制台 + 文件轮转 + 内存环形缓冲（供面板实时查看）。

日志行格式：
    [2026-08-21 13:07:38] [INFO ] [LOGIN] [password_input] task=12 acc=a@b.com 消息内容
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "OK": logging.INFO,
    "ERROR": logging.ERROR,
    "FAIL": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# 环形缓冲：面板 /api/logs 直接读，避免反复读文件
_BUFFER_SIZE = 2000
_buffer: Deque[Dict[str, Any]] = deque(maxlen=_BUFFER_SIZE)
_buffer_lock = threading.Lock()
_seq = 0

_configured = False
_config_lock = threading.Lock()
_ROOT_NAME = "outlook_automation"


def _push_buffer(record: Dict[str, Any]) -> None:
    global _seq
    with _buffer_lock:
        _seq += 1
        record["seq"] = _seq
        _buffer.append(record)


def get_buffer(after_seq: int = 0, limit: int = 200, level: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取内存日志。after_seq 用于增量拉取（面板轮询）。"""
    want = (level or "").upper()
    with _buffer_lock:
        items = [r for r in _buffer if r["seq"] > after_seq]
    if want:
        items = [r for r in items if r["level"].upper() == want]
    return items[-limit:]


def buffer_cursor() -> int:
    with _buffer_lock:
        return _seq


def clear_buffer() -> None:
    global _seq
    with _buffer_lock:
        _buffer.clear()
        _seq = 0


class _SafeStreamHandler(logging.StreamHandler):
    """Windows 控制台 GBK 编码下中文/箭头可能报错，降级为忽略不可编码字符。"""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - 环境相关
        try:
            super().emit(record)
        except UnicodeEncodeError:
            try:
                msg = self.format(record)
                enc = getattr(self.stream, "encoding", None) or "utf-8"
                self.stream.write(msg.encode(enc, errors="replace").decode(enc, errors="replace") + self.terminator)
                self.flush()
            except Exception:
                self.handleError(record)


def setup_logging(
    log_dir: str | Path = "logs",
    level: str = "INFO",
    console: bool = True,
    to_file: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    force: bool = False,
) -> logging.Logger:
    """初始化根 logger。重复调用默认幂等。"""
    global _configured
    with _config_lock:
        root = logging.getLogger(_ROOT_NAME)
        if _configured and not force:
            return root

        root.setLevel(LEVELS.get(level.upper(), logging.INFO))
        root.propagate = False
        for handler in list(root.handlers):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        fmt = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)-5s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        if console:
            sh = _SafeStreamHandler(stream=sys.stdout)
            sh.setFormatter(fmt)
            root.addHandler(sh)

        if to_file:
            d = Path(log_dir)
            d.mkdir(parents=True, exist_ok=True)
            fname = d / f"{time.strftime('%Y-%m-%d')}_{os.getpid()}.log"
            fh = RotatingFileHandler(
                fname, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            fh.setFormatter(fmt)
            root.addHandler(fh)

            err = d / "error.log"
            eh = RotatingFileHandler(
                err, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            eh.setLevel(logging.ERROR)
            eh.setFormatter(fmt)
            root.addHandler(eh)

        _configured = True
        return root


def setup_from_config(cfg, force: bool = False) -> logging.Logger:
    """用 Config 对象初始化日志。"""
    return setup_logging(
        log_dir=cfg.path_of("logger.dir", "logs"),
        level=str(cfg.get("logger.level", "INFO")),
        console=bool(cfg.get("logger.console", True)),
        to_file=bool(cfg.get("logger.file", True)),
        max_bytes=int(cfg.get("logger.max_bytes", 10 * 1024 * 1024)),
        backup_count=int(cfg.get("logger.backup_count", 5)),
        force=force,
    )


class FlowLogger:
    """带上下文的日志器：flow / stage / task_id / account 自动拼进日志行。"""

    def __init__(
        self,
        name: str = "app",
        flow: str = "SYS",
        task_id: Optional[int] = None,
        account: str = "",
    ):
        self._logger = logging.getLogger(f"{_ROOT_NAME}.{name}")
        self.flow = flow
        self.task_id = task_id
        self.account = account

    def bind(self, **kwargs: Any) -> "FlowLogger":
        """派生一个带新上下文的子 logger（不修改自身）。"""
        child = FlowLogger(
            name=self._logger.name.split(".", 1)[-1],
            flow=kwargs.get("flow", self.flow),
            task_id=kwargs.get("task_id", self.task_id),
            account=kwargs.get("account", self.account),
        )
        return child

    # ---------- 核心 ----------
    def event(self, level: str, stage: str, message: str, **extra: Any) -> None:
        level_up = (level or "INFO").upper()
        py_level = LEVELS.get(level_up, logging.INFO)

        bits = [f"[{self.flow}]", f"[{stage}]"]
        if self.task_id is not None:
            bits.append(f"task={self.task_id}")
        if self.account:
            bits.append(f"acc={self.account}")
        for k, v in extra.items():
            bits.append(f"{k}={v}")
        line = " ".join(bits) + f" {message}"

        self._logger.log(py_level, line)
        _push_buffer(
            {
                "ts": time.time(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "level": level_up,
                "flow": self.flow,
                "stage": stage,
                "task_id": self.task_id,
                "account": self.account,
                "message": message,
                "extra": extra or {},
            }
        )

    # ---------- 快捷方法 ----------
    def debug(self, stage: str, message: str, **extra: Any) -> None:
        self.event("DEBUG", stage, message, **extra)

    def info(self, stage: str, message: str, **extra: Any) -> None:
        self.event("INFO", stage, message, **extra)

    def ok(self, stage: str, message: str, **extra: Any) -> None:
        self.event("OK", stage, message, **extra)

    def warn(self, stage: str, message: str, **extra: Any) -> None:
        self.event("WARN", stage, message, **extra)

    def error(self, stage: str, message: str, **extra: Any) -> None:
        self.event("ERROR", stage, message, **extra)

    def fail(self, stage: str, message: str, **extra: Any) -> None:
        self.event("FAIL", stage, message, **extra)

    def exception(self, stage: str, message: str, exc: BaseException) -> None:
        self.event("ERROR", stage, f"{message}: {exc.__class__.__name__}: {exc}")
        self._logger.debug("traceback", exc_info=exc)


def get_logger(
    name: str = "app",
    flow: str = "SYS",
    task_id: Optional[int] = None,
    account: str = "",
) -> FlowLogger:
    return FlowLogger(name=name, flow=flow, task_id=task_id, account=account)
