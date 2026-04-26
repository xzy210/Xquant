from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from live_rotation.config import RotationConfig
from live_rotation.rotation_execution_service import RotationExecutionService
from live_rotation.state_manager import RotationState
from live_rotation.trade_executor import TradeExecutor, XtQuantExecutor


class FakeStateManager:
    def __init__(self, state: RotationState) -> None:
        self.state = state
        self.saved = 0
        self.holding_updates: list[tuple] = []
        self.clear_count = 0

    def save(self) -> None:
        self.saved += 1

    def update_holding(self, code: str, name: str, score: float, price: float, quantity: int) -> None:
        self.state.current_holding = code
        self.state.current_holding_name = name
        self.state.current_score = score
        self.state.buy_price = price
        self.state.buy_quantity = quantity
        self.state.holding_high_price = price
        self.holding_updates.append((code, name, score, price, quantity))
        self.save()

    def clear_holding(self) -> None:
        self.state.current_holding = None
        self.state.current_holding_name = ""
        self.state.current_score = 0.0
        self.state.buy_price = 0.0
        self.state.buy_quantity = 0
        self.state.holding_high_price = 0.0
        self.clear_count += 1
        self.save()


class FakeExecutor(TradeExecutor):
    def __init__(self) -> None:
        self.prices = {"510880": 10.0, "159949": 20.0}
        self.positions = {"159949": {"quantity": 1000, "avg_price": 18.0}}
        self.cash = 100_000.0
        self.order_id = 0
        self.next_sell_success = True
        self.next_sell_message = "卖出成功"

    def is_connected(self) -> bool:
        return True

    def buy(self, code: str, amount: float, price: Optional[float] = None):
        current_price = float(price or self.prices[code])
        quantity = int(amount / current_price / 100) * 100
        self.order_id += 1
        self.positions[code] = {"quantity": quantity, "avg_price": current_price}
        return True, "买入成功", self.order_id, current_price, quantity

    def sell(self, code: str, quantity: int, price: Optional[float] = None):
        self.order_id += 1
        if not self.next_sell_success:
            return False, self.next_sell_message, self.order_id
        current_price = float(price or self.prices[code])
        pos = self.positions.get(code, {"quantity": 0, "avg_price": current_price})
        pos["quantity"] = max(0, int(pos.get("quantity", 0)) - quantity)
        if pos["quantity"] <= 0:
            self.positions.pop(code, None)
        else:
            self.positions[code] = pos
        return True, "卖出成功", self.order_id

    def get_current_price(self, code: str) -> float:
        return float(self.prices.get(code, 0.0))

    def query_position(self, code: str):
        pos = self.positions.get(code)
        if not pos:
            return 0, 0.0
        return int(pos["quantity"]), float(pos["avg_price"])

    def query_sellable_position(self, code: str):
        return self.query_position(code)


class Recorder:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.trade_events: list[tuple[bool, dict]] = []
        self.partial_events: list[tuple[dict, int, str, str]] = []
        self.order_records: list[tuple] = []
        self.order_updates: list[tuple] = []
        self.capital_entries: list[tuple] = []
        self.ledger_buys: list[dict] = []
        self.ledger_sells: list[dict] = []
        self.fill_qty_override: Optional[int] = None


class FakeLedgerService:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def update_context(self, **kwargs) -> None:
        pass

    def available_cash(self) -> float:
        return 50_000.0

    def resolve_trade_fees(self, **kwargs) -> dict:
        return {
            "commission": 0.0,
            "stamp_tax": 0.0,
            "transfer_fee": 0.0,
            "total_fee": 0.0,
        }

    def add_order_record(self, *args, **kwargs) -> None:
        self.recorder.order_records.append(args)

    def update_order_record(self, *args, **kwargs) -> None:
        self.recorder.order_updates.append((args, kwargs))

    def add_capital_entry(self, *args, **kwargs) -> None:
        self.recorder.capital_entries.append((args, kwargs))

    def sync_unified_ledger_on_buy(self, **kwargs) -> None:
        self.recorder.ledger_buys.append(kwargs)

    def sync_unified_ledger_on_sell(self, **kwargs) -> None:
        self.recorder.ledger_sells.append(kwargs)


