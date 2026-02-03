from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any, Optional

class BaseStrategy(ABC):
    """选股与回测策略基类"""
    
    def __init__(self):
        self.name = "Base Strategy"
        self.description = "Base strategy description"
        self.params = {}

    @abstractmethod
    def check(self, code: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        【选股模式】检查股票是否符合策略 (静态扫描)
        """
        pass

    def initialize(self, context):
        """
        【回测模式】初始化，设置全局变量等
        """
        pass

    def on_bar(self, context, bars: Dict[str, Any], history: Dict[str, pd.DataFrame] = None):
        """
        【回测模式】每根K线调用一次
        :param context: 交易上下文，包含账户资金、持仓、下单函数
        :param bars: 当前时刻的数据切片 {code: row_data}
        :param history: 截止当前时刻的历史数据 {code: dataframe}
        """
        pass

    def set_params(self, params: Dict[str, Any]):
        """设置策略参数"""
        self.params.update(params)

    def run_backtest(self, data: pd.DataFrame, code: str, initial_cash: float = 100000.0):
        """
        便捷方法：直接运行该策略的回测
        """
        # 使用绝对导入，避免 "attempted relative import beyond top-level package"
        from backtest.engine import BacktestEngine
        engine = BacktestEngine(initial_cash)
        return engine.run(self, data, code)
