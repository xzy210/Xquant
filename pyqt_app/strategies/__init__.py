from .base_strategy import BaseStrategy
from .rebound_strategy import ContinuousDropReboundStrategy
from .etf_grid_strategy import (
    ETFGridStrategy,
    GridConfig,
    GridType,
    SignalType,
    GridLevel,
    TradeSignal,
    GridState,
    create_default_etf_config
)

# 策略注册表
STRATEGIES = {
    "continuous_drop_rebound": ContinuousDropReboundStrategy,
    "etf_grid": ETFGridStrategy,
}

def get_strategy(name: str) -> BaseStrategy:
    """工厂方法获取策略实例"""
    if name in STRATEGIES:
        return STRATEGIES[name]()
    return None

def get_all_strategies() -> dict:
    """获取所有可用策略 {id: name}"""
    result = {}
    for k, v in STRATEGIES.items():
        if k == "etf_grid":
            result[k] = "ETF网格交易"
        else:
            result[k] = v().name
    return result
