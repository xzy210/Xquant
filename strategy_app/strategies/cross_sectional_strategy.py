from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, List, Any
from .base_strategy import BaseStrategy

class CrossSectionalStrategy(BaseStrategy):
    """
    截面策略基类 (Base Class for Cross-Sectional Strategies)
    
    用于在特定时间点（如每月月末）对全市场或股票池进行选股的策略。
    """
    
    def __init__(self):
        super().__init__()
        self.type = "cross_sectional" # 标识策略类型

    @abstractmethod
    def prepare_factors(self, data_dict: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        预计算因子
        
        :param data_dict: 所有股票的历史数据 {code: dataframe}
        :return: 包含所有股票因子的 MultiIndex DataFrame (Index=[date, code], Columns=[factors...])
        """
        pass

    @abstractmethod
    def on_rebalance(self, context, valid_codes: List[str], daily_factors: pd.DataFrame):
        """
        调仓日逻辑
        
        :param context: 交易上下文
        :param valid_codes: 当日可交易的股票代码列表
        :param daily_factors: 当日的截面因子数据
        """
        pass
    
    # 覆盖基类的方法，截面策略通常不需要逐K线运行
    def on_bar(self, context, bars, history=None):
        pass
    
    def check(self, code: str, data: pd.DataFrame) -> Any:
        # 截面策略通常不支持单只股票的简单 check
        return None

