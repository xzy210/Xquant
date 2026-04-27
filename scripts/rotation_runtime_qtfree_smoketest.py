from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from live_rotation.config import RotationConfig
from live_rotation.rotation_runtime_service import RotationRuntimeService
from live_rotation.state_manager import RotationState
from live_rotation.trade_executor import SimulatedExecutor


class FakeStateManager:
    def __init__(self, state: RotationState) -> None:
        self.state = state
        self.data_tasks: list[dict] = []
        self.signal_tasks: list[dict] = []
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
        self.check_results.append((signal, dict(scores)))


class FakeConfigManager:
    def save(self, config: RotationConfig) -> None:
        pass


class FakeService:
    def update_context(self, **kwargs) -> None:
        pass


class FakeSignalService(FakeService):
    def calculate_scores(self) -> dict[str, float]:
        return {"510880": 1.0, "159949": 0.5}


class FakeDecisionService(FakeService):
    def make_decision(self, scores: dict[str, float]):
        return "BUY", "510880", "qt-free smoke"


class FakeGuardService(FakeService):
    def in_drawdown_cooldown(self) -> bool:
        return False

    def check_drawdown_protection(self):
        return False, {}

    def check_trailing_stop(self):
        return False, {}

    def update_check_count(self) -> None:
        pass

    def filter_rebalance_signal(self, signal: str, target: str | None, reason: str):
        return signal, target, reason, False


class FakeLedgerService(FakeService):
    def available_cash(self) -> float:
        return 100000.0

    def total_asset(self) -> float:
        return 100000.0

    def record_daily_equity(self) -> None:
        pass


class FakeDataService:
    def __init__(self) -> None:
        self.updated = threading.Event()

    def update_context(self, **kwargs) -> None:
        pass

    def get_data_version(self, codes):
        return type("Audit", (), {"to_dict": lambda self: {"data_version": "qtfree-smoke", "symbols": list(codes)}})()

    def update_pool(self, codes, progress_cb=None):
        if progress_cb:
            progress_cb(1, len(codes), codes[0], "ok")
        self.updated.set()
        return len(codes), len(codes), []

    def is_pool_fresh(self, codes) -> bool:
        return True


class Recorder:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.statuses: list[str] = []
        self.signals: list[tuple[str, dict]] = []
        self.scores: list[dict] = []


def main() -> None:
    recorder = Recorder()
    state = RotationState()
    data_service = FakeDataService()
    config = RotationConfig(etf_pool=["510880", "159949"], auto_enabled=False)
    runtime = RotationRuntimeService(
        config=config,
        state=state,
        state_mgr=FakeStateManager(state),
        config_mgr=FakeConfigManager(),
        executor=SimulatedExecutor(),
        data_dir=PROJECT_ROOT / "data",
        signal_service=FakeSignalService(),
        decision_service=FakeDecisionService(),
        guard_service=FakeGuardService(),
        ledger_service=FakeLedgerService(),
        data_service=data_service,
        logger_fn=recorder.logs.append,
        status_fn=recorder.statuses.append,
        signal_fn=lambda signal, result: recorder.signals.append((signal, dict(result))),
        scores_fn=lambda scores: recorder.scores.append(dict(scores)),
        now_fn=lambda: datetime(2026, 4, 24, 14, 50),
        trading_day_fn=lambda value: True,
    )
    runtime.check_live_market_data_ready = lambda **kwargs: (True, "ok")

    result = runtime.run_signal_check()
    assert result["signal"] == "BUY"
    assert result["data_version"] == "qtfree-smoke"
    assert any("数据版本" in item for item in recorder.logs)

    runtime.auto_check_interval = 100
    runtime.start_auto()
    assert runtime._auto_running
    time.sleep(0.15)
    runtime.stop_auto()
    assert not runtime._auto_running

    runtime.update_data()
    assert data_service.updated.wait(2.0)
    for _ in range(20):
        if not runtime.is_update_running():
            break
        time.sleep(0.05)
    assert not runtime.is_update_running()

    print("rotation_runtime_qtfree_smoketest_ok")


if __name__ == "__main__":
    main()
