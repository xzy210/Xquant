from __future__ import annotations

from dataclasses import dataclass, field
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
    schema_version: str = "unified_backtest_result.v1"

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
            "schema_version": self.schema_version,
        }


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
        strategy.initialize(context)

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
        )
        return result.to_dict()

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
                    "schema_version": "legacy_dataframe.v1",
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
                    "schema_version": "legacy_data_dict.v1",
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
            raise ValueError("Backtest data frame must contain a date column")
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
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


class BacktestEngine:
    """Backward-compatible facade over UnifiedBacktestEngine."""

    def __init__(self, initial_cash=100000.0, broker: SimulationBroker = None, config: Optional[BacktestConfig] = None):
        self.initial_cash = initial_cash
        self.broker = broker
        self.config = config or BacktestConfig(initial_cash=float(initial_cash or 0.0), mode="bar")
        self.unified_engine = UnifiedBacktestEngine(self.config, broker=broker)

    def run(self, strategy, data: Any, code: str = "UNKNOWN"):
        return self.unified_engine.run(strategy, data, code=code, mode="bar")
