@echo off
setlocal EnableDelayedExpansion
set "PYTHONUTF8=1"
cd /d "%~dp0"
title OutlookAutomation

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] virtualenv not found. Run install.bat first!
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"

echo ==================================================
echo   OutlookAutomation
echo --------------------------------------------------
echo   Panel : http://127.0.0.1:8000
if exist "data\api_token" (
    set /p OA_TOKEN=<data\api_token
    echo   Token : !OA_TOKEN!
) else (
    echo   Token : ^(first start^) see log lines below:
)
echo --------------------------------------------------

REM auto-open browser after server warms up
start "" cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:8000"

echo   Close this window or press Ctrl+C to stop the panel.
echo   NOTE: login tasks keep running in their own process
echo         even if this window is closed. Use panel STOP button to halt them.
echo ==================================================
echo.

python main.py serve

echo.
echo [info] panel stopped.
pause
