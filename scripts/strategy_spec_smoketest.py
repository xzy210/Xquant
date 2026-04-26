from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from common.execution_contract import OrderExecutionReport, StrategySignal
from trading_app.services.live_strategy_center.strategy_adapter import PanelLiveStrategyAdapter
from trading_app.services.live_strategy_center.strategy_plugin import LiveStrategyPlugin
from trading_app.services.strategy_budget_service import StrategyBudgetService
from trading_app.services.strategy_registry_service import StrategyRegistryService
from trading_app.services.strategy_spec_service import get_strategy_spec_service


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    spec_service = get_strategy_spec_service()
    ai_spec = spec_service.ai_stock()
    etf_spec = spec_service.etf_rotation()
    unmanaged_spec = spec_service.unmanaged()

    _assert(ai_spec.strategy_id, "AI strategy_id should not be empty")
    _assert(ai_spec.virtual_account_id, "AI virtual_account_id should not be empty")
    _assert(etf_spec.strategy_id, "ETF strategy_id should not be empty")
    _assert(etf_spec.virtual_account_id, "ETF virtual_account_id should not be empty")
    _assert(unmanaged_spec.is_unmanaged, "unmanaged spec should be marked as unmanaged")
    _assert(unmanaged_spec.enabled is False, "unmanaged spec should not be tradable")

    with tempfile.TemporaryDirectory(prefix="strategy_spec_smoketest_") as tmpdir:
        tmp = Path(tmpdir)
        budget = StrategyBudgetService(
            config_path=tmp / "strategy_budget_config.json",
            state_path=tmp / "strategy_budget_state.json",
        )
        ai_snapshot = budget.get_strategy_snapshot(
            ai_spec.strategy_id,
            strategy_name=ai_spec.strategy_name,
            virtual_account_id=ai_spec.virtual_account_id,
        )
        etf_snapshot = budget.get_strategy_snapshot(
            etf_spec.strategy_id,
            strategy_name=etf_spec.strategy_name,
            virtual_account_id=etf_spec.virtual_account_id,
        )
        unmanaged_snapshot = budget.get_strategy_snapshot(
            unmanaged_spec.strategy_id,
            strategy_name=unmanaged_spec.strategy_name,
            virtual_account_id=unmanaged_spec.virtual_account_id,
        )
        _assert(ai_snapshot["virtual_account_id"] == ai_spec.virtual_account_id, "AI budget identity mismatch")
        _assert(etf_snapshot["strategy_name"] == etf_spec.strategy_name, "ETF budget identity mismatch")
        _assert(unmanaged_snapshot["is_unmanaged"], "unmanaged budget config mismatch")

        registry = StrategyRegistryService(path=tmp / "strategy_symbol_ownership.json")
        for code in etf_spec.universe:
            owner = registry.get_owner(code)
            _assert(owner is not None, f"{code} should have default ETF ownership")
            _assert(owner.strategy_id == etf_spec.strategy_id, f"{code} owner strategy_id mismatch")
            _assert(owner.virtual_account_id == etf_spec.virtual_account_id, f"{code} owner virtual_account_id mismatch")
            _assert(owner.owner_type == etf_spec.owner_type, f"{code} owner_type mismatch")

    plugin = LiveStrategyPlugin(
        plugin_id=ai_spec.plugin_id,
        plugin_name=ai_spec.plugin_name,
        tab_key=ai_spec.plugin_tab_key,
        tab_title=ai_spec.plugin_tab_title,
        metadata=ai_spec.to_plugin_metadata(),
    )
    _assert(plugin.metadata["strategy_id"] == ai_spec.strategy_id, "plugin metadata strategy_id mismatch")
    _assert(plugin.metadata["virtual_account_id"] == ai_spec.virtual_account_id, "plugin metadata virtual_account_id mismatch")

    class SignalPanel:
        def __init__(self) -> None:
            self.executed: list[StrategySignal] = []

        def generate_live_signals(self, payload=None):
            return [
                StrategySignal(
                    symbol="510880",
                    action="buy",
                    target_quantity=100,
                    price=1.23,
                    reason=str((payload or {}).get("reason", "adapter smoke")),
                )
            ]

        def execute_live_signals(self, signals, *, execution_service=None, stock_name_map=None):
            self.executed = list(signals or [])
            return execution_service.execute_signals(signals, stock_name_map=stock_name_map or {})

    class FakeExecutionService:
        def __init__(self) -> None:
            self.received: list[StrategySignal] = []

        def execute_signals(self, signals, *, stock_name_map=None):
            self.received = list(signals or [])
            return [
                OrderExecutionReport(
                    intent=None,
                    accepted=True,
                    status="submitted",
                    message="adapter smoke executed",
                    execution_mode="live",
                )
            ]

    signal_panel = SignalPanel()
    adapter = PanelLiveStrategyAdapter.from_panel(
        signal_panel,
        strategy_id=etf_spec.strategy_id,
        strategy_name=etf_spec.strategy_name,
        virtual_account_id=etf_spec.virtual_account_id,
    )
    signals = adapter.generate_live_signals({"reason": "center smoke"})
    _assert(len(signals) == 1, "adapter should generate one signal")
    _assert(signals[0].strategy_id == etf_spec.strategy_id, "adapter should inject strategy_id")
    _assert(signals[0].metadata["virtual_account_id"] == etf_spec.virtual_account_id, "adapter should inject virtual account")
    fake_execution = FakeExecutionService()
    reports = adapter.execute_live_signals(signals, execution_service=fake_execution)
    _assert(len(reports) == 1 and reports[0].accepted, "adapter execution report mismatch")
    _assert(fake_execution.received[0].metadata["source"] == "live_strategy_center", "adapter source metadata mismatch")
    _assert(signal_panel.executed[0].metadata["trigger"] == "strategy_center", "adapter should delegate panel execution")

    print("STRATEGY_SPEC_SMOKE= ok")


if __name__ == "__main__":
    main()
