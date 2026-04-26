from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from live_rotation.config import RotationConfig
from live_rotation.rotation_signal_service import RotationDecisionService
from live_rotation.state_manager import RotationState


def _service(config: RotationConfig, state: RotationState) -> RotationDecisionService:
    return RotationDecisionService(
        config=config,
        state=state,
        code_name_fn=lambda code: f"ETF-{code}",
    )


def main() -> None:
    config = RotationConfig(
        enable_empty_position=True,
        empty_threshold=-0.5,
        rebalance_threshold=1.5,
    )

    signal, target, reason = _service(config, RotationState()).make_decision(
        {"510880": 1.2, "159949": 0.3}
    )
    assert signal == "BUY"
    assert target == "510880"
    assert "初始建仓" in reason

    state = RotationState(current_holding="159949")
    signal, target, reason = _service(config, state).make_decision(
        {"510880": 2.0, "159949": 1.0}
    )
    assert signal == "SWITCH"
    assert target == "510880"
    assert "ETF-510880" in reason

    state = RotationState(current_holding="159949")
    signal, target, reason = _service(config, state).make_decision(
        {"510880": -0.8, "159949": -0.7}
    )
    assert signal == "SELL_ALL"
    assert target is None
    assert "低于阈值" in reason

    signal, target, reason = _service(config, RotationState()).make_decision(
        {"510880": -0.8, "159949": -0.7}
    )
    assert signal == "NO_ACTION"
    assert target is None
    assert "空仓中" in reason

    config.enable_empty_position = False
    state = RotationState(current_holding="159949")
    signal, target, reason = _service(config, state).make_decision(
        {"510880": -0.1, "159949": -0.2}
    )
    assert signal == "HOLD"
    assert target is None
    assert "负分区" in reason

    print("rotation_signal_service_smoketest_ok")


if __name__ == "__main__":
    main()
