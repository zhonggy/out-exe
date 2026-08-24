"""logger 包：控制台 + 文件 + 内存缓冲 + 可插拔 sink。"""

from .logger import (
    FlowLogger,
    add_sink,
    buffer_cursor,
    clear_buffer,
    clear_sinks,
    get_buffer,
    get_logger,
    remove_sink,
    setup_from_config,
    setup_logging,
)

__all__ = [
    "FlowLogger",
    "add_sink",
    "buffer_cursor",
    "clear_buffer",
    "clear_sinks",
    "get_buffer",
    "get_logger",
    "remove_sink",
    "setup_from_config",
    "setup_logging",
]
