from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from typing import Any, Callable, Dict, Iterable, Optional

import pandas as pd

from common.data_portal import MarketDataBundle
from common.events.event_bus import BacktestEvent, EventBus
from .broker import SimulationBroker
from .context import Context

@dataclass(frozen=True)
class BacktestConfig:
    """Configuration for the unified backtest engine."""

    initial_cash: float = 100000.0
    mode: str = "auto"
    benchmark_code: Optional[str] = None
    use_live_risk: bool = False
    use_live_budget: bool = False
    use_live_execution_gateway: bool = False
    schema_version: str = "unified_backtest_config.v1"


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


class _BacktestAutoTradeConfigService:
    def __init__(self, mode: str):
        from trading_app.services.auto_trade_config_service import AutoTradeConfig

        self._config = AutoTradeConfig(
            manual_orders_enabled=True,
            auto_trade_mode=mode,
            require_trading_time=False,
            duplicate_window_seconds=1,
            status_poll_seconds=0.01,
            status_poll_interval_seconds=0.001,
        )

    def get_config(self):
        return self._config


class _BacktestStrategyRegistry:
    def __init__(self) -> None:
        self.claimed: Dict[str, Any] = {}

    def validate_or_claim(
        self,
        symbol_code: str,
        *,
        strategy_id: str,
        strategy_name: str = "",
        virtual_account_id: str = "",
        owner_type: str = "other",
        auto_claim: bool = True,
    ):
        code = _plain_code(symbol_code)
        if not code or not strategy_id:
            return False, "策略归属校验缺少 symbol_code 或 strategy_id", None
        owner = self.claimed.get(code)
        if owner is not None and getattr(owner, "strategy_id", "") != strategy_id:
            return False, f"{code} 已归属于 {getattr(owner, 'strategy_name', '') or getattr(owner, 'strategy_id', '')}，当前策略无权操作", owner
        if owner is None:
            owner = SimpleNamespace(
                symbol_code=code,
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                virtual_account_id=virtual_account_id,
                owner_type=owner_type,
                enabled=True,
            )
            self.claimed[code] = owner
        return True, "", owner


