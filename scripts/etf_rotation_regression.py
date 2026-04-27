# -*- coding: utf-8 -*-
"""Regression check for ETF rotation signal entry convergence."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.execution_contract import StrategySignal
from strategy_app.backtest import BacktestConfig, UnifiedBacktestEngine
from strategy_app.backtest.context import Context
from strategy_app.strategies.etf_rotation_params import ETFRotationParams
from strategy_app.strategies.etf_three_factor_momentum_strategy_fast import ETFThreeFactorMomentumStrategyFast

FIXTURE_DIR = PROJECT_ROOT / "tests" / "regression" / "etf_rotation" / "fixtures"
EXPECTED_PATH = FIXTURE_DIR / "expected.json"
PARAMS_PATH = FIXTURE_DIR / "params.json"
DATA_VERSION_PATH = FIXTURE_DIR / "data_version.txt"
FLOAT_TOLERANCE = 1e-8


def _stable_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def normalize(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 8) if math.isfinite(value) else None
    if isinstance(value, (np.integer, np.floating)):
        return normalize(value.item())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "to_dict"):
        return normalize(value.to_dict())
    if hasattr(value, "__dataclass_fields__"):
        return {key: normalize(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, tuple):
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


def build_fixture_data(rows: int = 150) -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2024-01-02", periods=rows, freq="B")
    index = np.arange(rows)
    specs = {
        "510880": (2.50, 0.0012, 0.040, 0.0),
        "159949": (1.30, 0.0018, 0.035, 0.7),
        "513100": (1.05, -0.0002, 0.060, 1.4),
        "518880": (3.80, 0.0005, 0.025, 2.1),
    }
    frames: dict[str, pd.DataFrame] = {}
    for symbol, (base, drift, amp, phase) in specs.items():
        close = base + index * drift + np.sin(index / 9.0 + phase) * amp + np.cos(index / 17.0 + phase) * amp * 0.35
        close = np.maximum(close, 0.2)
        open_price = close * (1.0 + np.sin(index / 6.0 + phase) * 0.002)
        high = np.maximum(open_price, close) * 1.006
        low = np.minimum(open_price, close) * 0.994
        volume = 1_000_000 + index * 1234 + int(phase * 1000)
        frames[symbol] = pd.DataFrame(
            {
                "date": dates,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return frames


def load_params() -> ETFRotationParams:
    if PARAMS_PATH.exists():
        with open(PARAMS_PATH, "r", encoding="utf-8") as file:
            return ETFRotationParams.from_mapping(json.load(file))
    return ETFRotationParams(
        etf_pool=["510880", "159949", "513100", "518880"],
        rebalance_threshold=1.2,
        momentum_window=20,
        zscore_window=45,
        empty_threshold=-0.75,
        enable_empty_position=True,
        rebalance_period=3,
        enable_trailing_stop=True,
        trailing_stop_pct=0.12,
        enable_drawdown_protection=True,
        max_drawdown_pct=0.20,
        drawdown_cooldown_days=5,
    )


def signal_payload(signal: StrategySignal) -> dict[str, Any]:
    return normalize(
        {
            "symbol": signal.symbol,
            "action": signal.action,
            "strategy_id": signal.strategy_id,
            "strategy_name": signal.strategy_name,
            "target_quantity": signal.target_quantity,
            "target_percent": signal.target_percent,
            "price": signal.price,
            "reason": signal.reason,
            "timestamp": signal.timestamp,
            "metadata": signal.metadata,
        }
    )


def comparable_result(result: dict[str, Any], signals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    serializable = result.get("serializable_result", {})
    trades = normalize(serializable.get("trades", []))
    equity_curve = normalize(serializable.get("equity_curve", []))
    metrics = normalize(serializable.get("metrics", {}))
    return {
        "summary": normalize(
            {
                "final_value": serializable.get("final_value"),
                "metrics": metrics,
                "trade_count": len(trades),
                "first_trade": trades[0] if trades else {},
                "last_trade": trades[-1] if trades else {},
            }
        ),
        "trade_history_hash": _stable_hash(trades),
        "equity_curve_hash": _stable_hash(equity_curve),
        "generate_signals_hash": _stable_hash(signals or []),
    }


def run_unified_generate_path(params: ETFRotationParams, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    strategy = ETFThreeFactorMomentumStrategyFast(params)
    engine = UnifiedBacktestEngine(BacktestConfig(initial_cash=100000.0, mode="bar"))
    result = engine.run(strategy, {symbol: frame.copy() for symbol, frame in data.items()}, code="510880", mode="bar")
    return comparable_result(result)


def run_direct_generate_path(params: ETFRotationParams, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    strategy = ETFThreeFactorMomentumStrategyFast(params)
    context = Context(100000.0)
    strategy.initialize(context)
    captured_signals: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    dates = sorted({date for frame in data.values() for date in frame["date"].tolist()})

    for current_date in dates:
        bars = {}
        history = {}
        prices = {}
        for symbol, frame in data.items():
            history_slice = frame[frame["date"] <= current_date]
            if history_slice.empty:
                continue
            history[symbol] = history_slice
            day_data = history_slice[history_slice["date"] == current_date]
            if day_data.empty:
                continue
            row = day_data.iloc[-1]
            bars[symbol] = row
            prices[symbol] = float(row.get("close", 0.0) or 0.0)
        if not bars:
            continue
        context.current_prices = dict(prices)
        context.before_trading_day(current_date, bars)
        payload = {
            "mode": "bar",
            "date": current_date,
            "bars": bars,
            "history": history,
            "prices": prices,
            "valid_symbols": list(bars.keys()),
        }
        signals = strategy.generate_signals(payload, context=context)
        captured_signals.extend(signal_payload(signal) for signal in signals)
        context.execute_signals(signals, source="backtest", trigger="strategy")
        market_value = 0.0
        for symbol, pos in context.positions.items():
            price = prices.get(symbol, pos.last_price or pos.avg_price)
            pos.last_price = price
            market_value += int(pos.quantity or 0) * float(price or 0.0)
        equity_rows.append(
            {
                "date": current_date,
                "total_asset": float(context.cash or 0.0) + market_value,
                "cash": float(context.cash or 0.0),
                "market_value": market_value,
                "holdings_count": len(context.positions),
                "close": next(iter(prices.values())) if prices else 0.0,
            }
        )

    equity_curve = pd.DataFrame(equity_rows)
    final_value = float(equity_rows[-1]["total_asset"]) if equity_rows else 100000.0
    result = {
        "serializable_result": {
            "final_value": final_value,
            "metrics": UnifiedBacktestEngine(BacktestConfig(initial_cash=100000.0))._calculate_metrics(equity_curve),
            "trades": normalize(context.trade_history),
            "equity_curve": normalize(equity_rows),
        }
    }
    return comparable_result(result, captured_signals)


def run_direct_on_bar_path(params: ETFRotationParams, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    strategy = ETFThreeFactorMomentumStrategyFast(params)
    context = Context(100000.0)
    strategy.initialize(context)
    equity_rows: list[dict[str, Any]] = []
    dates = sorted({date for frame in data.values() for date in frame["date"].tolist()})

    for current_date in dates:
        bars = {}
        history = {}
        prices = {}
        for symbol, frame in data.items():
            history_slice = frame[frame["date"] <= current_date]
            if history_slice.empty:
                continue
            history[symbol] = history_slice
            day_data = history_slice[history_slice["date"] == current_date]
            if day_data.empty:
                continue
            row = day_data.iloc[-1]
            bars[symbol] = row
            prices[symbol] = float(row.get("close", 0.0) or 0.0)
        if not bars:
            continue
        context.current_prices = dict(prices)
        context.before_trading_day(current_date, bars)
        strategy.on_bar(context, bars, history)
        market_value = 0.0
        for symbol, pos in context.positions.items():
            price = prices.get(symbol, pos.last_price or pos.avg_price)
            pos.last_price = price
            market_value += int(pos.quantity or 0) * float(price or 0.0)
        equity_rows.append(
            {
                "date": current_date,
                "total_asset": float(context.cash or 0.0) + market_value,
                "cash": float(context.cash or 0.0),
                "market_value": market_value,
                "holdings_count": len(context.positions),
                "close": next(iter(prices.values())) if prices else 0.0,
            }
        )

    equity_curve = pd.DataFrame(equity_rows)
    final_value = float(equity_rows[-1]["total_asset"]) if equity_rows else 100000.0
    result = {
        "serializable_result": {
            "final_value": final_value,
            "metrics": UnifiedBacktestEngine(BacktestConfig(initial_cash=100000.0))._calculate_metrics(equity_curve),
            "trades": normalize(context.trade_history),
            "equity_curve": normalize(equity_rows),
        }
    }
    return comparable_result(result)


def load_expected() -> dict[str, Any]:
    if not EXPECTED_PATH.exists():
        return {}
    with open(EXPECTED_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def main() -> int:
    params = load_params()
    data = build_fixture_data()
    data_version = _stable_hash({symbol: normalize(frame.to_dict(orient="records")) for symbol, frame in data.items()})
    if DATA_VERSION_PATH.exists():
        expected_version = DATA_VERSION_PATH.read_text(encoding="utf-8").strip()
        if expected_version and expected_version != data_version:
            raise AssertionError(f"data_version mismatch: {data_version} != {expected_version}")

    unified_payload = run_unified_generate_path(params, data)
    direct_generate_payload = run_direct_generate_path(params, data)
    direct_on_bar_payload = run_direct_on_bar_path(params, data)

    assert_close(unified_payload["summary"], direct_generate_payload["summary"], "unified_vs_direct_generate.summary")
    assert_close(unified_payload["trade_history_hash"], direct_generate_payload["trade_history_hash"], "unified_vs_direct_generate.trade_history_hash")
    assert_close(unified_payload["equity_curve_hash"], direct_generate_payload["equity_curve_hash"], "unified_vs_direct_generate.equity_curve_hash")
    assert_close(direct_generate_payload["summary"], direct_on_bar_payload["summary"], "direct_generate_vs_on_bar.summary")
    assert_close(direct_generate_payload["trade_history_hash"], direct_on_bar_payload["trade_history_hash"], "direct_generate_vs_on_bar.trade_history_hash")
    assert_close(direct_generate_payload["equity_curve_hash"], direct_on_bar_payload["equity_curve_hash"], "direct_generate_vs_on_bar.equity_curve_hash")

    payload = {
        "schema_version": "etf_rotation_regression.v1",
        "data_version": data_version,
        **unified_payload,
        "generate_signals_hash": direct_generate_payload["generate_signals_hash"],
    }
    expected = load_expected()
    if expected:
        assert_close(payload, expected, "expected")
    print("ETF Rotation regression passed: generate_signals path matches on_bar bypass and expected baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
