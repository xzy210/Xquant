@echo off
REM 来财 - 启动脚本

cd /d %~dp0

REM 检查是否安装了依赖
python -c "import PyQt6" 2>nul
if errorlevel 1 (
    echo 正在安装依赖...
    pip install -r requirements.txt
)

REM 运行应用
python main.py

pause

