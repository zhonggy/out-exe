"""测试脚本共用的输出兜底。

Windows 上 Python 的 stdout 编码跟随系统 locale（GitHub runner 是 cp1252，
国内机器常是 gbk）。本项目日志与提示全是中文，直接 print 会抛
UnicodeEncodeError，把本来通过的测试变成失败。

这里在导入时就把标准流切成 UTF-8。放在独立模块里，是为了让三个冒烟脚本
在 import 任何业务代码之前先执行它。
"""

from __future__ import annotations

import sys


def force_utf8() -> None:
    """把 stdout/stderr 切成 UTF-8，不可编码字符退化为替代符而非抛异常。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # 流已被重定向到不支持 reconfigure 的对象：忽略
            pass


force_utf8()
