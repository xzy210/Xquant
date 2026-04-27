# -*- coding: utf-8 -*-
"""Regression check for the migrated ETF Grid native runner."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.perspectives.etf_grid import ETFGridParams, run_etf_grid_backtest
from strategy_app.strategies.etf_grid_strategy import ETFGridStrategy


FLOAT_TOLERANCE = 1e-8


def build_fixture_data(rows: int = 180) -> pd.DataFrame:
    index = np.arange(rows)
    base = 3.0 + np.sin(index / 8.0) * 0.08 + index * 0.0008
    close = base + np.cos(index / 13.0) * 0.03
    open_price = close + np.sin(index / 5.0) * 0.01
    high = np.maximum(open_price, close) + 0.02
    low = np.minimum(open_price, close) - 0.02
    volume = 1_000_000 + index * 1000
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=rows, freq="D"),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def normalize(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 8) if math.isfinite(value) else None
    if isinstance(value, (np.integer, np.floating)):
        return normalize(value.item())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def assert_close(left: Any, right: Any, path: str) -> None:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not math.isclose(float(left), float(right), rel_tol=FLOAT_TOLERANCE, abs_tol=FLOAT_TOLERANCE):
            raise AssertionError(f"{path}: {left!r} != {right!r}")
        return
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            raise AssertionError(f"{path}: keys differ {set(left) ^ set(right)}")
        for key in sorted(left):
            assert_close(left[key], right[key], f"{path}.{key}")
        return
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            raise AssertionError(f"{path}: length {len(left)} != {len(right)}")
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            assert_close(left_item, right_item, f"{path}[{index}]")
        return
    if left != right:
        raise AssertionError(f"{path}: {left!r} != {right!r}")


def comparable_payload(result: dict[str, Any]) -> dict[str, Any]:
    trades = result.get("trade_history", []) or []
    stats = result.get("daily_stats", []) or []
    return normalize(
        {
            "summary": result.get("summary", {}),
            "config": result.get("config", {}),
            "trade_count": len(trades),
            "first_trade": trades[0] if trades else {},
            "last_trade": trades[-1] if trades else {},
            "daily_stats_count": len(stats),
            "first_daily_stat": stats[0] if stats else {},
            "last_daily_stat": stats[-1] if stats else {},
        }
    )


def main() -> int:
    params = ETFGridParams(
        initial_capital=100000,
        grid_count=8,
        grid_spacing_pct=2.0,
        grid_type="geometric",
        position_per_grid_pct=10.0,
        max_position_ratio_pct=80.0,
        use_atr_adaptive=True,
        atr_period=14,
        atr_multiplier=1.5,
        stop_loss_ratio_pct=15.0,
        take_profit_ratio_pct=30.0,
        rebalance_threshold_pct=10.0,
    )
    data = build_fixture_data()

    legacy_strategy = ETFGridStrategy(params.to_grid_config())
    legacy_result = legacy_strategy.run_backtest(data.copy())
    legacy_payload = comparable_payload(legacy_result)

    native_result = run_etf_grid_backtest(params, data.copy(), code="ETF_GRID")
    native_payload = comparable_payload(native_result)

    assert_close(legacy_payload, native_payload, "etf_grid")
    print("ETF Grid regression passed: native runner matches legacy strategy output fields.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
