from .base_strategy import BaseStrategy
from .rebound_strategy import ContinuousDropReboundStrategy
from .double_ma_strategy import DoubleMAStrategy
from .ml_strategy import XGBoostStrategy
from .xgboost_cross_sectional_strategy import XGBoostCrossSectionalStrategy
from .volatility_breakout_strategy import VolatilityBreakoutStrategy
from .etf_three_factor_momentum_strategy_fast import (
    ETFThreeFactorMomentumStrategyFast,
    ETFThreeFactorMomentumScreenerFast
)

# Backward-compatible aliases
ETFThreeFactorMomentumStrategy = ETFThreeFactorMomentumStrategyFast
ETFThreeFactorMomentumScreener = ETFThreeFactorMomentumScreenerFast

# 选股器
from .stock_screener import (
    StockScreener,
    ScreeningCriteria,
    StockScore,
    TechnicalIndicators,
    quick_screen,
    screen_for_volatility_breakout
)
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
    "xgboost_ai": XGBoostStrategy,
    "double_ma": DoubleMAStrategy,
    "continuous_drop_rebound": ContinuousDropReboundStrategy,
    "xgboost_cross_sectional": XGBoostCrossSectionalStrategy,  # XGBoost截面选股策略
    "etf_grid": ETFGridStrategy,
    "volatility_breakout": VolatilityBreakoutStrategy,  # ATR波动率突破策略
    "etf_three_factor_momentum": ETFThreeFactorMomentumStrategyFast,  # ETF三因子动量轮动策略
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
        elif k == "etf_three_factor_momentum":
            result[k] = "ETF三因子动量轮动策略"
        else:
            result[k] = v().name
    return result


def create_strategy(strategy_id: str, params: dict = None) -> BaseStrategy:
    """
    创建策略实例并设置参数
    
    Args:
        strategy_id: 策略ID
        params: 策略参数字典
    
    Returns:
        配置好的策略实例
    """
    strategy_class = STRATEGIES.get(strategy_id)
    if not strategy_class:
        raise ValueError(f"未知策略: {strategy_id}")
    
    strategy = strategy_class()
    if params:
        strategy.set_params(params)
    return strategy
