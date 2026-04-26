from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from live_rotation.config import RotationConfig
from live_rotation.rotation_guard_service import RotationGuardService
from live_rotation.state_manager import RotationState


class Recorder:
    def __init__(self) -> None:
        self.saved = 0
        self.sold_reasons: list[str] = []
        self.logs: list[str] = []
        self.price = 0.0
        self.total_asset = 0.0
        self.now = datetime(2026, 4, 26, 14, 30)

    def save(self) -> None:
        self.saved += 1

    def sell_all(self, reason: str) -> None:
        self.sold_reasons.append(reason)


def _service(config: RotationConfig, state: RotationState, recorder: Recorder) -> RotationGuardService:
    return RotationGuardService(
        config=config,
        state=state,
        state_saver=recorder.save,
        total_asset_fn=lambda: recorder.total_asset,
        current_price_fn=lambda code: recorder.price,
        sell_all_fn=recorder.sell_all,
        logger_fn=recorder.logs.append,
        code_name_fn=lambda code: f"ETF-{code}",
        now_fn=lambda: recorder.now,
    )


def main() -> None:
    config = RotationConfig(
        enable_drawdown_protection=True,
        max_drawdown_pct=0.1,
        drawdown_cooldown_days=3,
        enable_trailing_stop=True,
        trailing_stop_pct=0.08,
        rebalance_period=3,
    )

    recorder = Recorder()
    recorder.total_asset = 100_000.0
    state = RotationState(current_holding="510880", account_peak=0.0)
    service = _service(config, state, recorder)
    triggered, result = service.check_drawdown_protection(auto_execute=False)
    assert not triggered
    assert result == {}
    assert state.account_peak == 100_000.0
    assert recorder.saved == 1

    recorder.total_asset = 88_000.0
    triggered, result = service.check_drawdown_protection(auto_execute=True)
    assert triggered
    assert result["signal"] == "DRAWDOWN_STOP"
    assert result["executed"] is True
    assert state.cooldown_remaining == 3
    assert len(recorder.sold_reasons) == 1

    recorder.now = datetime(2026, 4, 27, 14, 30)
    assert service.in_drawdown_cooldown() is True
    assert state.cooldown_remaining == 2
    assert state.cooldown_last_decrement_date == "2026-04-27"

    recorder = Recorder()
    state = RotationState(current_holding="159949", holding_high_price=10.0)
    service = _service(config, state, recorder)
    recorder.price = 9.5
    triggered, result = service.check_trailing_stop(auto_execute=False)
    assert not triggered
    assert result == {}

    recorder.price = 9.0
    triggered, result = service.check_trailing_stop(auto_execute=True)
    assert triggered
    assert result["signal"] == "TRAILING_STOP"
    assert result["executed"] is True
    assert "ETF-159949" in result["reason"]
    assert len(recorder.sold_reasons) == 1

    state = RotationState(check_count=2)
    service = _service(config, state, Recorder())
    assert service.is_rebalance_day() is False
    signal, target, reason, filtered = service.filter_rebalance_signal(
        "SWITCH",
        "510880",
        "candidate wins",
    )
    assert filtered is True
    assert signal == "HOLD"
    assert target == "510880"
    assert "非调仓日" in reason

    state.check_count = 3
    signal, target, reason, filtered = service.filter_rebalance_signal(
        "BUY",
        "510880",
        "candidate wins",
    )
    assert filtered is False
    assert signal == "BUY"
    assert target == "510880"
    assert reason == "candidate wins"

    print("rotation_guard_service_smoketest_ok")


if __name__ == "__main__":
    main()
