@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM 开发模式启动桌面 GUI。正式发布版是安装包里的 OutlookAutomation.exe，
REM 双击即可，不需要这个脚本。

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] virtualenv not found. Run install.bat first!
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"

echo ==================================================
echo   OutlookAutomation - Desktop (dev mode)
echo --------------------------------------------------
echo   Data dir : %%APPDATA%%\OutlookAutomation (frozen)
echo              project root (dev mode)
echo --------------------------------------------------
echo   Close the window to exit the GUI.
echo   NOTE: login tasks run in a separate process and
echo         keep running after the GUI closes. Use the
echo         STOP button on the Tasks page to halt them.
echo ==================================================
echo.

python main.py gui