class _BacktestStrategyBudget:
    def __init__(self, initial_cash: float, *, enabled: bool = True):
        self.enabled = bool(enabled)
        self.initial_cash = float(initial_cash or 0.0) if self.enabled else 1_000_000_000_000.0
        self._states: Dict[str, dict] = {}

    def _state(self, strategy_id: str, *, strategy_name: str = "", virtual_account_id: str = "", real_total_asset: float = 0.0) -> dict:
        sid = str(strategy_id or "").strip()
        base_cash = float(real_total_asset or self.initial_cash or 0.0) if self.enabled else self.initial_cash
        state = self._states.setdefault(
            sid,
            {
                "strategy_id": sid,
                "strategy_name": strategy_name,
                "virtual_account_id": virtual_account_id,
                "capital_limit": base_cash,
                "cash_balance": base_cash,
                "reserved_cash": 0.0,
                "positions": {},
                "reservations": {},
                "realized_pnl": 0.0,
            },
        )
        if strategy_name and not state.get("strategy_name"):
            state["strategy_name"] = strategy_name
        if virtual_account_id and not state.get("virtual_account_id"):
            state["virtual_account_id"] = virtual_account_id
        return state

    def get_strategy_snapshot(self, strategy_id: str, *, strategy_name: str = "", virtual_account_id: str = "", real_total_asset: float = 0.0) -> dict:
        state = self._state(strategy_id, strategy_name=strategy_name, virtual_account_id=virtual_account_id, real_total_asset=real_total_asset)
        invested_market_value = sum(float(pos.get("quantity", 0) or 0) * float(pos.get("avg_cost", 0.0) or 0.0) for pos in state["positions"].values())
        available_cash = max(float(state["cash_balance"] or 0.0) - float(state["reserved_cash"] or 0.0), 0.0)
        return {
            "strategy_id": state["strategy_id"],
            "strategy_name": state["strategy_name"],
            "virtual_account_id": state["virtual_account_id"],
            "capital_limit": round(float(state["capital_limit"] or 0.0), 2),
            "cash_balance": round(float(state["cash_balance"] or 0.0), 2),
            "reserved_cash": round(float(state["reserved_cash"] or 0.0), 2),
            "available_cash": round(available_cash, 2),
            "invested_market_value": round(invested_market_value, 2),
            "position_count": len([pos for pos in state["positions"].values() if int(pos.get("quantity", 0) or 0) > 0]),
            "realized_pnl": round(float(state["realized_pnl"] or 0.0), 2),
            "enabled": True,
            "is_test": True,
            "hidden": True,
            "is_unmanaged": False,
        }

    def reserve_cash(self, *, strategy_id: str, intent_id: str, amount: float, strategy_name: str = "", virtual_account_id: str = "", real_total_asset: float = 0.0):
        state = self._state(strategy_id, strategy_name=strategy_name, virtual_account_id=virtual_account_id, real_total_asset=real_total_asset)
        key = str(intent_id or "").strip()
        amount = round(float(amount or 0.0), 2)
        if amount <= 0:
            return True, ""
        if float(state["reservations"].get(key, 0.0) or 0.0) > 0:
            return True, ""
        available = max(float(state["cash_balance"] or 0.0) - float(state["reserved_cash"] or 0.0), 0.0)
        if available + 1e-6 < amount:
            return False, f"策略预算不足，需 {amount:,.2f}，可用 {available:,.2f}"
        state["reservations"][key] = amount
        state["reserved_cash"] = round(float(state["reserved_cash"] or 0.0) + amount, 2)
        return True, ""

    def release_reservation(self, *, strategy_id: str, intent_id: str) -> None:
        state = self._states.get(str(strategy_id or "").strip())
        if state is None:
            return
        amount = float(state["reservations"].pop(str(intent_id or "").strip(), 0.0) or 0.0)
        state["reserved_cash"] = round(max(float(state["reserved_cash"] or 0.0) - amount, 0.0), 2)

    def commit_buy(self, *, strategy_id: str, symbol_code: str, price: float, volume: int, intent_id: str = "", strategy_name: str = "", virtual_account_id: str = "", real_total_asset: float = 0.0, commission: float = 0.0, stamp_tax: float = 0.0, transfer_fee: float = 0.0) -> None:
        state = self._state(strategy_id, strategy_name=strategy_name, virtual_account_id=virtual_account_id, real_total_asset=real_total_asset)
        self.release_reservation(strategy_id=strategy_id, intent_id=intent_id)
        amount = round(float(price or 0.0) * int(volume or 0), 2)
        total_fee = round(float(commission or 0.0) + float(stamp_tax or 0.0) + float(transfer_fee or 0.0), 2)
        state["cash_balance"] = round(max(float(state["cash_balance"] or 0.0) - amount - total_fee, 0.0), 2)
        code = _plain_code(symbol_code)
        pos = state["positions"].setdefault(code, {"quantity": 0, "avg_cost": 0.0})
        old_qty = int(pos.get("quantity", 0) or 0)
        new_qty = old_qty + int(volume or 0)
        if new_qty > 0:
            old_cost = old_qty * float(pos.get("avg_cost", 0.0) or 0.0)
            pos["avg_cost"] = round((old_cost + amount) / new_qty, 4)
            pos["quantity"] = new_qty

    def commit_sell(self, *, strategy_id: str, symbol_code: str, price: float, volume: int, strategy_name: str = "", virtual_account_id: str = "", real_total_asset: float = 0.0, commission: float = 0.0, stamp_tax: float = 0.0, transfer_fee: float = 0.0) -> None:
        state = self._state(strategy_id, strategy_name=strategy_name, virtual_account_id=virtual_account_id, real_total_asset=real_total_asset)
        amount = round(float(price or 0.0) * int(volume or 0), 2)
        total_fee = round(float(commission or 0.0) + float(stamp_tax or 0.0) + float(transfer_fee or 0.0), 2)
        state["cash_balance"] = round(float(state["cash_balance"] or 0.0) + amount - total_fee, 2)
        code = _plain_code(symbol_code)
        pos = state["positions"].setdefault(code, {"quantity": 0, "avg_cost": 0.0})
        sell_qty = min(int(volume or 0), int(pos.get("quantity", 0) or 0))
        pos["quantity"] = max(int(pos.get("quantity", 0) or 0) - sell_qty, 0)
        state["realized_pnl"] = round(float(state["realized_pnl"] or 0.0) + (float(price or 0.0) - float(pos.get("avg_cost", 0.0) or 0.0)) * sell_qty - total_fee, 2)


