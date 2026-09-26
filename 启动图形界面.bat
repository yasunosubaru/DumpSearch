@echo off
REM Dump Search - 图形界面启动器
setlocal
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m dupehunter --gui
) else (
    python -m dupehunter --gui
)
endlocal
