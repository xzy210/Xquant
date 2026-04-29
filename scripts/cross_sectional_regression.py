# -*- coding: utf-8 -*-
"""Minimal regression for cross-sectional research -> backtest -> live contract."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.execution_contract import StrategySignal
from scripts.live_contract_test_support import create_backtest_live_gateway_factory
from strategy_app.backtest import BacktestConfig, UnifiedBacktestEngine


class RankingCrossSectionalStrategy:
    strategy_id = "cross_sectional_regression"
    prefer_generate_signals = True

    def __init__(self) -> None:
        self.done = False

    def initialize(self, context) -> None:
        pass

    def prepare_factors(self, data_dict: dict[str, pd.DataFrame]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for code, frame in data_dict.items():
            for _, row in frame.iterrows():
                rows.append(
                    {
                        "date": row["date"],
                        "code": code,
                        "score": float(row["close"]),
                    }
                )
        return pd.DataFrame(rows).set_index(["date", "code"])

    def on_rebalance(self, context, valid_codes, daily_factors) -> None:
        pass

    def generate_signals(self, data, context=None):
        if self.done or data["daily_factors"] is None or data["daily_factors"].empty:
            return []
        factors = data["daily_factors"].sort_values("score", ascending=False)
        symbol = str(factors.index[0])
        self.done = True
        return [
            StrategySignal(
                symbol=symbol,
                action="buy",
                strategy_id=self.strategy_id,
                strategy_name="Cross Sectional Regression",
                target_percent=0.5,
                price=float(data["prices"][symbol]),
                reason="top ranked cross-sectional signal",
            )
        ]


def build_fixture_data() -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2024-01-02", periods=6, freq="B")
    return {
        "000001": pd.DataFrame(
            {
                "date": dates,
                "open": [10.0, 10.1, 10.2, 10.1, 10.3, 10.4],
                "high": [10.2, 10.3, 10.4, 10.3, 10.5, 10.6],
                "low": [9.9, 10.0, 10.1, 10.0, 10.2, 10.3],
                "close": [10.1, 10.2, 10.3, 10.2, 10.4, 10.5],
                "volume": [100000, 101000, 102000, 103000, 104000, 105000],
            }
        ),
        "000002": pd.DataFrame(
            {
                "date": dates,
                "open": [20.0, 20.3, 20.5, 20.4, 20.8, 21.0],
                "high": [20.3, 20.6, 20.8, 20.7, 21.1, 21.3],
                "low": [19.8, 20.1, 20.3, 20.2, 20.6, 20.8],
                "close": [20.2, 20.5, 20.7, 20.6, 21.0, 21.2],
                "volume": [90000, 91000, 92000, 93000, 94000, 95000],
            }
        ),
    }


def assert_research_signal(data: dict[str, pd.DataFrame]) -> None:
    strategy = RankingCrossSectionalStrategy()
    factors = strategy.prepare_factors(data)
    first_date = min(frame["date"].min() for frame in data.values())
    daily_factors = factors.xs(first_date, level="date")
    prices = {code: float(frame.loc[frame["date"] == first_date, "close"].iloc[0]) for code, frame in data.items()}
    signals = strategy.generate_signals(
        {
            "date": first_date,
            "valid_codes": list(data.keys()),
            "valid_symbols": list(data.keys()),
            "prices": prices,
            "daily_factors": daily_factors,
        }
    )
    assert len(signals) == 1
    assert signals[0].symbol == "000002"
    assert signals[0].target_percent == 0.5


def run_backtest(data: dict[str, pd.DataFrame], *, live_contract: bool = False) -> dict:
    config = BacktestConfig(initial_cash=100000, mode="cross_sectional")
    if live_contract:
        config = BacktestConfig(
            initial_cash=100000,
            mode="cross_sectional",
            use_live_execution_gateway=True,
            live_execution_gateway_factory=create_backtest_live_gateway_factory(),
        )
    return UnifiedBacktestEngine(config).run(RankingCrossSectionalStrategy(), data, mode="cross_sectional")


def main() -> int:
    data = build_fixture_data()
    assert_research_signal(data)

    backtest_result = run_backtest(data)
    assert backtest_result["strategy_id"] == "cross_sectional_regression"
    assert len(backtest_result["trades"]) == 1
    assert backtest_result["execution_reports"][0].intent.intent_type == "target_percent"

    live_result = run_backtest(data, live_contract=True)
    summary = live_result["provenance"]["live_gateway_summary"]
    assert summary["checked_count"] == 1
    assert summary["accepted_count"] == 1
    assert summary["execution_modes"] == ["shadow"]
    assert len(live_result["trades"]) == len(backtest_result["trades"])

    print("Cross-sectional regression passed: research signal, backtest and live dry-run contract align.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
