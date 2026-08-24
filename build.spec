# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

用法：
    pyinstaller --noconfirm build.spec

为什么必须 onedir 而不是 onefile：

- ``patchright/driver/`` 含 node.exe 和完整 node 包（约 96MB），onefile 下每次启动
  都要解压到临时目录才能用，启动多等好几秒。
- 本项目是双进程模型（GUI + 执行进程），onefile 下两个进程各解压一份，
  磁盘占用和启动时间都翻倍。
- 杀软对 onefile 自解压行为误报率高。

需要显式声明的隐式依赖：

- ``patchright/driver`` 整目录 —— 自动分析抓不到（运行时才按路径查找）
- ``apscheduler`` 的 executors/triggers/jobstores —— 按字符串动态导入
- ``flow`` 子模块 —— 流程注册表靠 import 时的装饰器副作用填充

还需要一个 runtime hook（``scripts/rthook_streams.py``）：``console=False`` 时
PyInstaller 把 ``sys.stdout``/``stderr`` 置为 None，而本项目的 logger 和 CLI 大量
用到它们，不补会在启动时直接 AttributeError。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# spec 文件执行时 __file__ 不可靠，用 SPECPATH（PyInstaller 注入）
PROJECT_ROOT = Path(SPECPATH).resolve()  # noqa: F821

APP_NAME = "OutlookAutomation"


# ---------------------------------------------------------------- datas
def patchright_driver_datas():
    """patchright 的 node driver 必须整目录随包，否则浏览器起不来。"""
    try:
        import patchright
    except ImportError:
        raise SystemExit(
            "未安装 patchright，无法打包。先执行 pip install -r requirements.txt"
        )
    driver = Path(patchright.__file__).parent / "driver"
    if not driver.is_dir():
        raise SystemExit(f"未找到 patchright driver 目录: {driver}")
    node = driver / ("node.exe" if sys.platform == "win32" else "node")
    if not node.is_file():
        raise SystemExit(f"patchright driver 缺少 {node.name}: {driver}")
    return [(str(driver), "patchright/driver")]


datas = []
datas += patchright_driver_datas()

# 默认配置随包发布，首次启动由 config/loader.py 拷到用户数据目录
datas += [(str(PROJECT_ROOT / "config" / "config.yaml"), "config")]


# ---------------------------------------------------------------- hidden imports
hiddenimports = []

# APScheduler 的 executor / trigger / jobstore 都是运行时按字符串导入
hiddenimports += collect_submodules("apscheduler")

# 流程注册表依赖 import 副作用（@register_flow 装饰器）
hiddenimports += [
    "flow",
    "flow.action",
    "flow.base",
    "flow.captcha",
    "flow.checkpoint",
    "flow.detector",
    "flow.login_flow",
]

# GUI 页面由 desktop/views/__init__.py 汇总导入，这里兜一层
hiddenimports += collect_submodules("desktop")

# 业务包
hiddenimports += [
    "account",
    "browser",
    "config",
    "database",
    "logger",
    "proxy",
    "task",
]


# ---------------------------------------------------------------- excludes
# 这些不参与运行，排掉能显著减小体积
excludes = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "pytest",
    "_pytest",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtTest",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
]


a = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

icon_path = PROJECT_ROOT / "assets" / "app.ico"

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 压缩会显著提高杀软误报率
    console=False,      # GUI 不弹黑窗；执行进程用 CREATE_NO_WINDOW
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.is_file() else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
