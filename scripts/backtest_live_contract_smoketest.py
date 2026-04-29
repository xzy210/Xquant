# -*- coding: utf-8 -*-
"""Smoke tests for BacktestConfig live contract switches."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.execution_contract import StrategySignal
from scripts.live_contract_test_support import create_backtest_live_gateway_factory
from strategy_app.backtest import BacktestConfig, UnifiedBacktestEngine


class OneShotSignalStrategy:
    prefer_generate_signals = True

    def __init__(self, *, quantity: int = 100, strategy_id: str = "live_contract_smoke") -> None:
        self.quantity = int(quantity)
        self.strategy_id = strategy_id
        self.done = False

    def initialize(self, context) -> None:
        pass

    def generate_signals(self, data, context=None):
        if self.done:
            return []
        self.done = True
        code = data["code"]
        price = float(data["bars"][code]["close"])
        return [
            StrategySignal(
                symbol=code,
                action="buy",
                strategy_id=self.strategy_id,
                strategy_name="Live Contract Smoke",
                target_quantity=self.quantity,
                price=price,
                reason="live contract switch smoke",
            )
        ]


def build_fixture_data() -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    return {
        "000001": pd.DataFrame(
            {
                "date": dates,
                "open": [10.0, 10.2, 10.1],
                "high": [10.4, 10.5, 10.3],
                "low": [9.9, 10.0, 9.8],
                "close": [10.0, 10.2, 10.1],
                "volume": [100000, 110000, 105000],
            }
        )
    }


def assert_blocked(result: dict, expected_fragment: str, label: str) -> None:
    summary = result["provenance"]["live_gateway_summary"]
    assert summary["enabled"], label
    assert summary["checked_count"] == 1, label
    assert summary["blocked_count"] == 1, label
    assert len(result["trades"]) == 0, label
    reason = summary["blocked_reasons"][0]
    assert expected_fragment in reason, f"{label}: {reason}"


def test_use_live_risk() -> None:
    result = UnifiedBacktestEngine(
        BacktestConfig(
            initial_cash=10000,
            mode="bar",
            use_live_risk=True,
            live_execution_gateway_factory=create_backtest_live_gateway_factory(
                broker_cash=10000,
                broker_total_asset=10000,
                risk_config={"max_single_position_pct": 0.05},
            ),
        )
    ).run(OneShotSignalStrategy(quantity=100), build_fixture_data(), code="000001", mode="bar")
    assert_blocked(result, "单笔仓位", "use_live_risk should reuse live risk rejection")


def test_use_live_budget() -> None:
    result = UnifiedBacktestEngine(
        BacktestConfig(
            initial_cash=1000,
            mode="bar",
            use_live_budget=True,
            live_execution_gateway_factory=create_backtest_live_gateway_factory(
                broker_cash=1_000_000,
                broker_total_asset=1000,
                risk_config={"max_single_position_pct": 1000.0, "max_total_position_pct": 1000.0},
            ),
        )
    ).run(OneShotSignalStrategy(quantity=10000), build_fixture_data(), code="000001", mode="bar")
    assert_blocked(result, "策略预算不足", "use_live_budget should reuse strategy budget rejection")


def test_use_live_execution_gateway() -> None:
    result = UnifiedBacktestEngine(
        BacktestConfig(
            initial_cash=10000,
            mode="bar",
            use_live_execution_gateway=True,
            live_execution_gateway_factory=create_backtest_live_gateway_factory(),
        )
    ).run(OneShotSignalStrategy(quantity=100), build_fixture_data(), code="000001", mode="bar")
    summary = result["provenance"]["live_gateway_summary"]
    assert summary["enabled"]
    assert summary["checked_count"] == 1
    assert summary["accepted_count"] == 1
    assert summary["blocked_count"] == 0
    assert summary["execution_modes"] == ["shadow"]
    assert summary["shadow_count"] == 1
    assert len(result["trades"]) == 1


def main() -> int:
    test_use_live_risk()
    test_use_live_budget()
    test_use_live_execution_gateway()
    print("Backtest live contract smoketest passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
