from .base_strategy import BaseStrategy
from .xgboost_cross_sectional_strategy import XGBoostCrossSectionalStrategy
from .etf_three_factor_momentum_strategy_fast import (
    ETFThreeFactorMomentumStrategyFast,
    ETFThreeFactorMomentumScreenerFast,
)

# Screeners
from .stock_screener import (
    StockScreener,
    ScreeningCriteria,
    StockScore,
    TechnicalIndicators,
    quick_screen,
)
from .etf_grid_strategy import (
    ETFGridStrategy,
    GridConfig,
    GridType,
    SignalType,
    GridLevel,
    TradeSignal,
    GridState,
    create_default_etf_config,
)

# Strategy registry kept intentionally small during cleanup.
STRATEGIES = {
    "xgboost_cross_sectional": XGBoostCrossSectionalStrategy,
    "etf_grid": ETFGridStrategy,
    "etf_three_factor_momentum": ETFThreeFactorMomentumStrategyFast,
}


def get_strategy(name: str) -> BaseStrategy:
    """Create a strategy instance by id."""
    if name in STRATEGIES:
        return STRATEGIES[name]()
    return None


def get_all_strategies() -> dict:
    """Return available strategy labels by strategy id."""
    result = {}
    for strategy_id, strategy_class in STRATEGIES.items():
        if strategy_id == "etf_grid":
            result[strategy_id] = "ETF网格回测"
        elif strategy_id == "etf_three_factor_momentum":
            result[strategy_id] = "ETF三因子动量轮动策略"
        else:
            result[strategy_id] = strategy_class().name
    return result


def create_strategy(strategy_id: str, params: dict = None) -> BaseStrategy:
    """
    Create and configure a strategy instance.
    """
    strategy_class = STRATEGIES.get(strategy_id)
    if not strategy_class:
        raise ValueError(f"未知策略: {strategy_id}")

    strategy = strategy_class()
    if params:
        strategy.set_params(params)
    return strategy
