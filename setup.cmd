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
echo 설치가 완료되었습니다. "SHT41 Monitor" 바로가기를 실행하세요.
pause
exit /b 0

:error
echo.
echo 설치에 실패했습니다.
pause
exit /b 1
