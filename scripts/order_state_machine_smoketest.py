from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from trading_app.services.auto_trade_config_service import AutoTradeConfig
from trading_app.services.live_strategy_center.alert_event_service import AlertEventService
from trading_app.services.live_strategy_center.models import LiveCenterEvent
from trading_app.services.live_strategy_center.storage import LiveStrategyCenterStorage
from trading_app.services.order_execution_event_service import OrderExecutionEvent, OrderExecutionEventService
from trading_app.services.order_state_machine import (
    OrderLifecycle,
    OrderLifecycleEvent,
    OrderLifecycleState,
    OrderStateTransitionError,
    normalize_order_state,
    rebuild_order_lifecycle,
)
from trading_app.services.trade_execution_service import ExecutionRequest, TradeExecutionService

class _MemoryEventStorage:
    def __init__(self) -> None:
        self.events = []

    def add_event(self, event) -> None:
        self.events.append(event)


class _PollingFakeBroker:
    is_connected = True

    def __init__(self) -> None:
        self.order_id = 880000 + int(time.time() * 1000) % 100000
        self.ordered = False
        self.query_count = 0

    def query_stock_asset(self):
        return SimpleNamespace(cash=500_000.0, available_cash=500_000.0, total_asset=1_000_000.0)

    def query_stock_positions(self):
        return []

    def order_stock(self, **kwargs):
        self.ordered = True
        self.order_kwargs = dict(kwargs)
        return self.order_id

    def query_stock_order(self, order_id: int):
        assert int(order_id) == self.order_id
        self.query_count += 1
        if self.query_count == 1:
            return SimpleNamespace(
                order_id=self.order_id,
                stock_code="600000.SH",
                stock_name="Poll Test",
                order_type=23,
                order_status=50,
                status_msg="submitted",
                traded_volume=0,
                traded_price=0.0,
                order_time=datetime.now().strftime("%Y%m%d%H%M%S"),
            )
        return SimpleNamespace(
            order_id=self.order_id,
            stock_code="600000.SH",
            stock_name="Poll Test",
            order_type=23,
            order_status=56,
            status_msg="filled",
            traded_volume=100,
            traded_price=10.2,
            traded_time=datetime.now().strftime("%Y%m%d%H%M%S"),
        )


def _cleanup_smoketest_rows() -> None:
    trade_db = PROJECT_ROOT / "trading_app" / "data" / "trade_records.db"
    if trade_db.exists():
        conn = sqlite3.connect(str(trade_db))
        cur = conn.cursor()
        cur.execute("DELETE FROM trades WHERE intent_id LIKE ? OR remark LIKE ?", ("poll-smoke-%", "%poll smoketest%"))
        cur.execute("DELETE FROM order_records WHERE intent_id LIKE ? OR remark LIKE ?", ("poll-smoke-%", "%poll smoketest%"))
        conn.commit()
        conn.close()
    event_db = PROJECT_ROOT / "trading_app" / "data" / "live_strategy_center.db"
    if event_db.exists():
        conn = sqlite3.connect(str(event_db))
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM live_center_events WHERE category = ? AND (payload_json LIKE ? OR request_id LIKE ?)",
            ("order_execution", "%poll-smoke-%", "%poll-smoke-%"),
        )
        conn.commit()
        conn.close()


def _state(**kwargs):
    return normalize_order_state(SimpleNamespace(**kwargs))


def _assert_order_lifecycle_state_machine() -> None:
    lifecycle = OrderLifecycle("request-lifecycle", updated_at=100.0)
    submitted = _state(order_status=49, traded_volume=0, traded_price=0, status_msg="pending")
    accepted = _state(order_status=50, traded_volume=0, traded_price=0, status_msg="accepted")
    partial = _state(order_status=55, traded_volume=50, traded_price=10.1, status_msg="partial")
    filled = _state(order_status=56, traded_volume=100, traded_price=10.2, status_msg="filled")

    transition = lifecycle.on_broker_event(submitted, occurred_at=101.0)
    assert transition.previous_state == OrderLifecycleState.REQUESTED
    assert transition.current_state == OrderLifecycleState.SUBMITTED
    assert not transition.is_idempotent

    duplicate = lifecycle.on_broker_event(submitted, occurred_at=102.0)
    assert duplicate.current_state == OrderLifecycleState.SUBMITTED
    assert duplicate.is_idempotent

    lifecycle.on_broker_event(accepted, occurred_at=103.0)
    lifecycle.on_broker_event(partial, occurred_at=104.0)
    lifecycle.on_broker_event(filled, occurred_at=105.0)
    assert lifecycle.state == OrderLifecycleState.FILLED
    assert lifecycle.is_terminal

    try:
        lifecycle.on_broker_event(accepted, occurred_at=106.0)
    except OrderStateTransitionError:
        pass
    else:
        raise AssertionError("filled -> accepted must be rejected")

    stale = OrderLifecycle("request-timeout", updated_at=100.0)
    stale.on_broker_event(accepted, occurred_at=101.0)
    assert stale.mark_timeout_if_stale(now=105.0, timeout_seconds=10.0) is None
    timeout_transition = stale.mark_timeout_if_stale(now=112.0, timeout_seconds=10.0)
    assert timeout_transition is not None
    assert timeout_transition.current_state == OrderLifecycleState.TIMEOUT_PENDING
    assert not stale.is_terminal
    stale.on_broker_event(filled, occurred_at=113.0)
    assert stale.state == OrderLifecycleState.FILLED

    rebuilt = rebuild_order_lifecycle(
        "request-rebuild",
        [
            OrderLifecycleEvent("OrderSubmitted", snapshot=submitted, occurred_at=1.0),
            OrderLifecycleEvent("OrderAccepted", snapshot=accepted, occurred_at=2.0),
            OrderLifecycleEvent("OrderPartiallyFilled", snapshot=partial, occurred_at=3.0),
            OrderLifecycleEvent("OrderFilled", snapshot=filled, occurred_at=4.0),
        ],
    )
    assert rebuilt.state == OrderLifecycleState.FILLED
    assert rebuilt.snapshot == filled
    assert len(rebuilt.transitions) == 4