def _service(state: RotationState, executor: FakeExecutor, recorder: Recorder) -> RotationExecutionService:
    state_mgr = FakeStateManager(state)

    def confirm_fill(order_id: int, expected_qty: int, expected_price: float) -> dict:
        filled_qty = recorder.fill_qty_override if recorder.fill_qty_override is not None else expected_qty
        return {
            "filled": filled_qty == expected_qty,
            "filled_qty": filled_qty,
            "filled_price": expected_price,
            "commission": -1.0,
            "timed_out": False,
        }

    return RotationExecutionService(
        config=RotationConfig(cash_ratio=1.0, min_trade_amount=100.0, notify_on_trade=False),
        state=state,
        state_mgr=state_mgr,
        executor=executor,
        ledger_service=FakeLedgerService(recorder),
        ensure_price_fn=executor.get_current_price,
        preflight_risk_fn=lambda **kwargs: (True, "ok"),
        confirm_fill_fn=confirm_fill,
        trade_event_fn=lambda success, result: recorder.trade_events.append((success, result.copy())),
        partial_switch_stop_fn=lambda sell_result, remaining, message, reason: recorder.partial_events.append(
            (sell_result.copy(), remaining, message, reason)
        ),
        logger_fn=recorder.logs.append,
        code_name_fn=lambda code: f"ETF-{code}",
        code_name_map_fn=lambda code: f"Name-{code}",
    )


def main() -> None:
    recorder = Recorder()
    state = RotationState()
    executor = FakeExecutor()
    service = _service(state, executor, recorder)

    buy_result = service.buy("510880", 50_000.0, reason="test buy")
    assert buy_result["success"] is True
    assert buy_result["action"] == "BUY"
    assert state.current_holding == "510880"
    assert state.buy_quantity == 5000
    assert len(recorder.trade_events) == 1
    assert len(recorder.ledger_buys) == 1

    recorder = Recorder()
    state = RotationState(current_holding="159949", buy_price=18.0, buy_quantity=1000)
    executor = FakeExecutor()
    service = _service(state, executor, recorder)

    sell_result = service.sell_all(reason="test sell")
    assert sell_result["success"] is True
    assert sell_result["action"] == "SELL"
    assert sell_result["quantity"] == 1000
    assert state.current_holding is None
    assert len(recorder.ledger_sells) == 1

    recorder = Recorder()
    recorder.fill_qty_override = 400
    state = RotationState(current_holding="159949", buy_price=18.0, buy_quantity=1000)
    executor = FakeExecutor()
    service = _service(state, executor, recorder)

    switch_result = service.execute_signal(
        "SWITCH",
        "510880",
        {"510880": 2.0, "159949": 1.0},
        "candidate wins",
    )
    assert switch_result["success"] is False
    assert "部分成交" in switch_result["reason"]
    assert state.current_holding == "159949"
    assert state.buy_quantity == 600
    assert len(recorder.partial_events) == 1

    recorder = Recorder()
    state = RotationState(current_holding="159949", buy_price=18.0, buy_quantity=1000)
    executor = FakeExecutor()
    service = _service(state, executor, recorder)

    switch_result = service.execute_signal(
        "SWITCH",
        "510880",
        {"510880": 2.0, "159949": 1.0},
        "candidate wins",
    )
    assert switch_result["success"] is True
    assert len(switch_result["trades"]) == 2
    assert state.current_holding == "510880"
    assert state.current_score == 2.0

    class FakeExecutionGateway:
        def __init__(self) -> None:
            self.intent = None
            self.stock_name = ""

        def execute_order_intent(self, intent, *, stock_name: str = ""):
            self.intent = intent
            self.stock_name = stock_name
            return type(
                "Report",
                (),
                {
                    "accepted": True,
                    "message": "submitted",
                    "order_id": "321",
                },
            )()

    xt_executor = XtQuantExecutor()
    xt_executor._broker_session_service = type("Session", (), {"is_connected": True})()
    xt_executor._execution_service = FakeExecutionGateway()
    xt_executor.get_current_price_snapshot = lambda code, allow_daily_fallback=False: type(
        "Snapshot",
        (),
        {"price": 2.5, "is_fresh": True, "message": "fresh"},
    )()
    ok, message, order_id, price, quantity = xt_executor.buy("510880", 1_000.0)
    assert ok is True
    assert order_id == 321
    assert price == 2.5
    assert quantity == 400
    assert xt_executor._execution_service.intent.schema_version == "order_intent.v1"
    assert xt_executor._execution_service.intent.side == "buy"
    assert xt_executor._execution_service.intent.source == "etf_rotation"
    assert xt_executor._execution_service.stock_name == "510880"

    print("rotation_execution_service_smoketest_ok")


if __name__ == "__main__":
    main()
