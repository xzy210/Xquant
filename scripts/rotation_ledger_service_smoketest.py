from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from live_rotation.config import RotationConfig
from live_rotation.rotation_ledger_service import RotationLedgerService
from live_rotation.state_manager import CapitalLedgerEntry, OrderRecord, RotationState
from live_rotation.trade_executor import SimulatedExecutor


class FakeStateManager:
    def __init__(self, state: RotationState) -> None:
        self.state = state
        self.saved = 0

    def save(self) -> None:
        self.saved += 1

    def add_capital_entry(self, entry: CapitalLedgerEntry) -> None:
        self.state.capital_ledger.append(entry.to_dict())
        self.save()

    def add_order_record(self, record: OrderRecord) -> None:
        self.state.order_records = [
            item for item in self.state.order_records
            if item.get("order_id") != record.order_id
        ]
        self.state.order_records.append(record.to_dict())
        self.save()

    def update_order_record(self, order_id: int, **kwargs) -> None:
        for item in self.state.order_records:
            if item.get("order_id") == order_id:
                item.update(kwargs)
                break
        self.save()

    def record_daily_equity(self, equity: float) -> None:
        self.state.daily_equity["today"] = round(float(equity), 2)
        self.save()


class Recorder:
    def __init__(self) -> None:
        self.logs: list[str] = []


def main() -> None:
    state = RotationState(
        current_holding="510880",
        current_holding_name="红利ETF",
        buy_price=9.5,
        buy_quantity=1000,
    )
    state_mgr = FakeStateManager(state)
    executor = SimulatedExecutor(initial_cash=80_000.0)
    executor.set_prices({"510880": 10.0})
    recorder = Recorder()
    service = RotationLedgerService(
        config=RotationConfig(use_dedicated_capital=False),
        state=state,
        state_mgr=state_mgr,
        executor=executor,
        strategy_identity_fn=lambda: ("etf_rotation", "ETF轮动", "va_etf_rotation"),
        code_name_map_fn=lambda code: "红利ETF" if code == "510880" else code,
        logger_fn=recorder.logs.append,
    )

    assert service.available_cash() == 80_000.0
    assert service.total_asset() == 90_000.0

    order = service.add_order_record(1, "买入", "510880", 1000, 10.0, "smoke")
    assert order.order_id == 1
    assert len(state.order_records) == 1
    assert state.order_records[0]["status"]

    service.update_order_record(
        1,
        {
            "filled": True,
            "filled_qty": 1000,
            "filled_price": 10.0,
            "commission": 1.23,
            "timed_out": False,
        },
        pnl=0.0,
    )
    assert state.order_records[0]["filled_qty"] == 1000
    assert state.order_records[0]["commission"] == 1.23

    service.record_daily_equity()
    assert state.daily_equity["today"] == 90_000.0

    state.trade_history = [{"action": "BUY"}]
    state.capital_ledger = [{"action": "test"}]
    state.total_pnl = 123.0
    service.clear_analytics_data()
    assert state.trade_history == []
    assert state.order_records == []
    assert state.capital_ledger == []
    assert state.daily_equity == {}
    assert state.total_pnl == 0.0
    assert recorder.logs[-1] == "🗑 历史分析数据已全部清空"

    print("rotation_ledger_service_smoketest_ok")


if __name__ == "__main__":
    main()
