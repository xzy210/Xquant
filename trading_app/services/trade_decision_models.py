from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TradeAction(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    REDUCE = "reduce"
    ADD = "add"


class TimeHorizon(Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


TRADE_ACTION_LABELS = {
    TradeAction.BUY.value: "买入",
    TradeAction.SELL.value: "卖出",
    TradeAction.HOLD.value: "持有",
    TradeAction.REDUCE.value: "减仓",
    TradeAction.ADD.value: "加仓",
}

TIME_HORIZON_LABELS = {
    TimeHorizon.SHORT.value: "短线(1-5日)",
    TimeHorizon.MEDIUM.value: "中线(1-4周)",
    TimeHorizon.LONG.value: "长线(1月+)",
}


@dataclass
class TradeDecision:
    action: str
    symbol_code: str
    symbol_name: str
    confidence: float = 0.0
    target_price: float = 0.0
    stop_loss_price: float = 0.0
    current_price: float = 0.0
    position_pct: float = 0.0
    risk_score: float = 0.5
    time_horizon: str = "short"
    invalidation: str = ""
    reasoning: str = ""
    bull_case: str = ""
    bear_case: str = ""

    @property
    def action_label(self) -> str:
        return TRADE_ACTION_LABELS.get(self.action, self.action)

    @property
    def horizon_label(self) -> str:
        return TIME_HORIZON_LABELS.get(self.time_horizon, self.time_horizon)

    @property
    def is_actionable(self) -> bool:
        return self.action in (TradeAction.BUY.value, TradeAction.SELL.value,
                               TradeAction.REDUCE.value, TradeAction.ADD.value)

    @property
    def expected_return_pct(self) -> Optional[float]:
        if self.current_price > 0 and self.target_price > 0:
            return round((self.target_price - self.current_price) / self.current_price * 100, 2)
        return None

    @property
    def max_loss_pct(self) -> Optional[float]:
        if self.current_price > 0 and self.stop_loss_price > 0:
            return round((self.stop_loss_price - self.current_price) / self.current_price * 100, 2)
        return None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TradeDecision:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class RiskCheckItem:
    name: str
    passed: bool
    level: str = "info"
    message: str = ""


@dataclass
class RiskCheckResult:
    passed: bool
    checks: List[RiskCheckItem] = field(default_factory=list)
    overall_risk_level: str = "low"
    warnings: List[str] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DecisionOutcome(Enum):
    INSPECTED = "inspected"
    APPROVED = "approved"
    REJECTED_BY_RISK = "rejected_by_risk"
    REJECTED_BY_USER = "rejected_by_user"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"
    EXPIRED = "expired"


@dataclass
class DecisionRecord:
    record_id: str
    created_at: str
    symbol_code: str
    symbol_name: str
    decision: Dict[str, Any] = field(default_factory=dict)
    risk_result: Dict[str, Any] = field(default_factory=dict)
    outcome: str = ""
    user_remark: str = ""
    broker_order_id: int = -1
    entry_price: float = 0.0
    exit_price: float = 0.0
    actual_pnl: float = 0.0
    actual_pnl_pct: float = 0.0
    closed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DecisionRecord:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})