def _assert_order_event_ledger_rebuild() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        service = OrderExecutionEventService(Path(tmpdir) / "events.db")
        request_id = "request-ledger-filled"
        pending_request_id = "request-ledger-pending"

        service.add_event(
            OrderExecutionEvent(
                event_id="order:request-ledger-filled:pre_submit:OrderRequested:OrderRequested",
                occurred_at="2026-01-01 09:30:00",
                request_id=request_id,
                broker_order_id=0,
                symbol="600000",
                payload={"event_type": "OrderRequested"},
            )
        )
        service.add_event(
            OrderExecutionEvent(
                event_id="order:request-ledger-filled:12345:OrderSubmitted:OrderSubmitted",
                occurred_at="2026-01-01 09:30:01",
                request_id=request_id,
                broker_order_id=12345,
                symbol="600000",
                payload={"event_type": "OrderSubmitted"},
            )
        )
        service.add_event(
            OrderExecutionEvent(
                event_id="order:request-ledger-filled:12345:OrderFilled:status:56:volume:100",
                occurred_at="2026-01-01 09:30:02",
                request_id=request_id,
                broker_order_id=12345,
                symbol="600000",
                payload={
                    "event_type": "OrderFilled",
                    "order_status_code": 56,
                    "order_status_text": "已成",
                    "executed_volume": 100,
                    "executed_price": 10.2,
                },
            )
        )
        service.add_event(
            OrderExecutionEvent(
                event_id="order:request-ledger-pending:54321:OrderSubmitted:OrderSubmitted",
                occurred_at="2026-01-01 09:31:00",
                request_id=pending_request_id,
                broker_order_id=54321,
                symbol="000001",
                payload={"event_type": "OrderSubmitted"},
            )
        )
        service.add_event(
            OrderExecutionEvent(
                event_id="order:request-ledger-pending:54321:OrderPendingConfirmation:timeout",
                occurred_at="2026-01-01 09:31:05",
                request_id=pending_request_id,
                broker_order_id=54321,
                symbol="000001",
                status="open",
                payload={"event_type": "OrderPendingConfirmation", "latest_status": "已报"},
            )
        )

        request_events = service.query_by_request_id(request_id)
        assert [event.event_type for event in request_events] == ["OrderRequested", "OrderSubmitted", "OrderFilled"]
        assert service.query_by_broker_order_id(12345)[-1].event_type == "OrderFilled"
        assert len(service.replay_since("2026-01-01 09:30:01")) == 4

        lifecycle = service.rebuild_state(request_id)
        assert lifecycle is not None
        assert lifecycle.state == OrderLifecycleState.FILLED
        assert lifecycle.is_terminal
        assert lifecycle.snapshot is not None
        assert lifecycle.snapshot.traded_volume == 100

        pending_lifecycle = service.rebuild_state(pending_request_id)
        assert pending_lifecycle is not None
        assert pending_lifecycle.state == OrderLifecycleState.TIMEOUT_PENDING
        assert not pending_lifecycle.is_terminal

        open_orders = service.query_open_orders()
        assert list(open_orders.keys()) == [pending_request_id]
        assert open_orders[pending_request_id].state == OrderLifecycleState.TIMEOUT_PENDING


