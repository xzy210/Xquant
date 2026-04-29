from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from common.execution_contract import OrderExecutionReport, RebalanceIntent, TargetPortfolio
from live_rotation.config import RotationConfig
from live_rotation.rotation_runtime_service import RotationRuntimeService
from live_rotation.state_manager import RotationState
from live_rotation.trade_executor import SimulatedExecutor


class FakeStateManager:
    def __init__(self, state: RotationState) -> None:
        self.state = state
        self.signal_tasks: list[dict] = []
        self.data_tasks: list[dict] = []
        self.check_results: list[tuple[str, dict]] = []

    def save(self) -> None:
        pass

    def mark_auto_signal_task(self, **kwargs) -> None:
        self.signal_tasks.append(kwargs)

    def mark_auto_data_task(self, **kwargs) -> None:
        self.data_tasks.append(kwargs)

    def is_auto_data_task_completed(self, **kwargs) -> bool:
        return False

    def is_auto_signal_task_completed(self, **kwargs) -> bool:
        return False

    def update_check_result(self, signal: str, scores: dict) -> None:
        self.check_results.append((signal, scores.copy()))
        self.state.last_signal = signal
        self.state.last_scores = scores.copy()


class FakeConfigManager:
    def __init__(self) -> None:
        self.saved: list[RotationConfig] = []

    def save(self, config: RotationConfig) -> None:
        self.saved.append(config)


class FakeSignalService:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.updated = 0

    def update_context(self, **kwargs) -> None:
        self.updated += 1

    def calculate_scores(self) -> dict[str, float]:
        return self.scores.copy()


class FakeDecisionService:
    def __init__(self, decision: tuple[str, str | None, str]) -> None:
        self.decision = decision
        self.updated = 0

    def update_context(self, **kwargs) -> None:
        self.updated += 1

    def make_decision(self, scores: dict[str, float]) -> tuple[str, str | None, str]:
        return self.decision


class FakeGuardService:
    def __init__(self) -> None:
        self.check_count_updates = 0

    def in_drawdown_cooldown(self) -> bool:
        return False

    def check_drawdown_protection(self):
        return False, {}

    def check_trailing_stop(self):
        return False, {}

    def update_check_count(self) -> None:
        self.check_count_updates += 1

    def filter_rebalance_signal(self, signal: str, target: str | None, reason: str):
        return signal, target, reason, False


class FakeLedgerService:
    def __init__(self) -> None:
        self.snapshots = 0
        self.cash = 100_000.0
        self.asset = 100_000.0

    def record_daily_equity(self) -> None:
        self.snapshots += 1

    def available_cash(self) -> float:
        return self.cash

    def total_asset(self) -> float:
        return self.asset


class FakeTimer:
    def __init__(self) -> None:
        self.started: list[int] = []
        self.stopped = 0

    def start(self, interval: int) -> None:
        self.started.append(interval)

    def stop(self) -> None:
        self.stopped += 1


class Recorder:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.statuses: list[str] = []
        self.signals: list[tuple[str, dict]] = []
        self.scores: list[dict] = []
        self.notifications: list[tuple] = []


def _service(
    config: RotationConfig,
    state: RotationState,
    recorder: Recorder,
    *,
    execute_rebalance_fn=None,
) -> tuple[RotationRuntimeService, FakeLedgerService, FakeTimer, FakeStateManager]:
    state_mgr = FakeStateManager(state)
    config_mgr = FakeConfigManager()
    ledger = FakeLedgerService()
    timer = FakeTimer()
    executor = SimulatedExecutor()
    executor.set_prices({"510880": 10.0, "159949": 20.0})
    runtime = RotationRuntimeService(
        config=config,
        state=state,
        state_mgr=state_mgr,
        config_mgr=config_mgr,
        executor=executor,
        data_dir=PROJECT_ROOT / "live_rotation" / "data",
        signal_service=FakeSignalService({"510880": 1.2, "159949": 0.5}),
        decision_service=FakeDecisionService(("BUY", "510880", "smoke buy")),
        guard_service=FakeGuardService(),
        ledger_service=ledger,
        auto_timer=timer,
        logger_fn=recorder.logs.append,
        status_fn=recorder.statuses.append,
        signal_fn=lambda signal, result: recorder.signals.append((signal, result.copy())),
        scores_fn=lambda scores: recorder.scores.append(scores.copy()),
        notify_signal_fn=lambda *args: recorder.notifications.append(args),
        execute_rebalance_fn=execute_rebalance_fn,
        now_fn=lambda: datetime(2026, 4, 24, 14, 50),
        trading_day_fn=lambda value: True,
    )
    runtime.check_live_market_data_ready = lambda **kwargs: (True, "ok")
    return runtime, ledger, timer, state_mgr


