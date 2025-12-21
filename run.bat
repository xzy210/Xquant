@echo off
chcp 65001 > nul
REM 来财 - 启动脚本

cd /d %~dp0

REM =========== 激活 conda 环境 ===========
echo 正在激活 stock 环境...
call conda activate stock
if errorlevel 1 (
    echo Conda 激活失败，尝试使用旧版 activate 命令...
    call activate stock
)
REM =====================================

REM 检查是否安装了依赖
python -c "import PyQt6" 2>nul
if errorlevel 1 (
    echo 正在安装依赖...
    pip install -r requirements.txt
)

REM 运行应用
python main.py

pause
