from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

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

    def check_drawdown_protection(self, auto_execute: bool):
        return False, {}

    def check_trailing_stop(self, auto_execute: bool):
        return False, {}

    def update_check_count(self) -> None:
        self.check_count_updates += 1

    def filter_rebalance_signal(self, signal: str, target: str | None, reason: str):
        return signal, target, reason, False


class FakeExecutionService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, dict, str]] = []

    def update_context(self, **kwargs) -> None:
        pass

    def execute_signal(self, signal: str, target: str | None, scores: dict, reason: str) -> dict:
        self.calls.append((signal, target, scores.copy(), reason))
        return {"success": True, "trades": []}


class FakeLedgerService:
    def __init__(self) -> None:
        self.snapshots = 0

    def record_daily_equity(self) -> None:
        self.snapshots += 1


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


def _service(config: RotationConfig, state: RotationState, recorder: Recorder) -> tuple[RotationRuntimeService, FakeExecutionService, FakeLedgerService, FakeTimer, FakeStateManager]:
    state_mgr = FakeStateManager(state)
    config_mgr = FakeConfigManager()
    execution = FakeExecutionService()
    ledger = FakeLedgerService()
    timer = FakeTimer()
    runtime = RotationRuntimeService(
        config=config,
        state=state,
        state_mgr=state_mgr,
        config_mgr=config_mgr,
        executor=SimulatedExecutor(),
        data_dir=PROJECT_ROOT / "live_rotation" / "data",
        signal_service=FakeSignalService({"510880": 1.2, "159949": 0.5}),
        decision_service=FakeDecisionService(("BUY", "510880", "smoke buy")),
        guard_service=FakeGuardService(),
        execution_service=execution,
        ledger_service=ledger,
        auto_timer=timer,
        logger_fn=recorder.logs.append,
        status_fn=recorder.statuses.append,
        signal_fn=lambda signal, result: recorder.signals.append((signal, result.copy())),
        scores_fn=lambda scores: recorder.scores.append(scores.copy()),
        notify_signal_fn=lambda *args: recorder.notifications.append(args),
        now_fn=lambda: datetime(2026, 4, 24, 14, 50),
        trading_day_fn=lambda value: True,
    )
    runtime.check_live_market_data_ready = lambda **kwargs: (True, "ok")
    return runtime, execution, ledger, timer, state_mgr


def main() -> None:
    recorder = Recorder()
    config = RotationConfig(notify_on_signal=True)
    state = RotationState()
    runtime, execution, ledger, timer, state_mgr = _service(config, state, recorder)

    result = runtime.run_signal_check(
        auto_execute=True,
        schedule_context={
            "schedule_time": "14:50",
            "trigger": "smoke",
            "task_date": "2026-04-24",
        },
    )
    assert result["signal"] == "BUY"
    assert result["target"] == "510880"
    assert result["executed"] is True
    assert result["trade_result"]["success"] is True
    assert execution.calls[0][0] == "BUY"
    assert state_mgr.signal_tasks[-1]["status"] == "completed"
    assert result["strategy_signals"][0]["schema_version"] == "strategy_signal.v1"
    assert result["strategy_signals"][0]["metadata"]["virtual_account_id"]

    blocked_status = SimpleNamespace(can_run_live_strategy=False, summary="stale")

    runtime.start_auto()
    assert config.auto_enabled is True
    assert timer.started == [30_000]
    runtime.stop_auto()
    assert config.auto_enabled is False
    assert timer.stopped == 1

    recorder2 = Recorder()
    config2 = RotationConfig()
    runtime2, execution2, ledger2, _timer2, _state_mgr2 = _service(config2, RotationState(), recorder2)
    runtime2.check_live_market_data_ready = lambda **kwargs: (False, "missing bars")
    blocked = runtime2.run_signal_check(auto_execute=True)
    assert blocked["signal"] == "BLOCKED"
    assert not execution2.calls
    assert ledger2.snapshots == 0
    assert "行情数据未就绪" in blocked["reason"]

    print("rotation_runtime_service_smoketest_ok")


if __name__ == "__main__":
    main()