def main() -> None:
    recorder = Recorder()
    config = RotationConfig(notify_on_signal=True, auto_signal_enabled=True)
    state = RotationState()
    runtime, ledger, timer, state_mgr = _service(config, state, recorder)

    result = runtime.run_signal_check(
        schedule_context={
            "schedule_time": "14:50",
            "trigger": "smoke",
            "task_date": "2026-04-24",
        },
    )
    assert result["signal"] == "BUY"
    assert result["target"] == "510880"
    assert result["executed"] is False
    assert "trade_result" not in result
    assert state_mgr.signal_tasks[-1]["status"] == "completed"
    assert result["target_portfolio"]["schema_version"] == "target_portfolio.v1"
    assert result["target_portfolio"]["weights"] == {"510880": 0.99}
    assert result["rebalance_intent"]["schema_version"] == "rebalance_intent.v1"
    assert len(result["order_intents"]) == 1
    assert result["order_intents"][0]["schema_version"] == "order_intent.v1"
    assert result["order_intents"][0]["intent_type"] == "target_portfolio"
    assert result["order_intents"][0]["quantity"] == 9900
    assert result["strategy_signals"][0]["schema_version"] == "strategy_signal.v1"
    assert result["strategy_signals"][0]["metadata"]["virtual_account_id"]
    assert result["strategy_signals"][0]["metadata"]["rebalance_intent_id"]
    assert isinstance(TargetPortfolio(**result["target_portfolio"]), TargetPortfolio)
    assert isinstance(
        runtime.build_rebalance_intent("BUY", "510880", "smoke buy"),
        RebalanceIntent,
    )
    hold_intent = runtime.build_rebalance_intent("HOLD", None, "keep holding")
    assert hold_intent.target_portfolio.weights == {}
    assert hold_intent.order_intents == ()

    runtime.start_auto()
    assert config.auto_enabled is True
    assert timer.started == [30_000]
    runtime.stop_auto()
    assert config.auto_enabled is False
    assert timer.stopped == 1

    recorder2 = Recorder()
    config2 = RotationConfig()
    runtime2, ledger2, _timer2, _state_mgr2 = _service(config2, RotationState(), recorder2)
    runtime2.check_live_market_data_ready = lambda **kwargs: (False, "missing bars")
    blocked = runtime2.run_signal_check()
    assert blocked["signal"] == "BLOCKED"
    assert ledger2.snapshots == 0
    assert "行情数据未就绪" in blocked["reason"]

    recorder3 = Recorder()
    config3 = RotationConfig(
        auto_enabled=True,
        auto_signal_enabled=False,
        data_update_time="14:40",
        check_time="14:50",
    )
    runtime3, _ledger3, _timer3, state_mgr3 = _service(config3, RotationState(), recorder3)
    runtime3.is_data_fresh = lambda: True
    update_calls: list[dict] = []
    runtime3.update_data = lambda **kwargs: update_calls.append(kwargs)
    runtime3.on_auto_timer()
    assert runtime3.auto_data_done_date == "2026-04-24"
    assert len(update_calls) == 1
    assert update_calls[0]["run_signal_check_after"] is False
    assert update_calls[0]["schedule_context"]["trigger"] == "scheduled"
    assert update_calls[0]["schedule_context"]["schedule_time"] == "14:40"
    assert not state_mgr3.data_tasks
    assert "定时触发数据更新" in recorder3.logs[-1]

    recorder4 = Recorder()
    config4 = RotationConfig(
        notify_on_signal=False,
        auto_signal_enabled=True,
        auto_execute_enabled=True,
    )
    executed_intents: list[RebalanceIntent] = []

    def execute_rebalance(rebalance_intent: RebalanceIntent):
        executed_intents.append(rebalance_intent)
        return [
            OrderExecutionReport(
                intent=rebalance_intent.order_intents[0],
                accepted=True,
                submitted=True,
                status="submitted",
                message="submitted",
                execution_mode="live",
            )
        ]

    runtime4, _ledger4, _timer4, state_mgr4 = _service(
        config4,
        RotationState(),
        recorder4,
        execute_rebalance_fn=execute_rebalance,
    )
    result4 = runtime4.run_signal_check(
        schedule_context={
            "schedule_time": "14:50",
            "trigger": "scheduled",
            "task_date": "2026-04-24",
        },
    )
    assert result4["executed"] is True
    assert len(executed_intents) == 1
    assert len(result4["execution_reports"]) == 1
    assert state_mgr4.signal_tasks[-1]["status"] == "completed"
    assert any("自动执行已提交" in item for item in recorder4.logs)

    print("rotation_runtime_service_smoketest_ok")


if __name__ == "__main__":
    main()
