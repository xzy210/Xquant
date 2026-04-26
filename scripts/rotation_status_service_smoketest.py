from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from live_rotation.config import RotationConfig
from live_rotation.rotation_status_service import RotationStatusService
from live_rotation.state_manager import RotationState
from live_rotation.trade_executor import PriceSnapshot, SimulatedExecutor


class SnapshotExecutor(SimulatedExecutor):
    def __init__(self, snapshot: PriceSnapshot) -> None:
        super().__init__()
        self.snapshot = snapshot

    def get_current_price_snapshot(
        self,
        code: str,
        *,
        allow_daily_fallback: bool = True,
        require_fresh: bool = True,
    ) -> PriceSnapshot:
        return self.snapshot


class FakeLedgerService:
    def __init__(self, available_cash: float) -> None:
        self.available_cash = available_cash

    def ledger_available_cash(self) -> float:
        return self.available_cash


def main() -> None:
    config = RotationConfig(
        auto_enabled=True,
        use_dedicated_capital=True,
        dedicated_capital=100000.0,
    )
    state = RotationState(
        current_holding="510880",
        current_holding_name="红利ETF",
        buy_price=2.0,
        buy_date="2026-04-20",
        buy_quantity=1000,
        last_check_date="2026-04-24",
        last_check_time="14:50:00",
        last_signal="BUY",
        trades_today=1,
        trades_today_date="2026-04-24",
        total_pnl=120.0,
        holding_high_price=2.2,
        cooldown_remaining=0,
        last_scores={"510880": 1.5},
        trade_history=[
            {
                "date": "2026-04-01",
                "time": "14:50:00",
                "action": "BUY",
                "code": "510880",
                "price": 2.0,
                "quantity": 1000,
                "amount": 2000.0,
                "success": True,
            },
            {
                "date": "2026-04-10",
                "time": "14:50:00",
                "action": "SELL",
                "code": "510880",
                "price": 2.1,
                "quantity": 1000,
                "amount": 2100.0,
                "success": True,
                "pnl": 100.0,
            },
            {
                "date": "2026-04-11",
                "time": "14:50:00",
                "action": "SELL_ALL",
                "code": "159949",
                "price": 1.0,
                "quantity": 1000,
                "amount": 1000.0,
                "success": True,
                "pnl": -20.0,
            },
        ],
        daily_equity={
            "2026-04-20": 100000.0,
            "2026-04-21": 102000.0,
            "2026-04-22": 99000.0,
            "2026-04-24": 101000.0,
        },
    )
    executor = SimulatedExecutor(initial_cash=50000.0)
    executor.set_prices({"510880": 2.1})

    service = RotationStatusService(
        config=config,
        state=state,
        executor=executor,
        ledger_service=FakeLedgerService(98000.0),
        data_dir=PROJECT_ROOT / "live_rotation" / "data",
        data_fresh_fn=lambda: True,
        now_fn=lambda: datetime(2026, 4, 24, 15, 0, 0),
    )

    summary = service.get_status_summary()
    assert summary["holding"] == "510880"
    assert summary["current_price"] == 2.1
    assert summary["price_is_realtime"] is True
    assert round(summary["unrealized_pnl"], 6) == 100.0
    assert summary["last_signal"] == "BUY"
    assert summary["data_fresh"] is True
    assert summary["dedicated_cash"] == 98000.0
    assert summary["executor_connected"] is True

    latest_price_service = RotationStatusService(
        config=config,
        state=state,
        executor=SnapshotExecutor(
            PriceSnapshot(
                price=2.05,
                source="tick",
                is_fresh=False,
                message="stale tick accepted as latest price",
            )
        ),
        ledger_service=FakeLedgerService(98000.0),
        data_dir=PROJECT_ROOT / "live_rotation" / "data",
        data_fresh_fn=lambda: True,
        now_fn=lambda: datetime(2026, 4, 24, 15, 0, 0),
    )
    latest_summary = latest_price_service.get_status_summary()
    assert latest_summary["current_price"] == 2.05
    assert latest_summary["price_is_realtime"] is False
    assert latest_summary["price_source"] == "tick"
    assert round(latest_summary["unrealized_pnl"], 6) == 50.0

    stats = service.get_statistics()
    assert stats["total_trades"] == 2
    assert stats["win_trades"] == 1
    assert stats["loss_trades"] == 1
    assert stats["win_rate"] == 50.0
    assert stats["avg_pnl"] == 40.0
    assert stats["best_trade"] == 100.0
    assert stats["worst_trade"] == -20.0
    assert stats["total_pnl"] == 120.0
    assert stats["current_equity"] == 100100.0
    assert stats["initial_capital"] == 100000.0
    assert stats["total_return_pct"] == 0.1
    assert round(stats["max_drawdown"], 6) == round((102000.0 - 99000.0) / 102000.0 * 100, 6)
    assert stats["avg_hold_days"] == 9.0
    assert stats["current_hold_days"] == 4

    print("rotation_status_service_smoketest_ok")


if __name__ == "__main__":
    main()
