from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from trading_app.services.order_state_machine import normalize_order_state


def _state(**kwargs):
    return normalize_order_state(SimpleNamespace(**kwargs))


def main() -> None:
    filled = _state(order_status=56, traded_volume=100, traded_price=12.3, status_msg="")
    assert filled.status_text == "已成"
    assert filled.has_trade
    assert filled.is_fully_filled
    assert filled.trade_record_status == "filled"
    assert filled.fill_event_type == "OrderFilled"

    partial = _state(order_status=55, traded_volume=50, traded_price=12.1, status_msg="partial")
    assert partial.status_text == "部成"
    assert partial.has_trade
    assert partial.is_partially_filled
    assert partial.trade_record_status == "partial_fill"
    assert partial.fill_event_type == "OrderPartiallyFilled"

    partial_cancelled = _state(order_status=53, traded_volume=20, traded_price=12.0, status_msg="partial cancelled")
    assert partial_cancelled.has_trade
    assert partial_cancelled.trade_record_status == "partial_fill"

    rejected = _state(order_status=57, traded_volume=0, traded_price=0, status_msg="rejected")
    assert rejected.status_text == "废单"
    assert not rejected.has_trade
    assert rejected.is_rejected_terminal
    assert rejected.trade_record_status == "rejected"

    unknown = _state(order_status=999, traded_volume=0, traded_price=0, status_msg="unknown")
    assert unknown.status_text == "999"
    assert unknown.trade_record_status == "submitted"

    print("order_state_machine_smoketest_ok")


if __name__ == "__main__":
    main()
