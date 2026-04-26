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

_STRATEGY_CLASSES = (
    XGBoostCrossSectionalStrategy,
    ETFGridStrategy,
    ETFThreeFactorMomentumStrategyFast,
)

STRATEGY_ID_ALIASES = {
    "etf_three_factor_momentum": ETFThreeFactorMomentumStrategyFast.spec.strategy_id,
}


# Strategy registry is keyed by each strategy class's common StrategySpec id.
STRATEGIES = {strategy_class.spec.strategy_id: strategy_class for strategy_class in _STRATEGY_CLASSES}


def normalize_strategy_id(strategy_id: str) -> str:
    """Normalize legacy strategy ids to their common StrategySpec id."""
    normalized = str(strategy_id or "").strip()
    return STRATEGY_ID_ALIASES.get(normalized, normalized)


def get_strategy(name: str) -> BaseStrategy:
    """Create a strategy instance by id."""
    strategy_class = STRATEGIES.get(normalize_strategy_id(name))
    if strategy_class:
        return strategy_class()
    return None


def get_all_strategies() -> dict:
    """Return available strategy labels by common strategy id."""
    return {strategy_id: strategy_class.spec.strategy_name for strategy_id, strategy_class in STRATEGIES.items()}


def create_strategy(strategy_id: str, params: dict = None) -> BaseStrategy:
    """
    Create and configure a strategy instance.
    """
    normalized_id = normalize_strategy_id(strategy_id)
    strategy_class = STRATEGIES.get(normalized_id)
    if not strategy_class:
        raise ValueError(f"未知策略: {strategy_id}")

    strategy = strategy_class()
    if params:
        strategy.set_params(params)
    return strategy
