from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any, Optional

class BaseStrategy(ABC):
    """选股策略基类"""
    
    def __init__(self):
        self.name = "Base Strategy"
        self.description = "Base strategy description"
        self.params = {}  # 策略参数

    @abstractmethod
    def check(self, code: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        检查股票是否符合策略
        
        Args:
            code: 股票代码
            data: 股票历史数据 (DataFrame)，包含 date, open, high, low, close, volume
            
        Returns:
            如果符合策略，返回包含结果信息的字典 (例如 {'code': '000001', 'reason': '...'})
            如果不符合，返回 None
        """
        pass

    def set_params(self, params: Dict[str, Any]):
        """设置策略参数"""
        self.params.update(params)