def _assert_order_event_idempotency() -> None:
    storage = _MemoryEventStorage()
    service = TradeExecutionService(broker_service=SimpleNamespace(is_connected=True))
    service._event_storage = storage
    request = ExecutionRequest(
        stock_code="600000.SH",
        stock_name="Test",
        order_type=23,
        order_volume=100,
        price_type=5,
        price=10.0,
        source="smoketest",
        trigger="manual",
        strategy_id="smoketest_strategy",
        intent_id="intent-1",
    )

    for _ in range(2):
        service._record_order_event(
            event_type="OrderFilled",
            request_id="request-1",
            request=request,
            mode="live",
            broker_order_id=12345,
            order_record_id=9,
            dedupe_key="status:56:volume:100",
            title="filled",
            message="filled",
            payload={"order_status_code": 56, "executed_volume": 100},
        )

    assert len(storage.events) == 2
    assert storage.events[0].event_id == storage.events[1].event_id
    assert storage.events[0].event_id == "order:request-1:12345:OrderFilled:status:56:volume:100"
    assert storage.events[0].payload["event_scope"] == "status:56:volume:100"

    service._record_order_event(
        event_type="OrderFilled",
        request_id="request-1",
        request=request,
        mode="live",
        broker_order_id=54321,
        order_record_id=9,
        dedupe_key="status:56:volume:100",
        title="filled",
        message="filled",
    )
    assert storage.events[-1].event_id == "order:request-1:54321:OrderFilled:status:56:volume:100"


def _assert_order_event_observability_context() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LiveStrategyCenterStorage(Path(tmpdir) / "events.db")
        storage.add_event(
            LiveCenterEvent(
                event_id="order:request-1:12345:OrderFilled:status:56:volume:100",
                occurred_at="2026-01-01 09:30:00",
                level="info",
                category="order_execution",
                source="smoketest",
                strategy_id="smoketest_strategy",
                symbol="600000",
                request_id="request-1",
                broker_order_id=12345,
                title="订单已成交",
                message="委托已成交，单号 12345",
                status="resolved",
                payload_json=storage.dumps_payload(
                    {
                        "event_type": "OrderFilled",
                        "event_scope": "status:56:volume:100",
                        "direction": "buy",
                        "order_volume": 100,
                        "executed_volume": 100,
                        "executed_price": 10.2,
                        "order_status_text": "已成",
                        "order_record_id": 7,
                    }
                ),
            )
        )
        service = AlertEventService(storage=storage)
        rows = service.list_events(category="order_execution", limit=10)
        assert len(rows) == 1
        row = rows[0]
        assert row["order_reference"] == "600000 / 委托 12345 / 请求 request-"
        assert row["order_event_type"] == "OrderFilled"
        assert row["order_event_scope"] == "status:56:volume:100"
        assert "OrderFilled" in row["order_observable_detail"]
        assert "100股" in row["order_observable_detail"]
        assert "已成" in row["order_observable_detail"]


def _assert_execute_polling_flow() -> None:
    _cleanup_smoketest_rows()
    broker = _PollingFakeBroker()
    storage = _MemoryEventStorage()
    service = TradeExecutionService(broker_service=broker)
    service._event_storage = storage
    service._validate_market_data_status = lambda _request: ""
    service.config_service.get_config = lambda: AutoTradeConfig(
        manual_orders_enabled=True,
        auto_trade_mode="live",
        require_trading_time=False,
        duplicate_window_seconds=1,
        status_poll_seconds=1.0,
        status_poll_interval_seconds=0.01,
    )
    intent_id = f"poll-smoke-{int(time.time() * 1000)}"
    request = ExecutionRequest(
        stock_code="600000.SH",
        stock_name="Poll Test",
        order_type=23,
        order_volume=100,
        price_type=5,
        price=10.2,
        source="smoketest",
        trigger="manual",
        intent_id=intent_id,
        remark="poll smoketest",
    )

    try:
        result = service.execute(request)
        assert result.success, result.message
        assert result.live_submitted
        assert result.filled_confirmed
        assert result.broker_order_id == broker.order_id
        assert result.order_status == "已成"
        assert broker.ordered
        assert broker.query_count >= 2

        order_record = service.trade_service.get_order_record_by_request_id(result.request_id)
        assert order_record is not None
        assert order_record.status == "filled"
        assert order_record.executed_volume == 100
        assert order_record.order_status_code == 56

        trade = service.trade_service.get_latest_record_by_broker_order_id(broker.order_id)
        assert trade is not None
        assert trade.intent_id == intent_id
        assert trade.volume == 100

        event_types = [event.payload.get("event_type") for event in storage.events]
        assert "OrderSubmitted" in event_types
        assert "OrderFilled" in event_types
        filled_event = [event for event in storage.events if event.payload.get("event_type") == "OrderFilled"][-1]
        assert filled_event.event_id.endswith("OrderFilled:status:56:volume:100")
        assert filled_event.payload["trade_record_id"] == trade.id
    finally:
        _cleanup_smoketest_rows()


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

    _assert_order_lifecycle_state_machine()
    _assert_order_event_ledger_rebuild()
    _assert_order_event_idempotency()
    _assert_order_event_observability_context()
    _assert_execute_polling_flow()

    print("order_state_machine_smoketest_ok")


if __name__ == "__main__":
    main()
