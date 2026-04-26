from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4


BUY = "buy"
SELL = "sell"
HOLD = "hold"
TARGET_QUANTITY = "target_quantity"
TARGET_PERCENT = "target_percent"
MARKET = "market"
LIMIT = "limit"


@dataclass(frozen=True)
class StrategySignal:
    """Unified strategy output signal before it becomes an executable order."""

    symbol: str
    action: str
    signal_id: str = ""
    strategy_id: str = ""
    strategy_name: str = ""
    strength: float = 1.0
    target_quantity: Optional[int] = None
    target_percent: Optional[float] = None
    price: Optional[float] = None
    reason: str = ""
    timestamp: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "strategy_signal.v1"

    def __post_init__(self):
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "action", normalize_side(self.action, allow_hold=True))
        if not self.signal_id:
            object.__setattr__(self, "signal_id", uuid4().hex[:16])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_order_intent(
        self,
        *,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
        order_type: str = MARKET,
        intent_type: str = "quantity",
        source: str = "strategy",
        trigger: str = "auto",
    ) -> "OrderIntent":
        qty = quantity if quantity is not None else self.target_quantity
        intent_type_value = intent_type
        if self.target_percent is not None and quantity is None:
            intent_type_value = TARGET_PERCENT
        elif self.target_quantity is not None and quantity is None:
            intent_type_value = TARGET_QUANTITY
        return OrderIntent(
            symbol=self.symbol,
            side=self.action,
            quantity=int(qty or 0),
            price=self.price if price is None else price,
            order_type=order_type,
            intent_type=intent_type_value,
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            signal_id=self.signal_id,
            reason=self.reason,
            source=source,
            trigger=trigger,
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True)
class OrderIntent:
    """Unified executable order request shared by backtest and live execution."""

    symbol: str
    side: str
    quantity: int
    price: Optional[float] = None
    order_type: str = MARKET
    intent_type: str = "quantity"
    intent_id: str = ""
    strategy_id: str = ""
    strategy_name: str = ""
    virtual_account_id: str = ""
    signal_id: str = ""
    reason: str = ""
    source: str = "strategy"
    trigger: str = "auto"
    price_type: int = 5
    timestamp: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "order_intent.v1"

    def __post_init__(self):
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "side", normalize_side(self.side))
        object.__setattr__(self, "quantity", abs(int(self.quantity or 0)))
        if not self.intent_id:
            object.__setattr__(self, "intent_id", self.signal_id or uuid4().hex[:16])

    @property
    def signed_quantity(self) -> int:
        return self.quantity if self.side == BUY else -self.quantity

    @property
    def order_type_code(self) -> int:
        return 23 if self.side == BUY else 24

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_execution_request_kwargs(self, *, stock_name: str = "") -> Dict[str, Any]:
        return {
            "stock_code": self.symbol,
            "stock_name": stock_name or self.symbol,
            "order_type": self.order_type_code,
            "order_volume": self.quantity,
            "price_type": int(self.price_type or 5),
            "price": float(self.price or 0.0),
            "source": self.source,
            "trigger": self.trigger,
            "strategy_name": self.strategy_name,
            "strategy_id": self.strategy_id,
            "virtual_account_id": self.virtual_account_id,
            "intent_id": self.intent_id,
            "remark": self.reason,
            "metadata": {
                **dict(self.metadata),
                "schema_version": self.schema_version,
                "signal_id": self.signal_id,
                "intent_type": self.intent_type,
                "order_type": self.order_type,
            },
        }


