from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4
from typing import Any, Dict, Iterable, Optional

import pandas as pd

from common.data_portal import MarketDataBundle
from .broker import SimulationBroker
from .context import Context

@dataclass(frozen=True)
class BacktestConfig:
    """Configuration for the unified backtest engine."""

    initial_cash: float = 100000.0
    mode: str = "auto"
    benchmark_code: Optional[str] = None
    schema_version: str = "unified_backtest_config.v1"


@dataclass(frozen=True)
class BacktestEvent:
    """One timestamp in the unified backtest timeline."""

    date: Any
    bars: Dict[str, Any]
    history: Dict[str, pd.DataFrame]
    prices: Dict[str, float]
    valid_symbols: list[str]
    daily_factors: Any = None
    primary_symbol: Optional[str] = None

    def to_strategy_payload(self, mode: str) -> dict:
        primary_symbol = self.primary_symbol if self.primary_symbol in self.valid_symbols else None
        primary_symbol = primary_symbol or (self.valid_symbols[0] if self.valid_symbols else None)
        return {
            "mode": mode,
            "date": self.date,
            "code": primary_symbol,
            "primary_symbol": primary_symbol,
            "bars": self.bars,
            "history": self.history,
            "history_slice": self.history.get(primary_symbol) if primary_symbol else None,
            "prices": self.prices,
            "valid_codes": self.valid_symbols,
            "valid_symbols": self.valid_symbols,
            "daily_factors": self.daily_factors,
        }


