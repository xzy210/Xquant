from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ORDER_STATUS_LABELS = {
    48: "未报",
    49: "待报",
    50: "已报",
    51: "已报待撤",
    52: "部成待撤",
    53: "部撤",
    54: "已撤",
    55: "部成",
    56: "已成",
    57: "废单",
}
FINAL_REJECTED_STATUSES = {53, 54, 57}
FILLED_STATUSES = {55, 56}


@dataclass(frozen=True)
class OrderStateSnapshot:
    status_code: int
    status_text: str
    status_message: str
    traded_volume: int
    traded_price: float

    @property
    def has_trade(self) -> bool:
        return self.status_code in FILLED_STATUSES or self.traded_volume > 0

    @property
    def is_fully_filled(self) -> bool:
        return self.status_code == 56

    @property
    def is_partially_filled(self) -> bool:
        return self.has_trade and not self.is_fully_filled

    @property
    def is_rejected_terminal(self) -> bool:
        return self.status_code in FINAL_REJECTED_STATUSES

    @property
    def trade_record_status(self) -> str:
        if self.is_fully_filled:
            return "filled"
        if self.is_partially_filled:
            return "partial_fill"
        if self.is_rejected_terminal:
            return "rejected"
        return "submitted"

    @property
    def fill_event_type(self) -> str:
        return "OrderFilled" if self.is_fully_filled else "OrderPartiallyFilled"

    @property
    def fill_event_title(self) -> str:
        return "订单已成交" if self.is_fully_filled else "订单部分成交"


def normalize_order_state(order: Any) -> OrderStateSnapshot:
    status_code = _to_int(getattr(order, "order_status", 0))
    status_text = ORDER_STATUS_LABELS.get(status_code, str(status_code))
    return OrderStateSnapshot(
        status_code=status_code,
        status_text=status_text,
        status_message=str(getattr(order, "status_msg", "") or ""),
        traded_volume=_to_int(getattr(order, "traded_volume", 0)),
        traded_price=_to_float(getattr(order, "traded_price", 0)),
    )


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
