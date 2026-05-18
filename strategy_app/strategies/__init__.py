from importlib import import_module
from typing import Any

from .base_strategy import BaseStrategy

_STRATEGY_CLASS_IMPORTS = {
    "xgboost_cross_sectional": (
        ".xgboost_cross_sectional_strategy",
        "XGBoostCrossSectionalStrategy",
    ),
    "etf_grid": (".etf_grid_strategy", "ETFGridStrategy"),
    "etf_rotation": (
        ".etf_three_factor_momentum_strategy_fast",
        "ETFThreeFactorMomentumStrategyFast",
    ),
    "tcn_attention_timing": (
        ".tcn_attention_timing_strategy",
        "TCNAttentionTimingStrategy",
    ),
}
_STRATEGY_ALIASES = {
    "etf_three_factor_momentum": "etf_rotation",
}
_REGISTERED_STRATEGY_IDS: set[str] = set()

_LAZY_ATTR_IMPORTS = {
    "AIStockStrategyParams": (".ai_stock_strategy_params", "AIStockStrategyParams"),
    "ETFRotationParams": (".etf_rotation_params", "ETFRotationParams"),
    "XGBoostCrossSectionalStrategy": (
        ".xgboost_cross_sectional_strategy",
        "XGBoostCrossSectionalStrategy",
    ),
    "ETFThreeFactorMomentumStrategyFast": (
        ".etf_three_factor_momentum_strategy_fast",
        "ETFThreeFactorMomentumStrategyFast",
    ),
    "ETFThreeFactorMomentumScreenerFast": (
        ".etf_three_factor_momentum_strategy_fast",
        "ETFThreeFactorMomentumScreenerFast",
    ),
    "ETFGridStrategy": (".etf_grid_strategy", "ETFGridStrategy"),
    "GridConfig": (".etf_grid_strategy", "GridConfig"),
    "GridType": (".etf_grid_strategy", "GridType"),
    "SignalType": (".etf_grid_strategy", "SignalType"),
    "GridLevel": (".etf_grid_strategy", "GridLevel"),
    "TradeSignal": (".etf_grid_strategy", "TradeSignal"),
    "GridState": (".etf_grid_strategy", "GridState"),
    "create_default_etf_config": (".etf_grid_strategy", "create_default_etf_config"),
    "TCNAttentionTimingStrategy": (
        ".tcn_attention_timing_strategy",
        "TCNAttentionTimingStrategy",
    ),
}
_STRATEGY_ID_BY_CLASS_ATTR = {
    class_name: strategy_id
    for strategy_id, (_module_name, class_name) in _STRATEGY_CLASS_IMPORTS.items()
}


def _registry():
    from common.strategy_registry_service import get_strategy_registry_service

    return get_strategy_registry_service()


def _normalize_known_strategy_id(strategy_id: str) -> str:
    normalized = str(strategy_id or "").strip()
    return _STRATEGY_ALIASES.get(normalized, normalized)


def _load_attr(module_name: str, attr_name: str) -> Any:
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[attr_name] = value
    return value


def _register_strategy_class(strategy_id: str) -> None:
    normalized = _normalize_known_strategy_id(strategy_id)
    if normalized in _REGISTERED_STRATEGY_IDS:
        return
    target = _STRATEGY_CLASS_IMPORTS.get(normalized)
    if target is None:
        return
    strategy_class = _load_attr(*target)
    registry = _registry()
    registry.register_strategy_class(strategy_class)
    for alias, canonical in _STRATEGY_ALIASES.items():
        if canonical == normalized:
            registry.register_strategy_alias(alias, normalized)
    _REGISTERED_STRATEGY_IDS.add(normalized)


def _register_all_strategy_classes() -> None:
    for strategy_id in _STRATEGY_CLASS_IMPORTS:
        _register_strategy_class(strategy_id)


def normalize_strategy_id(strategy_id: str) -> str:
    """Normalize legacy strategy ids to their common StrategySpec id."""
    normalized = _normalize_known_strategy_id(strategy_id)
    _register_strategy_class(normalized)
    return _registry().normalize_strategy_id(normalized)


def get_strategy(name: str) -> BaseStrategy:
    """Create a strategy instance by id."""
    try:
        return create_strategy(name)
    except ValueError:
        return None


def get_all_strategies() -> dict:
    """Return available strategy labels by common strategy id."""
    _register_all_strategy_classes()
    return _registry().get_strategy_labels(include_hidden=False, include_test=True)


def create_strategy(strategy_id: str, params: dict = None) -> BaseStrategy:
    """
    Create and configure a strategy instance.
    """
    normalized = _normalize_known_strategy_id(strategy_id)
    _register_strategy_class(normalized)
    return _registry().create_strategy(normalized, params=params)


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTR_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    strategy_id = _STRATEGY_ID_BY_CLASS_ATTR.get(name)
    if strategy_id is not None:
        _register_strategy_class(strategy_id)
        return globals()[name]
    return _load_attr(*target)


__all__ = [
    "AIStockStrategyParams",
    "ETFRotationParams",
    "BaseStrategy",
    "XGBoostCrossSectionalStrategy",
    "ETFThreeFactorMomentumStrategyFast",
    "ETFThreeFactorMomentumScreenerFast",
    "ETFGridStrategy",
    "GridConfig",
    "GridType",
    "SignalType",
    "GridLevel",
    "TradeSignal",
    "GridState",
    "create_default_etf_config",
    "TCNAttentionTimingStrategy",
    "create_strategy",
    "get_all_strategies",
    "get_strategy",
    "normalize_strategy_id",
]