@dataclass
class BacktestResult:
    """Structured result returned by the unified backtest engine."""

    equity_curve: pd.DataFrame
    trades: list
    closed_trades: list
    execution_reports: list
    final_value: float
    metrics: Dict[str, float] = field(default_factory=dict)
    data_contract: Optional[dict] = None
    mode: str = "bar"
    context: Optional[Context] = None
    strategy_id: str = ""
    strategy_version: str = ""
    params_hash: str = ""
    data_version: str = ""
    engine_version: str = "unified_backtest_engine.v1"
    code_commit: str = ""
    run_id: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "unified_backtest_result.v2"

    def to_dict(self) -> dict:
        return {
            "equity_curve": self.equity_curve,
            "trades": self.trades,
            "closed_trades": self.closed_trades,
            "execution_reports": self.execution_reports,
            "final_value": self.final_value,
            "metrics": self.metrics,
            "data_contract": self.data_contract,
            "mode": self.mode,
            "context": self.context,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "params_hash": self.params_hash,
            "data_version": self.data_version,
            "engine_version": self.engine_version,
            "code_commit": self.code_commit,
            "run_id": self.run_id,
            "provenance": self.provenance,
            "serializable_result": self.to_serializable_dict(),
            "schema_version": self.schema_version,
        }

    def to_serializable_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "params_hash": self.params_hash,
            "data_version": self.data_version,
            "engine_version": self.engine_version,
            "code_commit": self.code_commit,
            "run_id": self.run_id,
            "mode": self.mode,
            "final_value": self.final_value,
            "metrics": self._to_jsonable(self.metrics),
            "data_contract": self._to_jsonable(self.data_contract),
            "provenance": self._to_jsonable(self.provenance),
            "equity_curve": self._to_jsonable(self.equity_curve.to_dict(orient="records")) if self.equity_curve is not None else [],
            "trades": [self._to_jsonable(item) for item in self.trades],
            "closed_trades": [self._to_jsonable(item) for item in self.closed_trades],
            "execution_reports": [self._to_jsonable(item) for item in self.execution_reports],
        }

    @classmethod
    def _to_jsonable(cls, value: Any) -> Any:
        if hasattr(value, "to_dict"):
            return cls._to_jsonable(value.to_dict())
        if hasattr(value, "__dataclass_fields__"):
            return {key: cls._to_jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
        if isinstance(value, dict):
            return {str(key): cls._to_jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._to_jsonable(item) for item in value]
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                pass
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()
            except Exception:
                pass
        return value


@dataclass
class PreparedBacktestData:
    """Normalized market data consumed by the unified event loop."""

    data: Dict[str, pd.DataFrame]
    primary_symbol: Optional[str] = None
    benchmark_code: Optional[str] = None
    contract_info: Optional[dict] = None
    source_bundle: Optional[MarketDataBundle] = None


class UnifiedBacktestEngine:
    """Unified event-driven backtest engine for single-symbol and cross-sectional strategies."""

    def __init__(self, config: Optional[BacktestConfig] = None, broker: Optional[SimulationBroker] = None):
        self.config = config or BacktestConfig()
        self.broker = broker

    def run(
        self,
        strategy,
        data: Any,
        *,
        code: str = "UNKNOWN",
        benchmark_code: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> dict:
        prepared = self._prepare_data(strategy, data, code=code, benchmark_code=benchmark_code)
        selected_mode = self._resolve_mode(strategy, mode or self.config.mode)
        context = Context(self.config.initial_cash, broker=self.broker)
        self._initialize_strategy(strategy, context, prepared)

        factor_data = self._prepare_factor_data(strategy, prepared, selected_mode)
        equity_rows: list[dict] = []

        for event in self._iter_events(prepared, factor_data=factor_data, mode=selected_mode):
            context.current_prices = dict(event.prices)
            context.before_trading_day(event.date, event.bars)

            if selected_mode == "cross_sectional":
                self._call_rebalance(strategy, context, event)
            else:
                self._call_on_bar(strategy, context, event)

            self._execute_generated_signals(strategy, context, event, selected_mode)
            equity_rows.append(self._build_equity_row(context, event))

        equity_curve = pd.DataFrame(equity_rows)
        final_value = float(equity_rows[-1]["total_asset"]) if equity_rows else float(self.config.initial_cash)
        provenance = self._build_provenance(strategy, prepared, selected_mode)
        result = BacktestResult(
            equity_curve=equity_curve,
            trades=context.trade_history,
            closed_trades=context.closed_trades,
            execution_reports=context.execution_reports,
            final_value=final_value,
            metrics=self._calculate_metrics(equity_curve),
            data_contract=prepared.contract_info,
            mode=selected_mode,
            context=context,
            strategy_id=provenance["strategy_id"],
            strategy_version=provenance["strategy_version"],
            params_hash=provenance["params_hash"],
            data_version=provenance["data_version"],
            engine_version=provenance["engine_version"],
            code_commit=provenance["code_commit"],
            run_id=provenance["run_id"],
            provenance=provenance,
        )
        result_dict = result.to_dict()
        if hasattr(strategy, "finalize_backtest_result"):
            customized_result = strategy.finalize_backtest_result(
                result_dict,
                context=context,
                prepared_data=prepared,
            )
            if customized_result is not None:
                result_dict = customized_result
        return result_dict

    @staticmethod
    def _initialize_strategy(strategy, context: Context, prepared: PreparedBacktestData) -> None:
        if hasattr(strategy, "initialize_backtest"):
            strategy.initialize_backtest(context, prepared)
            return
        strategy.initialize(context)

    def _build_provenance(self, strategy, prepared: PreparedBacktestData, mode: str) -> Dict[str, Any]:
        strategy_id = self._resolve_strategy_id(strategy)
        strategy_version = self._resolve_strategy_version(strategy)
        params_payload = self._resolve_strategy_params(strategy)
        data_contract = dict(prepared.contract_info or {})
        provenance = {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "params_hash": self._stable_hash(params_payload),
            "data_version": self._resolve_data_version(prepared),
            "engine_version": "unified_backtest_engine.v1",
            "code_commit": self._resolve_code_commit(),
            "run_id": uuid4().hex,
            "mode": mode,
            "strategy_class": f"{strategy.__class__.__module__}.{strategy.__class__.__name__}",
            "params": params_payload,
            "config": {
                "initial_cash": float(self.config.initial_cash or 0.0),
                "mode": self.config.mode,
                "benchmark_code": self.config.benchmark_code,
                "schema_version": self.config.schema_version,
            },
            "data_contract": data_contract,
        }
        return provenance

    @staticmethod
    def _resolve_strategy_id(strategy) -> str:
        strategy_id = str(getattr(strategy, "strategy_id", "") or "").strip()
        if strategy_id:
            return strategy_id
        spec = getattr(strategy, "spec", None)
        if spec is not None:
            return str(getattr(spec, "strategy_id", "") or "").strip()
        return strategy.__class__.__name__

    @staticmethod
    def _resolve_strategy_version(strategy) -> str:
        for attr in ("strategy_version", "version", "__version__"):
            value = str(getattr(strategy, attr, "") or "").strip()
            if value:
                return value
        spec = getattr(strategy, "spec", None)
        if spec is not None:
            metadata = getattr(spec, "metadata", {}) or {}
            for key in ("strategy_version", "version"):
                value = str(metadata.get(key, "") or "").strip()
                if value:
                    return value
        return "v1"

    @staticmethod
    def _resolve_strategy_params(strategy) -> dict:
        params = getattr(strategy, "params", None)
        if isinstance(params, dict):
            return dict(params)
        return {}

    @classmethod
    def _resolve_data_version(cls, prepared: PreparedBacktestData) -> str:
        contract = dict(prepared.contract_info or {})
        payload = {
            "contract": contract,
            "frames": {
                symbol: cls._frame_fingerprint(frame)
                for symbol, frame in sorted(prepared.data.items(), key=lambda item: item[0])
            },
        }
        return cls._stable_hash(payload)

    @staticmethod
    def _frame_fingerprint(frame: pd.DataFrame) -> dict:
        if frame is None or frame.empty:
            return {"rows": 0, "columns": []}
        dates = pd.to_datetime(frame["date"], errors="coerce") if "date" in frame.columns else pd.Series(dtype="datetime64[ns]")
        return {
            "rows": int(len(frame)),
            "columns": list(frame.columns),
            "first_date": dates.min().strftime("%Y-%m-%d") if not dates.dropna().empty else "",
            "latest_date": dates.max().strftime("%Y-%m-%d") if not dates.dropna().empty else "",
        }

    @staticmethod
    def _stable_hash(payload: Any) -> str:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _resolve_code_commit() -> str:
        env_commit = str(os.environ.get("GIT_COMMIT", "") or "").strip()
        if env_commit:
            return env_commit
        try:
            root = Path(__file__).resolve().parents[2]
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode == 0:
                return str(result.stdout or "").strip()
        except Exception:
            pass
        return "unknown"

    def _prepare_data(self, strategy, data: Any, *, code: str, benchmark_code: Optional[str]) -> PreparedBacktestData:
        if isinstance(data, MarketDataBundle):
            if hasattr(strategy, "on_data_bundle"):
                strategy.on_data_bundle(data)
            data_dict = {}
            for symbol, view in data.iter_views():
                if data.primary_symbol == symbol and hasattr(strategy, "prepare_data_view"):
                    frame = strategy.prepare_data_view(view)
                else:
                    frame = view.to_frame()
                data_dict[symbol] = self._normalize_frame(frame)
            return PreparedBacktestData(
                data=data_dict,
                primary_symbol=data.primary_symbol or (data.symbols[0] if data.symbols else code),
                benchmark_code=benchmark_code or data.benchmark_symbol,
                contract_info={
                    "schema_version": data.schema_version,
                    "symbols": data.symbols,
                    "primary_symbol": data.primary_symbol or (data.symbols[0] if data.symbols else code),
                    "benchmark_symbol": data.benchmark_symbol,
                },
                source_bundle=data,
            )

        if isinstance(data, pd.DataFrame):
            symbol = code if code and code != "UNKNOWN" else "UNKNOWN"
            return PreparedBacktestData(
                data={symbol: self._normalize_frame(data)},
                primary_symbol=symbol,
                benchmark_code=benchmark_code,
                contract_info={
                    "schema_version": "dataframe_input.v1",
                    "symbols": [symbol],
                    "primary_symbol": symbol,
                    "benchmark_symbol": benchmark_code,
                },
            )

        if isinstance(data, dict):
            normalized = {
                str(symbol): self._normalize_frame(frame)
                for symbol, frame in data.items()
                if frame is not None and not frame.empty
            }
            primary = code if code in normalized else (next(iter(normalized.keys())) if normalized else None)
            return PreparedBacktestData(
                data=normalized,
                primary_symbol=primary,
                benchmark_code=benchmark_code,
                contract_info={
                    "schema_version": "data_dict_input.v1",
                    "symbols": list(normalized.keys()),
                    "primary_symbol": primary,
                    "benchmark_symbol": benchmark_code,
                },
            )

        raise TypeError("Backtest data must be a DataFrame, dict[str, DataFrame], or MarketDataBundle")

    def _iter_events(self, prepared: PreparedBacktestData, *, factor_data: Any, mode: str) -> Iterable[BacktestEvent]:
        all_dates = sorted({date for frame in prepared.data.values() for date in frame["date"].tolist()})
        for current_date in all_dates:
            bars: Dict[str, Any] = {}
            history: Dict[str, pd.DataFrame] = {}
            prices: Dict[str, float] = {}
            valid_symbols: list[str] = []

            for symbol, frame in prepared.data.items():
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
                valid_symbols.append(symbol)

            if not valid_symbols:
                continue

            yield BacktestEvent(
                date=current_date,
                bars=bars,
                history=history,
                prices=prices,
                valid_symbols=valid_symbols,
                daily_factors=self._slice_daily_factors(factor_data, current_date) if mode == "cross_sectional" else None,
                primary_symbol=prepared.primary_symbol,
            )

    def _prepare_factor_data(self, strategy, prepared: PreparedBacktestData, mode: str) -> Any:
        if mode != "cross_sectional":
            return None
        if prepared.source_bundle is not None and hasattr(strategy, "prepare_factors_from_bundle"):
            return strategy.prepare_factors_from_bundle(prepared.source_bundle)
        if hasattr(strategy, "prepare_factors"):
            return strategy.prepare_factors({symbol: frame.copy() for symbol, frame in prepared.data.items()})
        return None

    @staticmethod
    def _slice_daily_factors(factor_data: Any, current_date: Any) -> Any:
        if isinstance(factor_data, pd.DataFrame) and "date" in factor_data.index.names:
            try:
                return factor_data.xs(current_date, level="date")
            except KeyError:
                return pd.DataFrame()
        return None

    @staticmethod
    def _call_on_bar(strategy, context: Context, event: BacktestEvent) -> None:
        strategy.on_bar(context, event.bars, event.history)

    @staticmethod
    def _call_rebalance(strategy, context: Context, event: BacktestEvent) -> None:
        if hasattr(strategy, "on_rebalance"):
            strategy.on_rebalance(context, event.valid_symbols, event.daily_factors)

    @staticmethod
    def _execute_generated_signals(strategy, context: Context, event: BacktestEvent, mode: str) -> None:
        if not hasattr(strategy, "generate_signals"):
            return
        signals = strategy.generate_signals(event.to_strategy_payload(mode), context=context)
        context.execute_signals(signals, source="backtest", trigger="strategy")

    @staticmethod
    def _build_equity_row(context: Context, event: BacktestEvent) -> dict:
        market_value = 0.0
        for symbol, pos in context.positions.items():
            price = event.prices.get(symbol, pos.last_price or pos.avg_price)
            pos.last_price = price
            market_value += int(pos.quantity or 0) * float(price or 0.0)
        total_asset = float(context.cash or 0.0) + market_value
        primary_close = next(iter(event.prices.values())) if event.prices else 0.0
        return {
            "date": event.date,
            "total_asset": total_asset,
            "cash": float(context.cash or 0.0),
            "market_value": market_value,
            "holdings_count": len(context.positions),
            "close": primary_close,
        }

    def _resolve_mode(self, strategy, mode: str) -> str:
        normalized = str(mode or "auto").strip().lower()
        if normalized in {"bar", "single", "single_symbol"}:
            return "bar"
        if normalized in {"cross", "cross_sectional", "section"}:
            return "cross_sectional"
        if getattr(strategy, "type", "") == "cross_sectional" or hasattr(strategy, "on_rebalance"):
            return "cross_sectional"
        return "bar"

    @staticmethod
    def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        result = frame.copy()
        if "date" not in result.columns:
            if "time" not in result.columns:
                raise ValueError("Backtest data frame must contain a date or time column")
            result["date"] = result["time"]
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        if "time" in result.columns:
            result["time"] = pd.to_datetime(result["time"], errors="coerce")
        result = result.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        for column in ["open", "high", "low", "close", "volume"]:
            if column in result.columns:
                result[column] = pd.to_numeric(result[column], errors="coerce")
        required = ["open", "high", "low", "close"]
        missing = [column for column in required if column not in result.columns]
        if missing:
            raise ValueError(f"Backtest data frame missing required columns: {missing}")
        return result.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

    def _calculate_metrics(self, equity_curve: pd.DataFrame) -> Dict[str, float]:
        if equity_curve is None or equity_curve.empty or "total_asset" not in equity_curve.columns:
            return {
                "total_return": 0.0,
                "annual_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
            }
        asset = pd.to_numeric(equity_curve["total_asset"], errors="coerce").dropna()
        if asset.empty:
            return {"total_return": 0.0, "annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}
        returns = asset.pct_change().dropna()
        total_return = asset.iloc[-1] / float(self.config.initial_cash or 1.0) - 1.0
        dates = pd.to_datetime(equity_curve["date"], errors="coerce").dropna()
        years = max((dates.iloc[-1] - dates.iloc[0]).days / 365.25, 1 / 252) if len(dates) >= 2 else 1 / 252
        annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1 else -1.0
        peak = asset.cummax()
        drawdown = asset / peak - 1.0
        sharpe = 0.0
        if not returns.empty and float(returns.std() or 0.0) > 0:
            sharpe = float(returns.mean() / returns.std() * (252 ** 0.5))
        return {
            "total_return": float(total_return),
            "annual_return": float(annual_return),
            "max_drawdown": float(drawdown.min() if not drawdown.empty else 0.0),
            "sharpe": float(sharpe),
        }
