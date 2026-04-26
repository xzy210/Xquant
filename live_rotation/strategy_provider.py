from __future__ import annotations

from typing import Protocol


class StrategyProvider(Protocol):
    def create_strategy(self, strategy_id: str, config):
        ...


class DefaultStrategyProvider:
    """Default provider backed by `strategy_app.strategies.create_strategy`."""

    def create_strategy(self, strategy_id: str, config):
        try:
            from strategy_app.strategies import create_strategy

            strategy = create_strategy(strategy_id, config.to_strategy_params())
            if strategy is not None:
                return strategy
        except Exception:
            pass

        if strategy_id == "etf_rotation":
            from strategies.etf_three_factor_momentum_strategy_fast import (
                ETFThreeFactorMomentumStrategyFast,
            )

            strategy = ETFThreeFactorMomentumStrategyFast()
            strategy.set_params(config.to_strategy_params())
            return strategy

        raise ValueError(f"未知策略: {strategy_id}")