class _BacktestTradeRecordService:
    def __init__(self):
        self.orders: Dict[str, Any] = {}
        self.records: Dict[int, Any] = {}
        self._next_order_id = 1
        self._next_record_id = 1

    def add_order_record(self, **fields):
        request_id = str(fields.get("request_id", "") or "")
        order = SimpleNamespace(id=self._next_order_id, **fields)
        self._next_order_id += 1
        self.orders[request_id] = order
        return order

    def update_order_record(self, request_id: str, **fields) -> bool:
        order = self.orders.get(str(request_id or ""))
        if order is None:
            return False
        for key, value in fields.items():
            setattr(order, key, value)
        return True

    @staticmethod
    def normalize_stock_name(stock_code: str, fallback: str = "") -> str:
        return str(fallback or stock_code or "").strip()

    def find_recent_order_record(self, fingerprint: str, within_seconds: int = 30):
        return None

    def sync_from_orders(self, orders, **kwargs) -> None:
        source = str(kwargs.get("source", "") or "")
        strategy_id = str(kwargs.get("strategy_id", "") or "")
        virtual_account_id = str(kwargs.get("virtual_account_id", "") or "")
        intent_id = str(kwargs.get("intent_id", "") or "")
        for order in orders or []:
            record = SimpleNamespace(
                id=self._next_record_id,
                trade_id=str(self._next_record_id),
                stock_code=getattr(order, "stock_code", ""),
                direction="buy" if int(getattr(order, "order_type", 23) or 23) == 23 else "sell",
                volume=int(getattr(order, "traded_volume", 0) or 0),
                price=float(getattr(order, "traded_price", 0.0) or 0.0),
                amount=float(getattr(order, "traded_volume", 0) or 0) * float(getattr(order, "traded_price", 0.0) or 0.0),
                commission=0.0,
                stamp_tax=0.0,
                transfer_fee=0.0,
                broker_order_id=int(getattr(order, "order_id", 0) or 0),
                intent_id=intent_id,
                strategy_id=strategy_id,
                virtual_account_id=virtual_account_id,
                source=source,
                trade_date=getattr(order, "traded_time", None) or getattr(order, "order_time", None),
                remark=str(getattr(order, "remark", "") or ""),
            )
            self.records[record.id] = record
            self._next_record_id += 1

    def get_record_by_id(self, record_id: int):
        return self.records.get(int(record_id or 0))

    def get_latest_record_by_broker_order_id(self, broker_order_id: int):
        target = int(broker_order_id or 0)
        matches = [record for record in self.records.values() if int(getattr(record, "broker_order_id", 0) or 0) == target]
        return matches[-1] if matches else None

    @staticmethod
    def estimate_trade_fees(direction: str, amount: float, stock_code: str = "") -> dict:
        return {"commission": 0.0, "stamp_tax": 0.0, "transfer_fee": 0.0, "total_fee": 0.0}


class _BacktestEventStorage:
    def __init__(self) -> None:
        self.events = []

    def add_event(self, event) -> None:
        self.events.append(event)

    def query_open_orders(self):
        return {}


def _plain_code(code: str) -> str:
    value = str(code or "").strip().upper()
    return value.split(".")[0] if "." in value else value


