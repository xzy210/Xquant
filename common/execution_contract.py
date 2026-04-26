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
