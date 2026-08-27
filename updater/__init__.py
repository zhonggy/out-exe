"""updater 包：版本检查与安装包下载。

分发渠道是 GitHub Releases，安装包由 Inno Setup 构建，同版本号可覆盖安装。
"""

from .manager import (
    CheckResult,
    ReleaseInfo,
    UpdateManager,
    compare_versions,
    get_update_manager,
    human_bytes,
    is_newer,
    parse_version,
)

__all__ = [
    "CheckResult",
    "ReleaseInfo",
    "UpdateManager",
    "compare_versions",
    "get_update_manager",
    "human_bytes",
    "is_newer",
    "parse_version",
]