@dataclass(frozen=True)
class TargetPortfolio:
    """Target portfolio weights produced by a strategy before order planning."""

    weights: Dict[str, float] = field(default_factory=dict)
    cash_weight: float = 0.0
    target_id: str = ""
    strategy_id: str = ""
    strategy_name: str = ""
    reason: str = ""
    timestamp: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "target_portfolio.v1"

    def __post_init__(self):
        normalized_weights = {
            normalize_symbol(symbol): max(float(weight or 0.0), 0.0)
            for symbol, weight in dict(self.weights or {}).items()
            if str(symbol or "").strip()
        }
        object.__setattr__(self, "weights", normalized_weights)
        object.__setattr__(self, "cash_weight", max(float(self.cash_weight or 0.0), 0.0))
        if not self.target_id:
            object.__setattr__(self, "target_id", uuid4().hex[:16])

    @classmethod
    def single_asset(
        cls,
        *,
        symbol: str,
        weight: float,
        strategy_id: str = "",
        strategy_name: str = "",
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "TargetPortfolio":
        target_weight = max(float(weight or 0.0), 0.0)
        return cls(
            weights={symbol: target_weight},
            cash_weight=max(0.0, 1.0 - target_weight),
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            reason=reason,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def cash_only(
        cls,
        *,
        strategy_id: str = "",
        strategy_name: str = "",
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "TargetPortfolio":
        return cls(
            weights={},
            cash_weight=1.0,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            reason=reason,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RebalanceIntent:
    """Portfolio-level rebalance plan with ordered executable intents."""

    target_portfolio: TargetPortfolio
    order_intents: tuple[OrderIntent, ...] = ()
    current_positions: Dict[str, int] = field(default_factory=dict)
    prices: Dict[str, float] = field(default_factory=dict)
    total_asset: float = 0.0
    available_cash: float = 0.0
    intent_id: str = ""
    reason: str = ""
    timestamp: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "rebalance_intent.v1"

    def __post_init__(self):
        if not self.intent_id:
            object.__setattr__(self, "intent_id", uuid4().hex[:16])
        object.__setattr__(
            self,
            "current_positions",
            {
                normalize_symbol(symbol): max(int(quantity or 0), 0)
                for symbol, quantity in dict(self.current_positions or {}).items()
                if str(symbol or "").strip()
            },
        )
        object.__setattr__(
            self,
            "prices",
            {
                normalize_symbol(symbol): float(price or 0.0)
                for symbol, price in dict(self.prices or {}).items()
                if str(symbol or "").strip()
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_portfolio": self.target_portfolio.to_dict(),
            "order_intents": [intent.to_dict() for intent in self.order_intents],
            "current_positions": dict(self.current_positions),
            "prices": dict(self.prices),
            "total_asset": float(self.total_asset or 0.0),
            "available_cash": float(self.available_cash or 0.0),
            "intent_id": self.intent_id,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
            "schema_version": self.schema_version,
        }


class PortfolioPlanner:
    """Translate target portfolio weights into ordered executable order intents."""

    def __init__(
        self,
        *,
        lot_size: int = 100,
        min_trade_amount: float = 0.0,
        order_type: str = MARKET,
        price_type: int = 5,
    ) -> None:
        self.lot_size = max(int(lot_size or 1), 1)
        self.min_trade_amount = max(float(min_trade_amount or 0.0), 0.0)
        self.order_type = order_type or MARKET
        self.price_type = int(price_type or 5)

    def plan(
        self,
        target_portfolio: TargetPortfolio,
        *,
        current_positions: Optional[Dict[str, int]] = None,
        prices: Optional[Dict[str, float]] = None,
        total_asset: float = 0.0,
        available_cash: float = 0.0,
        reason: str = "",
        source: str = "strategy",
        trigger: str = "auto",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RebalanceIntent:
        positions = self._normalize_positions(current_positions or {})
        price_map = self._normalize_prices(prices or {})
        total_value = max(float(total_asset or 0.0), 0.0)
        cash_budget = max(float(available_cash or 0.0), 0.0)
        base_metadata = {**dict(target_portfolio.metadata), **dict(metadata or {})}
        target_quantities: Dict[str, int] = {}
        for symbol, weight in target_portfolio.weights.items():
            price = float(price_map.get(symbol, 0.0) or 0.0)
            if price <= 0 or total_value <= 0:
                continue
            target_value = total_value * max(float(weight or 0.0), 0.0)
            target_quantities[symbol] = self._round_lot(int(target_value / price))

        order_specs = []
        symbols = sorted(set(positions) | set(target_quantities))
        for symbol in symbols:
            current_qty = int(positions.get(symbol, 0) or 0)
            target_qty = int(target_quantities.get(symbol, 0) or 0)
            diff = target_qty - current_qty
            price = float(price_map.get(symbol, 0.0) or 0.0)
            if diff == 0 or price <= 0:
                continue
            quantity = self._round_lot(abs(diff))
            if quantity <= 0:
                continue
            side = BUY if diff > 0 else SELL
            amount = quantity * price
            if side == BUY and self.min_trade_amount and amount < self.min_trade_amount:
                continue
            order_specs.append((0 if side == SELL else 1, symbol, side, quantity, price, current_qty, target_qty, diff))
            if side == SELL:
                cash_budget += amount

        intents: list[OrderIntent] = []
        for _, symbol, side, quantity, price, current_qty, target_qty, diff in sorted(order_specs, key=lambda item: (item[0], item[1])):
            if side == BUY:
                affordable_qty = self._round_lot(int(cash_budget / price)) if price > 0 else 0
                quantity = min(quantity, affordable_qty)
                if quantity <= 0:
                    continue
                amount = quantity * price
                if self.min_trade_amount and amount < self.min_trade_amount:
                    continue
                cash_budget = max(0.0, cash_budget - amount)
            intent_metadata = {
                **base_metadata,
                "target_portfolio_id": target_portfolio.target_id,
                "target_weight": float(target_portfolio.weights.get(symbol, 0.0) or 0.0),
                "current_quantity": current_qty,
                "target_quantity": target_qty,
                "planned_delta": diff,
                "planner": "PortfolioPlanner.v1",
            }
            intents.append(
                OrderIntent(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    order_type=self.order_type,
                    intent_type="target_portfolio",
                    strategy_id=target_portfolio.strategy_id,
                    strategy_name=target_portfolio.strategy_name,
                    virtual_account_id=str(base_metadata.get("virtual_account_id", "") or ""),
                    reason=reason or target_portfolio.reason,
                    source=source,
                    trigger=trigger,
                    price_type=self.price_type,
                    metadata=intent_metadata,
                )
            )

        return RebalanceIntent(
            target_portfolio=target_portfolio,
            order_intents=tuple(intents),
            current_positions=positions,
            prices=price_map,
            total_asset=total_value,
            available_cash=float(available_cash or 0.0),
            reason=reason or target_portfolio.reason,
            metadata={**base_metadata, "cash_after_planning": round(cash_budget, 6)},
        )

    def _round_lot(self, quantity: int) -> int:
        return max(int(quantity or 0), 0) // self.lot_size * self.lot_size

    @staticmethod
    def _normalize_positions(positions: Dict[str, int]) -> Dict[str, int]:
        return {
            normalize_symbol(symbol): max(int(quantity or 0), 0)
            for symbol, quantity in dict(positions or {}).items()
            if str(symbol or "").strip()
        }

    @staticmethod
    def _normalize_prices(prices: Dict[str, float]) -> Dict[str, float]:
        return {
            normalize_symbol(symbol): float(price or 0.0)
            for symbol, price in dict(prices or {}).items()
            if str(symbol or "").strip()
        }


@dataclass(frozen=True)
class FillReport:
    """Unified fill/trade result contract."""

    symbol: str
    side: str
    quantity: int
    price: float
    amount: float = 0.0
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    total_fee: float = 0.0
    trade_id: str = ""
    order_id: str = ""
    intent_id: str = ""
    strategy_id: str = ""
    virtual_account_id: str = ""
    source: str = ""
    trade_date: Optional[Any] = None
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "fill_report.v1"

    def __post_init__(self):
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "side", normalize_side(self.side))
        object.__setattr__(self, "quantity", abs(int(self.quantity or 0)))
        amount = self.amount if self.amount else float(self.price or 0.0) * abs(int(self.quantity or 0))
        object.__setattr__(self, "amount", round(float(amount or 0.0), 6))
        fee = self.total_fee if self.total_fee else float(self.commission or 0.0) + float(self.stamp_tax or 0.0) + float(self.transfer_fee or 0.0)
        object.__setattr__(self, "total_fee", round(float(fee or 0.0), 6))
        if not self.trade_id:
            object.__setattr__(self, "trade_id", uuid4().hex[:16])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_backtest_trade(cls, trade: Any, *, intent_id: str = "", strategy_id: str = "") -> "FillReport":
        action = getattr(trade, "action", "")
        return cls(
            symbol=getattr(trade, "symbol", ""),
            side=normalize_side(action),
            quantity=int(getattr(trade, "quantity", 0) or 0),
            price=float(getattr(trade, "price", 0.0) or 0.0),
            commission=float(getattr(trade, "commission", 0.0) or 0.0),
            stamp_tax=float(getattr(trade, "stamp_tax", 0.0) or 0.0),
            transfer_fee=float(getattr(trade, "transfer_fee", 0.0) or 0.0),
            total_fee=float(getattr(trade, "total_fee", 0.0) or 0.0),
            intent_id=intent_id,
            strategy_id=strategy_id,
            source="backtest",
            trade_date=getattr(trade, "date", None),
            reason=getattr(trade, "reason", ""),
            metadata={"cash_after": getattr(trade, "cash_after", 0.0), "blocked_reason": getattr(trade, "blocked_reason", "")},
        )

    @classmethod
    def from_live_trade_record(cls, record: Any) -> "FillReport":
        return cls(
            symbol=getattr(record, "stock_code", ""),
            side=getattr(record, "direction", ""),
            quantity=int(getattr(record, "volume", 0) or 0),
            price=float(getattr(record, "price", 0.0) or 0.0),
            amount=float(getattr(record, "amount", 0.0) or 0.0),
            commission=float(getattr(record, "commission", 0.0) or 0.0),
            stamp_tax=float(getattr(record, "stamp_tax", 0.0) or 0.0),
            transfer_fee=float(getattr(record, "transfer_fee", 0.0) or 0.0),
            trade_id=str(getattr(record, "trade_id", "") or ""),
            order_id=str(getattr(record, "broker_order_id", "") or ""),
            intent_id=str(getattr(record, "intent_id", "") or ""),
            strategy_id=str(getattr(record, "strategy_id", "") or ""),
            virtual_account_id=str(getattr(record, "virtual_account_id", "") or ""),
            source=str(getattr(record, "source", "") or ""),
            trade_date=getattr(record, "trade_date", None),
            reason=str(getattr(record, "remark", "") or ""),
        )


@dataclass(frozen=True)
class OrderExecutionReport:
    """Unified order lifecycle result for both backtest and live execution."""

    intent: Optional[OrderIntent]
    accepted: bool
    status: str
    message: str = ""
    order_id: str = ""
    request_id: str = ""
    execution_mode: str = "backtest"
    fills: tuple[FillReport, ...] = ()
    blocked_reason: str = ""
    submitted: bool = False
    filled: bool = False
    partial: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "order_execution_report.v1"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["intent"] = self.intent.to_dict() if self.intent is not None else None
        data["fills"] = [fill.to_dict() for fill in self.fills]
        return data

    @classmethod
    def from_live_execution_result(cls, result: Any, *, intent: Optional[OrderIntent] = None, fills: Optional[list[FillReport]] = None) -> "OrderExecutionReport":
        blocked = bool(getattr(result, "blocked", False))
        filled = bool(getattr(result, "filled_confirmed", False))
        submitted = bool(getattr(result, "live_submitted", False) or getattr(result, "submitted_only", False))
        status = str(getattr(result, "order_status", "") or "")
        if blocked:
            status = "blocked"
        elif filled:
            status = status or "filled"
        elif submitted:
            status = status or "submitted"
        elif not bool(getattr(result, "success", False)):
            status = status or "failed"
        return cls(
            intent=intent,
            accepted=bool(getattr(result, "success", False)),
            status=status,
            message=str(getattr(result, "message", "") or ""),
            order_id=str(getattr(result, "broker_order_id", "") or ""),
            request_id=str(getattr(result, "request_id", "") or ""),
            execution_mode=str(getattr(result, "execution_mode", "live") or "live"),
            fills=tuple(fills or ()),
            blocked_reason=str(getattr(result, "message", "") or "") if blocked else "",
            submitted=submitted,
            filled=filled,
            partial=status == "partial_fill",
            metadata={
                "trade_record_id": getattr(result, "trade_record_id", 0),
                "order_record_id": getattr(result, "order_record_id", 0),
                "shadow": getattr(result, "shadow", False),
            },
        )


def normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    if "." in value:
        return value
    return value


def normalize_side(side: str, *, allow_hold: bool = False) -> str:
    value = str(side or "").strip().lower()
    mapping = {
        "buy": BUY,
        "b": BUY,
        "long": BUY,
        "add": BUY,
        "23": BUY,
        "sell": SELL,
        "s": SELL,
        "short": SELL,
        "reduce": SELL,
        "24": SELL,
        "BUY": BUY,
        "SELL": SELL,
    }
    if allow_hold and value in {"hold", "watch", "reject", "none", ""}:
        return HOLD
    result = mapping.get(value, value)
    if result not in {BUY, SELL}:
        raise ValueError(f"Unsupported order side: {side}")
    return result


def utcnow_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
