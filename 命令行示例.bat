@echo off
REM Dump Search - 命令行用法示例
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo.
echo 1) 列出所有可用盘符
python -m dupehunter --list-drives
echo.

echo 2) 扫描当前目录，列出重复文件（只报告，不删除）
python -m dupehunter .
echo.

echo 3) 生成测试目录树以便试用（不会碰你的真实文件）
python tools\make_fixtures.py "%TEMP%\dupetest" --clean
python -m dupehunter "%TEMP%\dupetest"
echo.

echo 4) 大文件优先：只看 100MB 以上的重复文件
python -m dupehunter --list-drives
rem    python -m dupehunter D:\ --min-size 100M
echo.

echo 5) 把重复副本移入回收站（会先列出清单并要求确认）
rem    python -m dupehunter D:\ --min-size 100M --delete
echo.

endlocal
