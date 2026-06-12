@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. See README.md for setup.
    pause
    exit /b 1
)
if "%~1"=="" (
    ".venv\Scripts\python.exe" -m meet.tui
) else (
    ".venv\Scripts\meet.exe" %*
    if errorlevel 1 pause
)
endlocal
