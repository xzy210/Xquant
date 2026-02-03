# indicators.py - 技术指标计算模块 (兼容层)
"""
技术指标计算模块 - pyqt_app 兼容层

实际实现已迁移到 common/indicators.py
此文件保留以保持向后兼容性
"""
import sys
from pathlib import Path

# 将项目根目录添加到路径，确保可以导入 common
def _setup_common_path():
    """设置 common 模块路径"""
    # 当前文件路径: pyqt_app/indicators.py
    current_file = Path(__file__).resolve()
    # 项目根目录
    project_root = current_file.parent.parent
    
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    return project_root

_project_root = _setup_common_path()

# 从 common 导入所有内容
try:
    from common.indicators import *
except ImportError as e:
    # 如果导入失败，提供有用的错误信息
    raise ImportError(
        f"无法从 common 导入 indicators: {e}\n"
        f"请确保 common/indicators.py 存在且没有语法错误"
    )

# 保持向后兼容的别名（如果有需要）
# 此处无需额外别名，所有内容已通过 * 导入
