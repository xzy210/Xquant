from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from common.execution_contract import FillReport, OrderExecutionReport, OrderIntent
from live_rotation.config import RotationConfig
from live_rotation.rotation_execution_service import RotationExecutionService
from live_rotation.state_manager import RotationState
from live_rotation.trade_executor import TradeExecutor


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
    return RotationExecutionService(
        config=RotationConfig(cash_ratio=1.0, min_trade_amount=100.0, notify_on_trade=False),
        state=state,
        state_mgr=state_mgr,
        executor=executor,
        ledger_service=FakeLedgerService(recorder),
        trade_event_fn=lambda success, result: recorder.trade_events.append((success, result.copy())),
        logger_fn=recorder.logs.append,
        code_name_fn=lambda code: f"ETF-{code}",
        code_name_map_fn=lambda code: f"Name-{code}",
    )


def main() -> None:
    recorder = Recorder()
    state = RotationState()
    executor = FakeExecutor()
    service = _service(state, executor, recorder)

    buy_report = OrderExecutionReport(
        intent=OrderIntent(
            symbol="510880",
            side="buy",
            quantity=5000,
            price=10.0,
            strategy_id="etf_rotation",
            reason="test buy",
        ),
        accepted=True,
        status="filled",
        message="bought",
        order_id="1",
        execution_mode="live",
        fills=(FillReport(symbol="510880", side="buy", quantity=5000, price=10.0, order_id="1"),),
        filled=True,
    )
    apply_result = service.apply_execution_reports([buy_report], scores={"510880": 2.0}, reason="test buy")
    assert apply_result["success"] is True
    assert apply_result["trades"][0]["action"] == "BUY"
    assert state.current_holding == "510880"
    assert state.buy_quantity == 5000
    assert state.current_score == 2.0
    assert len(recorder.trade_events) == 1

    recorder = Recorder()
    recorder.fill_qty_override = 400
    state = RotationState(current_holding="159949", buy_price=18.0, buy_quantity=1000)
    executor = FakeExecutor()
    service = _service(state, executor, recorder)

    sell_report = OrderExecutionReport(
        intent=OrderIntent(
            symbol="159949",
            side="sell",
            quantity=1000,
            price=20.0,
            strategy_id="etf_rotation",
            reason="candidate wins",
        ),
        accepted=True,
        status="partial_fill",
        message="partial",
        order_id="11",
        execution_mode="live",
        fills=(
            FillReport(
                symbol="159949",
                side="sell",
                quantity=400,
                price=20.0,
                order_id="11",
                strategy_id="etf_rotation",
            ),
        ),
        partial=True,
    )
    apply_result = service.apply_execution_reports([sell_report], scores={"510880": 2.0}, reason="candidate wins")
    assert apply_result["success"] is True
    assert apply_result["trades"][0]["partial_fill"] is True
    assert apply_result["trades"][0]["remaining"] == 600
    assert state.current_holding == "159949"
    assert state.buy_quantity == 600

    recorder = Recorder()
    state = RotationState(current_holding="159949", buy_price=18.0, buy_quantity=1000)
    executor = FakeExecutor()
    service = _service(state, executor, recorder)

    reports = [
        OrderExecutionReport(
            intent=OrderIntent(
                symbol="159949",
                side="sell",
                quantity=1000,
                price=20.0,
                strategy_id="etf_rotation",
                reason="candidate wins",
            ),
            accepted=True,
            status="filled",
            message="sold",
            order_id="21",
            execution_mode="live",
            fills=(FillReport(symbol="159949", side="sell", quantity=1000, price=20.0, order_id="21"),),
            filled=True,
        ),
        OrderExecutionReport(
            intent=OrderIntent(
                symbol="510880",
                side="buy",
                quantity=5000,
                price=10.0,
                strategy_id="etf_rotation",
                reason="candidate wins",
            ),
            accepted=True,
            status="filled",
            message="bought",
            order_id="22",
            execution_mode="live",
            fills=(FillReport(symbol="510880", side="buy", quantity=5000, price=10.0, order_id="22"),),
            filled=True,
        ),
    ]
    apply_result = service.apply_execution_reports(reports, scores={"510880": 2.0}, reason="candidate wins")
    assert apply_result["success"] is True
    assert len(apply_result["trades"]) == 2
    assert state.current_holding == "510880"
    assert state.current_score == 2.0
    assert len(recorder.ledger_buys) == 0
    assert len(recorder.ledger_sells) == 0

    print("rotation_execution_service_smoketest_ok")


if __name__ == "__main__":
    main()
