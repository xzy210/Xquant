from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    action: str
    position_adjusted: bool = False
    cash_adjusted: bool = False
    qty_before: int = 0
    qty_after: int = 0
    price_before: float = 0.0
    price_after: float = 0.0
    cash_before: float = 0.0
    cash_after: float = 0.0

    def __str__(self) -> str:
        parts = [self.action]
        if self.position_adjusted:
            parts.append(
                f"持仓: {self.qty_before}股@{self.price_before:.3f}"
                f" -> {self.qty_after}股@{self.price_after:.3f}"
            )
        if self.cash_adjusted:
            parts.append(f"资金: {self.cash_before:.2f} -> {self.cash_after:.2f}")
        return " | ".join(parts)


class StartupReconciler:
    """Reconciles persisted state with broker positions after broker becomes ready."""

    def reconcile(self, engine) -> str:
        result = self._reconcile_position_and_cash(engine, source="startup")
        return result.action

    def reconcile_end_of_day(self, engine) -> ReconcileResult:
        """Full reconciliation including dedicated_cash, called during EOD."""
        return self._reconcile_position_and_cash(engine, source="eod")

    def _reconcile_position_and_cash(self, engine, source: str = "") -> ReconcileResult:
        from .trade_executor import SimulatedExecutor
        from .state_manager import CapitalLedgerEntry

        state = engine.state
        executor = engine.executor

        if isinstance(executor, SimulatedExecutor):
            return ReconcileResult(action="no_position")

        if not state.current_holding:
            return ReconcileResult(action="no_position")

        if not executor.is_connected():
            return ReconcileResult(action="broker_disconnected")

        qty, cost = executor.query_position(state.current_holding)

        old_qty = state.buy_quantity
        old_price = state.buy_price
        old_cash = state.dedicated_cash

        if qty <= 0:
            holding_code = state.current_holding or ""
            holding_name = state.current_holding_name or ""
            logger.warning(
                "[%s] 对账发现持仓丢失，清空本地状态: %s",
                source, holding_code,
            )
            if engine.config.use_dedicated_capital and old_qty > 0 and old_price > 0:
                recovered = old_qty * old_price
                state.dedicated_cash = round(state.dedicated_cash + recovered, 2)
                logger.info(
                    "[%s] 持仓丢失，回收估算成本到资金余额: +%.2f -> %.2f",
                    source, recovered, state.dedicated_cash,
                )
            engine.state_mgr.clear_holding()
            result = ReconcileResult(
                action="cleared_missing_position",
                position_adjusted=True,
                qty_before=old_qty, qty_after=0,
                price_before=old_price, price_after=0.0,
                cash_before=old_cash, cash_after=state.dedicated_cash,
                cash_adjusted=abs(state.dedicated_cash - old_cash) > 0.01,
            )
            self._log_cash_entry(
                engine, result, source,
                code_override=holding_code, name_override=holding_name,
            )
            return result

        position_changed = False
        if qty != state.buy_quantity:
            state.buy_quantity = qty
            position_changed = True
        if cost > 0 and abs(cost - state.buy_price) > 1e-6:
            state.buy_price = cost
            position_changed = True

        cash_changed = False
        if engine.config.use_dedicated_capital and position_changed:
            old_cost = old_qty * old_price
            new_cost = qty * (cost if cost > 0 else old_price)
            if abs(old_cost - new_cost) > 0.01:
                adjustment = old_cost - new_cost
                state.dedicated_cash = round(state.dedicated_cash + adjustment, 2)
                cash_changed = True
                logger.info(
                    "[%s] 持仓成本偏差校准: 旧成本=%.2f 新成本=%.2f 调整=%.2f 资金余额=%.2f",
                    source, old_cost, new_cost, adjustment, state.dedicated_cash,
                )

        if position_changed or cash_changed:
            engine.state_mgr.save()

        if not position_changed:
            return ReconcileResult(action="position_consistent")

        result = ReconcileResult(
            action="updated_existing_position",
            position_adjusted=True,
            qty_before=old_qty, qty_after=qty,
            price_before=old_price, price_after=cost if cost > 0 else old_price,
            cash_before=old_cash, cash_after=state.dedicated_cash,
            cash_adjusted=cash_changed,
        )
        self._log_cash_entry(engine, result, source)
        return result

    @staticmethod
    def _log_cash_entry(
        engine,
        result: ReconcileResult,
        source: str,
        *,
        code_override: str = "",
        name_override: str = "",
    ) -> None:
        if not result.cash_adjusted:
            return
        if not engine.config.use_dedicated_capital:
            return
        from .state_manager import CapitalLedgerEntry

        now = datetime.now()
        adjustment = round(result.cash_after - result.cash_before, 2)
        entry = CapitalLedgerEntry(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            action=f"对账校准({source})",
            code=code_override or engine.state.current_holding or "",
            name=name_override or engine.state.current_holding_name or "",
            amount=adjustment,
            commission=0.0,
            balance=result.cash_after,
            fee_source="[对账]",
        )
        engine.state_mgr.add_capital_entry(entry)
