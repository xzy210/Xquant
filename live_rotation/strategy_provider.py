from __future__ import annotations

from typing import Protocol

from strategy_app.strategies import create_strategy


class StrategyProvider(Protocol):
    def create_strategy(self, strategy_id: str, config):
        ...


class DefaultStrategyProvider:
    """Default provider backed by `strategy_app.strategies.create_strategy`."""

    def create_strategy(self, strategy_id: str, config):
        params = config.to_params() if hasattr(config, "to_params") else config
        strategy = create_strategy(strategy_id, params=params)
        if strategy is None:
            raise ValueError(f"未知策略: {strategy_id}")
        return strategy
