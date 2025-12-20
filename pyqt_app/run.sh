#!/bin/bash
# 来财 - 启动脚本

cd "$(dirname "$0")"

# 检查是否安装了依赖
python3 -c "import PyQt6" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "正在安装依赖..."
    pip3 install -r requirements.txt
fi

# 运行应用
python3 main.py

