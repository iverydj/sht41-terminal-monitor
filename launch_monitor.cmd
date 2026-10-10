@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
mode con cols=140 lines=46 >nul

if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Run setup.cmd once first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "sht41_monitor.py"
if errorlevel 1 (
    echo.
    echo The program exited with an error.
    pause
)
