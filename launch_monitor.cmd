@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
mode con cols=140 lines=46 >nul

if not exist ".venv\Scripts\python.exe" (
    echo 실행 환경이 없습니다. 먼저 setup.cmd를 한 번 실행하세요.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "sht41_monitor.py"
if errorlevel 1 (
    echo.
    echo 프로그램이 오류로 종료되었습니다.
    pause
)