class UnifiedBacktestEngine:
    """Unified event-driven backtest engine for single-symbol and cross-sectional strategies."""

    def __init__(
        self,
        config: Optional[BacktestConfig] = None,
        broker: Optional[SimulationBroker] = None,
        bus: Optional[EventBus] = None,
    ):
        self.config = config or BacktestConfig()
        self.broker = broker
        self.bus = bus or EventBus()
        self._live_execution_gateway = None
        self._live_gateway_reports: list = []
        self._progress_callback: Optional[Callable[[int, int, str], None]] = None
        self._log_callback: Optional[Callable[[str], None]] = None
        self._run_id: str = ""

    def run(
        self,
        strategy,
        data: Any,
        *,
        code: str = "UNKNOWN",
        benchmark_code: Optional[str] = None,
        mode: Optional[str] = None,
        progress_callback: Optional[Callable[..., None]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> dict:
        self._progress_callback = progress_callback
        self._log_callback = log_callback
        self._run_id = uuid4().hex
        prepared = self._prepare_data(strategy, data, code=code, benchmark_code=benchmark_code)
        selected_mode = self._resolve_mode(strategy, mode or self.config.mode)
        context = Context(self.config.initial_cash, broker=self.broker)
        self._live_gateway_reports = []
        self._live_execution_gateway = self._build_live_execution_gateway(context) if self._uses_live_gateway_checks() else None
        self._publish_lifecycle_event(
            "run_started",
            selected_mode,
            message="Backtest run started",
            payload={"symbols": list(prepared.data.keys()), "code": code},
        )
        self._initialize_strategy(strategy, context, prepared)

        factor_data = self._prepare_factor_data(strategy, prepared, selected_mode)
        equity_rows: list[dict] = []
        events = list(self._iter_events(prepared, factor_data=factor_data, mode=selected_mode))
        total_events = len(events)

        for index, event in enumerate(events, start=1):
            event = replace(
                event,
                progress_current=index,
                progress_total=total_events,
                mode=selected_mode,
                run_id=self._run_id,
            )
            context.current_prices = dict(event.prices)
            context.before_trading_day(event.date, event.bars)

            if selected_mode == "cross_sectional":
                rebalance_event = replace(event, event_type="on_rebalance", message="Backtest rebalance event")
                self._publish_event(rebalance_event)
                self._call_rebalance(strategy, context, event)
            else:
                bar_event = replace(event, event_type="on_bar", message="Backtest bar event")
                self._publish_event(bar_event)
                if not getattr(strategy, "prefer_generate_signals", False):
                    self._call_on_bar(strategy, context, event)

            self._execute_generated_signals(strategy, context, event, selected_mode)
            equity_rows.append(self._build_equity_row(context, event))
            self._publish_event(replace(event, event_type="progress", message="Backtest progress updated"))

        equity_curve = pd.DataFrame(equity_rows)
        final_value = float(equity_rows[-1]["total_asset"]) if equity_rows else float(self.config.initial_cash)
        c3_summary = self._build_live_gateway_summary()
        provenance = self._build_provenance(strategy, prepared, selected_mode)
        provenance["run_id"] = self._run_id
        provenance["live_gateway_summary"] = c3_summary
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
        self._publish_lifecycle_event(
            "run_completed",
            selected_mode,
            message="Backtest run completed",
            payload={"final_value": final_value, "metrics": dict(result.metrics or {})},
        )
        return result_dict

    @staticmethod
    def _initialize_strategy(strategy, context: Context, prepared: PreparedBacktestData) -> None:
        if hasattr(strategy, "initialize_backtest"):
            strategy.initialize_backtest(context, prepared)
            return
        strategy.initialize(context)

    def _publish_lifecycle_event(self, event_type: str, mode: str, *, message: str = "", payload: Optional[dict] = None) -> None:
        self._publish_event(BacktestEvent(
            date=None,
            bars={},
            history={},
            prices={},
            valid_symbols=[],
            event_type=event_type,
            message=message,
            mode=mode,
            run_id=self._run_id,
            payload=payload or {},
        ))

    def _publish_event(self, event: BacktestEvent) -> None:
        self.bus.publish(event)
        self._dispatch_legacy_callbacks(event)

    def _dispatch_legacy_callbacks(self, event: BacktestEvent) -> None:
        if self._log_callback and event.event_type in {"run_started", "run_completed", "on_bar", "on_rebalance"}:
            message = event.message or event.event_type
            if event.date is not None:
                message = f"{message}: {event.date}"
            self._safe_log_callback(message)
        if (
            self._progress_callback
            and event.event_type == "progress"
            and event.progress_current is not None
            and event.progress_total is not None
        ):
            self._safe_progress_callback(event.progress_current, event.progress_total, str(event.date or event.message or ""))

    def _safe_log_callback(self, message: str) -> None:
        try:
            self._log_callback(message)
        except Exception:
            pass

    def _safe_progress_callback(self, current: int, total: int, message: str) -> None:
        try:
            self._progress_callback(current, total, message)
        except TypeError:
            try:
                self._progress_callback(current, total)
            except Exception:
                pass
        except Exception:
            pass

    def _build_provenance(self, strategy, prepared: PreparedBacktestData, mode: str) -> Dict[str, Any]:
        strategy_id = self._resolve_strategy_id(strategy)
        strategy_version = self._resolve_strategy_version(strategy)
        params_payload = self._resolve_strategy_params(strategy)
        data_contract = dict(prepared.contract_info or {})
        data_audit = self._resolve_data_audit(prepared)
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
            "data_audit": data_audit,
            "config": {
                "initial_cash": float(self.config.initial_cash or 0.0),
                "mode": self.config.mode,
                "benchmark_code": self.config.benchmark_code,
                "use_live_risk": bool(self.config.use_live_risk),
                "use_live_budget": bool(self.config.use_live_budget),
                "use_live_execution_gateway": bool(self.config.use_live_execution_gateway),
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
        data_audit = cls._resolve_data_audit(prepared)
        audit_version = str(data_audit.get("data_version", "") or "").strip()
        if audit_version:
            return audit_version
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
    def _resolve_data_audit(prepared: PreparedBacktestData) -> dict:
        if prepared.source_bundle is not None:
            audit = getattr(prepared.source_bundle, "data_audit", None)
            if isinstance(audit, dict):
                return dict(audit)
        contract = dict(prepared.contract_info or {})
        audit = contract.get("data_audit")
        return dict(audit) if isinstance(audit, dict) else {}

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
                    "data_audit": dict(data.data_audit or {}),
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

    def _execute_generated_signals(self, strategy, context: Context, event: BacktestEvent, mode: str) -> None:
        if not hasattr(strategy, "generate_signals"):
            return
        signals = list(strategy.generate_signals(event.to_strategy_payload(mode), context=context) or [])
        signals = self._filter_signals_through_live_gateway(signals, context)
        context.execute_signals(signals, source="backtest", trigger="strategy")

    def _uses_live_gateway_checks(self) -> bool:
        return bool(
            self.config.use_live_risk
            or self.config.use_live_budget
            or self.config.use_live_execution_gateway
        )

    def _build_live_execution_gateway(self, context: Context):
        from trading_app.services.trade_execution_service import TradeExecutionService

        gateway = TradeExecutionService(broker=context.broker)
        gateway.trade_service = _BacktestTradeRecordService()
        gateway.config_service = _BacktestAutoTradeConfigService("shadow" if self.config.use_live_execution_gateway else "paper")
        gateway.strategy_registry = _BacktestStrategyRegistry()
        gateway.strategy_budget = _BacktestStrategyBudget(self.config.initial_cash, enabled=self.config.use_live_budget)
        gateway._event_storage = _BacktestEventStorage()
        gateway._validate_market_data_status = lambda _request: ""
        if not self.config.use_live_risk:
            gateway._validate_strategy_risk_policy = lambda _request: ""
        return gateway

    def _filter_signals_through_live_gateway(self, signals: list, context: Context) -> list:
        if self._live_execution_gateway is None:
            return signals
        self._sync_context_to_gateway(context)
        passed = []
        for signal in signals:
            if signal is None or getattr(signal, "action", "") == "hold":
                continue
            report = self._live_execution_gateway.execute_signal(signal)
            if report is None:
                continue
            self._live_gateway_reports.append(report)
            if report.accepted:
                passed.append(signal)
        self._sync_context_to_gateway(context)
        return passed

    def _sync_context_to_gateway(self, context: Context) -> None:
        try:
            context._sync_broker_snapshot()
        except Exception:
            pass

    def _build_live_gateway_summary(self) -> dict:
        reports = list(self._live_gateway_reports or [])
        blocked = [report for report in reports if not bool(getattr(report, "accepted", False))]
        return {
            "enabled": self._uses_live_gateway_checks(),
            "use_live_risk": bool(self.config.use_live_risk),
            "use_live_budget": bool(self.config.use_live_budget),
            "use_live_execution_gateway": bool(self.config.use_live_execution_gateway),
            "checked_count": len(reports),
            "accepted_count": len(reports) - len(blocked),
            "blocked_count": len(blocked),
            "blocked_reasons": [str(getattr(report, "message", "") or getattr(report, "blocked_reason", "") or "") for report in blocked],
        }

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
