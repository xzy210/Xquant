# data_loader.py - 数据加载模块 (strategy_app 兼容层)
"""
数据加载模块 - strategy_app 兼容层

实际实现位于 common/data_loader.py
"""
import sys
from pathlib import Path

# 将项目根目录添加到路径，确保可以导入 common
def _setup_common_path():
    """设置 common 模块路径"""
    # 当前文件路径: strategy_app/data_loader.py
    current_file = Path(__file__).resolve()
    # 项目根目录
    project_root = current_file.parent.parent
    
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    return project_root

_project_root = _setup_common_path()

# 从 common 导入所有内容
try:
    from common.data_loader import *
except ImportError as e:
    raise ImportError(
        f"无法从 common 导入 data_loader: {e}\n"
        f"请确保 common/data_loader.py 存在且没有语法错误"
    )
