from __future__ import annotations

from typing import Any, Optional

from .broker import SimulationBroker
from .engine import BacktestConfig, UnifiedBacktestEngine


class CrossSectionalEngine:
    """Backward-compatible facade over the unified cross-sectional backtest mode."""

    def __init__(self, initial_cash: float = 1000000.0, broker: Optional[SimulationBroker] = None):
        self.initial_cash = initial_cash
        self.broker = broker
        self.config = BacktestConfig(initial_cash=float(initial_cash or 0.0), mode="cross_sectional")
        self.unified_engine = UnifiedBacktestEngine(self.config, broker=broker)

    def run(self, strategy, data_dict: Any, benchmark_code: str = None):
        return self.unified_engine.run(
            strategy,
            data_dict,
            benchmark_code=benchmark_code,
            mode="cross_sectional",
        )
