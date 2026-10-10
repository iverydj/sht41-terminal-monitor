@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Setup complete. Start the monitor with launch_monitor.cmd.
pause
exit /b 0

:error
echo.
echo Setup failed.
pause
exit /b 1
