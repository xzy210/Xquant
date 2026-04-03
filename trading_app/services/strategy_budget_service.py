from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from common.io_utils import atomic_write_json

from .strategy_constants import (
    AI_STOCK_STRATEGY_ID,
    AI_STOCK_STRATEGY_NAME,
    AI_STOCK_VIRTUAL_ACCOUNT_ID,
    load_default_etf_rotation_profile,
    normalize_symbol_code,
)

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "strategy_budget_config.json"
_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "strategy_budget_state.json"


@dataclass
class StrategyBudgetConfig:
    strategy_id: str
    strategy_name: str = ""
    virtual_account_id: str = ""
    capital_limit: float = 0.0
    enabled: bool = True
    updated_at: str = ""

    def __post_init__(self) -> None:
        self.strategy_id = (self.strategy_id or "").strip()
        self.strategy_name = (self.strategy_name or "").strip()
        self.virtual_account_id = (self.virtual_account_id or "").strip()
        self.capital_limit = float(self.capital_limit or 0.0)
        if not self.updated_at:
            self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyBudgetConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class StrategyPositionState:
    symbol_code: str
    quantity: int = 0
    avg_cost: float = 0.0
    updated_at: str = ""

    def __post_init__(self) -> None:
        self.symbol_code = normalize_symbol_code(self.symbol_code)
        self.quantity = int(self.quantity or 0)
        self.avg_cost = float(self.avg_cost or 0.0)
        if not self.updated_at:
            self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyPositionState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class StrategyBudgetState:
    strategy_id: str
    strategy_name: str = ""
    virtual_account_id: str = ""
    capital_limit: float = 0.0
    cash_balance: float = 0.0
    reserved_cash: float = 0.0
    realized_pnl: float = 0.0
    positions: Dict[str, dict] = field(default_factory=dict)
    reservations: Dict[str, float] = field(default_factory=dict)
    runtime_state: Dict[str, object] = field(default_factory=dict)
    trade_history: List[dict] = field(default_factory=list)
    capital_ledger: List[dict] = field(default_factory=list)
    daily_equity: Dict[str, float] = field(default_factory=dict)
    order_records: List[dict] = field(default_factory=list)
    updated_at: str = ""

    def __post_init__(self) -> None:
        self.strategy_id = (self.strategy_id or "").strip()
        self.strategy_name = (self.strategy_name or "").strip()
        self.virtual_account_id = (self.virtual_account_id or "").strip()
        self.capital_limit = float(self.capital_limit or 0.0)
        self.cash_balance = float(self.cash_balance or 0.0)
        self.reserved_cash = float(self.reserved_cash or 0.0)
        self.realized_pnl = float(self.realized_pnl or 0.0)
        self.positions = {
            normalize_symbol_code(code): StrategyPositionState.from_dict(value).to_dict()
            for code, value in (self.positions or {}).items()
            if normalize_symbol_code(code)
        }
        self.reservations = {
            str(key): float(value or 0.0)
            for key, value in (self.reservations or {}).items()
            if str(key)
        }
        self.runtime_state = dict(self.runtime_state or {})
        self.trade_history = list(self.trade_history or [])
        self.capital_ledger = list(self.capital_ledger or [])
        self.daily_equity = {
            str(key): round(float(value or 0.0), 2)
            for key, value in (self.daily_equity or {}).items()
            if str(key)
        }
        self.order_records = list(self.order_records or [])
        if not self.updated_at:
            self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        return asdict(self)

    def get_positions(self) -> Dict[str, StrategyPositionState]:
        return {
            code: StrategyPositionState.from_dict(data)
            for code, data in (self.positions or {}).items()
        }

    def available_cash(self) -> float:
        return max(float(self.cash_balance or 0.0) - float(self.reserved_cash or 0.0), 0.0)

    def invested_market_value(self) -> float:
        return round(
            sum(max(pos.quantity, 0) * max(pos.avg_cost, 0.0) for pos in self.get_positions().values()),
            2,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyBudgetState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class StrategyBudgetService:
    def __init__(
        self,
        config_path: Optional[Path] = None,
        state_path: Optional[Path] = None,
    ):
        self.config_path = config_path or _CONFIG_PATH
        self.state_path = state_path or _STATE_PATH
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._configs: Dict[str, StrategyBudgetConfig] = {}
        self._states: Dict[str, StrategyBudgetState] = {}
        self._load()

    def _load(self) -> None:
        self._configs = self._load_configs()
        self._states = self._load_states()
        changed = self._ensure_default_configs()
        if changed or not self.config_path.exists():
            self._save_configs()
        if not self.state_path.exists():
            self._save_states()

    def _load_configs(self) -> Dict[str, StrategyBudgetConfig]:
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
        except Exception as exc:
            logger.warning("读取策略预算配置失败，使用空配置重建: %s", exc)
            return {}
        items = raw.get("strategies", {}) if isinstance(raw, dict) else {}
        if isinstance(items, list):
            iterable = items
        else:
            iterable = items.values()
        result: Dict[str, StrategyBudgetConfig] = {}
        for item in iterable:
            try:
                cfg = StrategyBudgetConfig.from_dict(item or {})
            except Exception:
                continue
            if cfg.strategy_id:
                result[cfg.strategy_id] = cfg
        return result

    def _load_states(self) -> Dict[str, StrategyBudgetState]:
        if not self.state_path.exists():
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
        except Exception as exc:
            logger.warning("读取策略预算状态失败，使用空状态重建: %s", exc)
            return {}
        items = raw.get("strategies", {}) if isinstance(raw, dict) else {}
        result: Dict[str, StrategyBudgetState] = {}
        if isinstance(items, list):
            iterable = items
        else:
            iterable = items.values()
        for item in iterable:
            try:
                state = StrategyBudgetState.from_dict(item or {})
            except Exception:
                continue
            if state.strategy_id:
                result[state.strategy_id] = state
        return result

    def _save_configs(self) -> None:
        payload = {
            "strategies": {
                strategy_id: cfg.to_dict()
                for strategy_id, cfg in sorted(self._configs.items(), key=lambda item: item[0])
            }
        }
        atomic_write_json(self.config_path, payload)

    def _save_states(self) -> None:
        payload = {
            "strategies": {
                strategy_id: state.to_dict()
                for strategy_id, state in sorted(self._states.items(), key=lambda item: item[0])
            }
        }
        atomic_write_json(self.state_path, payload)

    def _ensure_default_configs(self) -> bool:
        changed = False
        if AI_STOCK_STRATEGY_ID not in self._configs:
            self._configs[AI_STOCK_STRATEGY_ID] = StrategyBudgetConfig(
                strategy_id=AI_STOCK_STRATEGY_ID,
                strategy_name=AI_STOCK_STRATEGY_NAME,
                virtual_account_id=AI_STOCK_VIRTUAL_ACCOUNT_ID,
                capital_limit=0.0,
                enabled=True,
            )
            changed = True
        etf_strategy_id, etf_strategy_name, etf_virtual_account_id, _, etf_capital_limit = (
            load_default_etf_rotation_profile()
        )
        if etf_strategy_id not in self._configs:
            self._configs[etf_strategy_id] = StrategyBudgetConfig(
                strategy_id=etf_strategy_id,
                strategy_name=etf_strategy_name,
                virtual_account_id=etf_virtual_account_id,
                capital_limit=etf_capital_limit,
                enabled=True,
            )
            changed = True
        else:
            cfg = self._configs[etf_strategy_id]
            updated = False
            if not cfg.strategy_name:
                cfg.strategy_name = etf_strategy_name
                updated = True
            if not cfg.virtual_account_id:
                cfg.virtual_account_id = etf_virtual_account_id
                updated = True
            if cfg.capital_limit <= 0 and etf_capital_limit > 0:
                cfg.capital_limit = etf_capital_limit
                updated = True
            if updated:
                cfg.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                changed = True
        return changed

    def _effective_capital_limit(self, strategy_id: str, real_total_asset: float = 0.0) -> float:
        cfg = self._configs.get(strategy_id)
        if cfg is None:
            return 0.0
        explicit = float(cfg.capital_limit or 0.0)
        if explicit > 0:
            return explicit
        total_asset = float(real_total_asset or 0.0)
        if total_asset <= 0:
            return 0.0
        reserved_by_others = sum(
            max(float(item.capital_limit or 0.0), 0.0)
            for sid, item in self._configs.items()
            if sid != strategy_id and bool(item.enabled)
        )
        return max(total_asset - reserved_by_others, 0.0)

    def _ensure_strategy(
        self,
        strategy_id: str,
        *,
        strategy_name: str = "",
        virtual_account_id: str = "",
        real_total_asset: float = 0.0,
    ) -> StrategyBudgetState:
        strategy_id = (strategy_id or "").strip()
        if not strategy_id:
            raise ValueError("strategy_id 不能为空")

        cfg = self._configs.get(strategy_id)
        if cfg is None:
            cfg = StrategyBudgetConfig(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                virtual_account_id=virtual_account_id,
                capital_limit=0.0,
                enabled=True,
            )
            self._configs[strategy_id] = cfg
            self._save_configs()
        else:
            updated = False
            if strategy_name and not cfg.strategy_name:
                cfg.strategy_name = strategy_name
                updated = True
            if virtual_account_id and not cfg.virtual_account_id:
                cfg.virtual_account_id = virtual_account_id
                updated = True
            if updated:
                cfg.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save_configs()

        state = self._states.get(strategy_id)
        initial_capital = self._effective_capital_limit(strategy_id, real_total_asset=real_total_asset)
        if state is None:
            state = StrategyBudgetState(
                strategy_id=strategy_id,
                strategy_name=strategy_name or cfg.strategy_name,
                virtual_account_id=virtual_account_id or cfg.virtual_account_id,
                capital_limit=initial_capital,
                cash_balance=initial_capital,
                reserved_cash=0.0,
                realized_pnl=0.0,
            )
            self._states[strategy_id] = state
            self._save_states()
            return state

        updated = False
        if strategy_name and not state.strategy_name:
            state.strategy_name = strategy_name
            updated = True
        if virtual_account_id and not state.virtual_account_id:
            state.virtual_account_id = virtual_account_id
            updated = True
        if state.capital_limit <= 0 and initial_capital > 0 and not state.positions and state.cash_balance <= 0:
            state.capital_limit = initial_capital
            state.cash_balance = initial_capital
            updated = True
        if updated:
            state.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_states()
        return state

    def get_strategy_snapshot(
        self,
        strategy_id: str,
        *,
        strategy_name: str = "",
        virtual_account_id: str = "",
        real_total_asset: float = 0.0,
    ) -> dict:
        state = self._ensure_strategy(
            strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            real_total_asset=real_total_asset,
        )
        return {
            "strategy_id": state.strategy_id,
            "strategy_name": state.strategy_name,
            "virtual_account_id": state.virtual_account_id,
            "capital_limit": round(float(state.capital_limit or 0.0), 2),
            "cash_balance": round(float(state.cash_balance or 0.0), 2),
            "reserved_cash": round(float(state.reserved_cash or 0.0), 2),
            "available_cash": round(state.available_cash(), 2),
            "invested_market_value": round(state.invested_market_value(), 2),
            "position_count": len([pos for pos in state.get_positions().values() if pos.quantity > 0]),
            "realized_pnl": round(float(state.realized_pnl or 0.0), 2),
        }

    def upsert_strategy_config(
        self,
        *,
        strategy_id: str,
        strategy_name: str = "",
        virtual_account_id: str = "",
        capital_limit: Optional[float] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        strategy_id = (strategy_id or "").strip()
        if not strategy_id:
            return
        cfg = self._configs.get(strategy_id)
        if cfg is None:
            cfg = StrategyBudgetConfig(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                virtual_account_id=virtual_account_id,
                capital_limit=float(capital_limit or 0.0),
                enabled=True if enabled is None else bool(enabled),
            )
            self._configs[strategy_id] = cfg
            self._save_configs()
            return
        updated = False
        if strategy_name and cfg.strategy_name != strategy_name:
            cfg.strategy_name = strategy_name
            updated = True
        if virtual_account_id and cfg.virtual_account_id != virtual_account_id:
            cfg.virtual_account_id = virtual_account_id
            updated = True
        if capital_limit is not None and abs(float(cfg.capital_limit or 0.0) - float(capital_limit or 0.0)) > 1e-6:
            cfg.capital_limit = float(capital_limit or 0.0)
            updated = True
        if enabled is not None and cfg.enabled != bool(enabled):
            cfg.enabled = bool(enabled)
            updated = True
        if updated:
            cfg.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_configs()

    def reset_strategy_account(
        self,
        *,
        strategy_id: str,
        cash_balance: float,
        strategy_name: str = "",
        virtual_account_id: str = "",
        capital_limit: Optional[float] = None,
        preserve_positions: bool = True,
    ) -> None:
        state = self._ensure_strategy(
            strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            real_total_asset=0.0,
        )
        state.cash_balance = round(float(cash_balance or 0.0), 2)
        if capital_limit is not None:
            state.capital_limit = round(float(capital_limit or 0.0), 2)
        if not preserve_positions:
            state.positions = {}
        state.reservations = {}
        state.reserved_cash = 0.0
        state.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_states()

    def reserve_cash(
        self,
        *,
        strategy_id: str,
        intent_id: str,
        amount: float,
        strategy_name: str = "",
        virtual_account_id: str = "",
        real_total_asset: float = 0.0,
    ) -> Tuple[bool, str]:
        amount = round(float(amount or 0.0), 2)
        if amount <= 0:
            return False, "预算冻结金额必须大于0"
        intent_id = (intent_id or "").strip()
        if not intent_id:
            return False, "预算冻结缺少 intent_id"
        state = self._ensure_strategy(
            strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            real_total_asset=real_total_asset,
        )
        existing = float(state.reservations.get(intent_id, 0.0) or 0.0)
        if existing > 0:
            return True, ""
        available_cash = state.available_cash()
        if available_cash + 1e-6 < amount:
            return False, f"策略预算不足，需 {amount:,.2f}，可用 {available_cash:,.2f}"
        state.reservations[intent_id] = amount
        state.reserved_cash = round(float(state.reserved_cash or 0.0) + amount, 2)
        state.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_states()
        return True, ""

    def release_reservation(self, *, strategy_id: str, intent_id: str) -> None:
        state = self._states.get((strategy_id or "").strip())
        if state is None:
            return
        amount = float(state.reservations.pop((intent_id or "").strip(), 0.0) or 0.0)
        if amount <= 0:
            return
        state.reserved_cash = round(max(float(state.reserved_cash or 0.0) - amount, 0.0), 2)
        state.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_states()

    def commit_buy(
        self,
        *,
        strategy_id: str,
        symbol_code: str,
        price: float,
        volume: int,
        intent_id: str = "",
        strategy_name: str = "",
        virtual_account_id: str = "",
        real_total_asset: float = 0.0,
    ) -> None:
        price = float(price or 0.0)
        volume = int(volume or 0)
        if price <= 0 or volume <= 0:
            self.release_reservation(strategy_id=strategy_id, intent_id=intent_id)
            return
        state = self._ensure_strategy(
            strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            real_total_asset=real_total_asset,
        )
        reserved_amount = float(state.reservations.pop((intent_id or "").strip(), 0.0) or 0.0)
        if reserved_amount > 0:
            state.reserved_cash = round(max(float(state.reserved_cash or 0.0) - reserved_amount, 0.0), 2)

        trade_amount = round(price * volume, 2)
        state.cash_balance = round(max(float(state.cash_balance or 0.0) - trade_amount, 0.0), 2)

        code = normalize_symbol_code(symbol_code)
        position = state.get_positions().get(code) or StrategyPositionState(symbol_code=code)
        total_qty = int(position.quantity or 0) + volume
        total_cost = float(position.quantity or 0) * float(position.avg_cost or 0.0) + trade_amount
        position.quantity = total_qty
        position.avg_cost = round(total_cost / total_qty, 4) if total_qty > 0 else 0.0
        position.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state.positions[code] = position.to_dict()
        state.updated_at = position.updated_at
        self._save_states()

    def commit_sell(
        self,
        *,
        strategy_id: str,
        symbol_code: str,
        price: float,
        volume: int,
        strategy_name: str = "",
        virtual_account_id: str = "",
        real_total_asset: float = 0.0,
    ) -> None:
        price = float(price or 0.0)
        volume = int(volume or 0)
        if price <= 0 or volume <= 0:
            return
        state = self._ensure_strategy(
            strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            real_total_asset=real_total_asset,
        )
        proceeds = round(price * volume, 2)
        code = normalize_symbol_code(symbol_code)
        position = state.get_positions().get(code)
        if position:
            sold_qty = min(volume, max(position.quantity, 0))
            state.realized_pnl = round(
                float(state.realized_pnl or 0.0) + (price - position.avg_cost) * sold_qty,
                2,
            )
            remaining_qty = max(position.quantity - sold_qty, 0)
            if remaining_qty <= 0:
                state.positions.pop(code, None)
            else:
                position.quantity = remaining_qty
                position.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                state.positions[code] = position.to_dict()
        state.cash_balance = round(float(state.cash_balance or 0.0) + proceeds, 2)
        state.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_states()

    def sync_strategy_positions(
        self,
        *,
        strategy_id: str,
        positions: List[dict],
        strategy_name: str = "",
        virtual_account_id: str = "",
        real_total_asset: float = 0.0,
        clear_reservations: bool = True,
    ) -> None:
        state = self._ensure_strategy(
            strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            real_total_asset=real_total_asset,
        )
        new_positions: Dict[str, dict] = {}
        for item in positions or []:
            code = normalize_symbol_code(item.get("stock_code", ""))
            volume = int(item.get("volume", 0) or 0)
            if not code or volume <= 0:
                continue
            new_positions[code] = StrategyPositionState(
                symbol_code=code,
                quantity=volume,
                avg_cost=float(item.get("open_price", 0) or 0.0),
            ).to_dict()
        state.positions = new_positions
        if clear_reservations:
            state.reservations = {}
            state.reserved_cash = 0.0
        state.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_states()

    def list_strategy_snapshots(self) -> List[dict]:
        return [
            self.get_strategy_snapshot(strategy_id)
            for strategy_id in sorted(self._states.keys())
        ]

    def get_strategy_state_record(
        self,
        strategy_id: str,
        *,
        strategy_name: str = "",
        virtual_account_id: str = "",
        real_total_asset: float = 0.0,
    ) -> StrategyBudgetState:
        return self._ensure_strategy(
            strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            real_total_asset=real_total_asset,
        )

    def save_strategy_state_record(self, state: StrategyBudgetState) -> None:
        strategy_id = (getattr(state, "strategy_id", "") or "").strip()
        if not strategy_id:
            return
        state.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._states[strategy_id] = state
        self._save_states()


_strategy_budget_service: Optional[StrategyBudgetService] = None


def get_strategy_budget_service() -> StrategyBudgetService:
    global _strategy_budget_service
    if _strategy_budget_service is None:
        _strategy_budget_service = StrategyBudgetService()
    return _strategy_budget_service
