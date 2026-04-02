from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "trading_app"))

from trading_app.services.agent_context_service import BrokerContext
from trading_app.services.auto_trade_config_service import AutoTradeConfig
from trading_app.services.daily_auto_trade_service import DailyAutoTradeService
from trading_app.services.trade_decision_models import RiskCheckResult, TradeDecision
from trading_app.services.trade_execution_service import TradeExecutionService


class FakeBroker:
    def __init__(self):
        self.is_connected = True
        self.order_calls = 0

    def query_stock_asset(self):
        return SimpleNamespace(cash=500000.0, total_asset=1000000.0)

    def query_stock_positions(self):
        return []

    def query_stock_orders(self):
        return []

    def order_stock(self, **kwargs):
        self.order_calls += 1
        raise RuntimeError("shadow 模式不应触发真实下单")


def main():
    broker = FakeBroker()
    service = DailyAutoTradeService(broker_service=broker)
    execution_service = TradeExecutionService(broker)
    execution_service.config_service.get_config = lambda: AutoTradeConfig(
        manual_orders_enabled=True,
        auto_trade_mode="shadow",
        require_trading_time=False,
        duplicate_window_seconds=1,
        status_poll_seconds=1.0,
        status_poll_interval_seconds=0.2,
        max_new_positions_per_day=2,
        max_buy_orders_per_day=2,
        max_sell_orders_per_day=2,
        reserve_cash_pct=0.2,
        max_intraday_failures=2,
        max_daily_loss_pct=0.5,
        auto_reconcile_enabled=False,
        reconcile_time="15:10",
        reconcile_retry_time="15:20",
    )
    service.execution_service = execution_service
    service._check_previous_snapshot_guard = lambda: ""
    task_id = f"test_task_{int(time.time())}"

    ok, msg = service.begin_task(task_id, {"name": "测试任务", "auto_execute": True})
    print("BEGIN=", ok, msg)

    decision = TradeDecision(
        action="buy",
        symbol_code="000001.SZ",
        symbol_name="平安银行",
        confidence=0.82,
        current_price=10.0,
        position_pct=0.1,
        risk_score=0.2,
    )
    risk_result = RiskCheckResult(
        passed=True,
        checks=[],
        overall_risk_level="low",
        warnings=[],
        blocked_reasons=[],
    )

    captured = []
    service.cycle_finished.connect(lambda task_id, success, message, summary: captured.append((task_id, success, message, summary)))
    service.handle_scan_results(
        task_id,
        {"name": "测试任务", "auto_execute": True},
        [{
            "decision": decision,
            "risk_result": risk_result,
            "symbol_code": "000001.SZ",
            "symbol_name": "平安银行",
            "decision_record_id": "demo123",
        }],
        BrokerContext(
            connected=True,
            total_asset=1000000.0,
            available_cash=500000.0,
            position_count=0,
            top_positions=[],
        ),
    )

    task_id, success, message, summary = captured[0]
    print("CYCLE=", task_id, success, message)
    print("EXECUTED=", summary.get("executed"))
    print("BROKER_ORDER_CALLS=", broker.order_calls)


if __name__ == "__main__":
    main()
