@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [DEPRECATED] run_strategy.bat 是旧策略研究入口，请优先使用 run_app.py
echo 正在启动旧策略研究入口...
python strategy_app\main.py
pause
