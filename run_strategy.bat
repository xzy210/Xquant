@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动策略研究...
python strategy_app\main.py
pause
