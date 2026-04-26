from .base_strategy import BaseStrategy
from trading_app.services.strategy_registry_service import get_strategy_registry_service
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

_registry = get_strategy_registry_service()
for _strategy_class in _STRATEGY_CLASSES:
    _registry.register_strategy_class(_strategy_class)


def normalize_strategy_id(strategy_id: str) -> str:
    """Normalize legacy strategy ids to their common StrategySpec id."""
    normalized = str(strategy_id or "").strip()
    return _registry.normalize_strategy_id(normalized)


def get_strategy(name: str) -> BaseStrategy:
    """Create a strategy instance by id."""
    try:
        return _registry.create_strategy(name)
    except ValueError:
        return None


def get_all_strategies() -> dict:
    """Return available strategy labels by common strategy id."""
    return _registry.get_strategy_labels(include_hidden=False, include_test=True)


def create_strategy(strategy_id: str, params: dict = None) -> BaseStrategy:
    """
    Create and configure a strategy instance.
    """
    return _registry.create_strategy(strategy_id, params=params)
