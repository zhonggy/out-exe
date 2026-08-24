@echo off
setlocal EnableDelayedExpansion
set "PYTHONUTF8=1"
cd /d "%~dp0"

echo ==================================================
echo   OutlookAutomation one-click setup (dev mode)
echo ==================================================
echo.
echo   This script sets up a local dev environment.
echo   End users should use OutlookAutomation-Setup.exe instead.
echo.

REM ---------- detect local proxy (needed for GitHub downloads) ----------
set "PROXY="
for %%p in (7897 7890 10809 1080) do (
    netstat -ano | find "127.0.0.1:%%p" | find "LISTENING" >nul 2>nul && (
        if not defined PROXY set "PROXY=http://127.0.0.1:%%p"
    )
)
if defined PROXY (
    echo [info] local proxy detected: !PROXY! , downloads will use it
    set "HTTP_PROXY=!PROXY!"
    set "HTTPS_PROXY=!PROXY!"
) else (
    echo [warn] no local proxy detected. If downloads fail, start your proxy and retry.
)
echo.

REM ---------- check python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo         Install Python 3.11+ and check "Add python.exe to PATH"
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [info] python version:
python --version
echo.

REM ---------- venv ----------
if exist ".venv\Scripts\activate.bat" (
    echo [step 1/5] venv already exists, skip
) else (
    echo [step 1/5] creating virtualenv .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] failed to create virtualenv
        pause
        exit /b 1
    )
)
call ".venv\Scripts\activate.bat"
echo.

REM ---------- deps ----------
echo [step 2/5] installing dependencies ...
python -m pip install -q --disable-pip-version-check --upgrade pip
python -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. Check network. CN mirror:
    echo         python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)
echo        dependencies installed
echo.

REM ---------- patchright chromium ----------
set "HAVE_CHROMIUM=0"
if exist "%LOCALAPPDATA%\ms-playwright" (
    dir /b "%LOCALAPPDATA%\ms-playwright" 2>nul | findstr /C:"chromium" >nul && set "HAVE_CHROMIUM=1"
)
if "!HAVE_CHROMIUM!"=="1" (
    echo [step 3/5] Patchright Chromium already installed, skip
) else (
    echo [step 3/5] installing Patchright Chromium (~230MB, please wait^) ...
    python -m patchright install chromium
    if errorlevel 1 (
        echo [ERROR] chromium install failed. Make sure proxy is running, then retry.
        pause
        exit /b 1
    )
)
echo.

REM ---------- fingerprint-chromium ----------
echo [step 4/5] downloading fingerprint-chromium kernel (~180MB) ...
python scripts\setup_browser.py
if errorlevel 1 (
    echo [WARN] fingerprint kernel download failed. Framework still works with
    echo        built-in Chromium: clear browser.executable_path in config.yaml
)
echo.

REM ---------- doctor ----------
echo [step 5/5] environment self-check ...
python main.py doctor
echo.

echo ==================================================
echo   Setup done! Next steps:
echo     1. Edit accounts.txt, one per line: email----password
echo     2. start.bat        (or: python main.py gui)
echo     3. In the GUI: import accounts, dispatch tasks, click Start
echo ==================================================
pause
