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
    cash_adjusted: bool = False  # 保留字段兼容历史调用方，现金由主账本维护，始终为 False
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
        return " | ".join(parts)


class StartupReconciler:
    """Reconciles persisted state with broker positions after broker becomes ready.

    注意：现金余额统一由 ``StrategyBudgetService`` 主账本维护（commit_buy/sell 实时扣加），
    本对账器只负责对齐 **持仓数量 / 持仓成本**，不再写 dedicated_cash。
    """

    def reconcile(self, engine) -> str:
        result = self._reconcile_position(engine, source="startup")
        return result.action

    def reconcile_end_of_day(self, engine) -> ReconcileResult:
        return self._reconcile_position(engine, source="eod")

    def _reconcile_position(self, engine, source: str = "") -> ReconcileResult:
        from .trade_executor import SimulatedExecutor

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

        if qty <= 0:
            holding_code = state.current_holding or ""
            logger.warning(
                "[%s] 对账发现持仓丢失，清空本地状态: %s",
                source, holding_code,
            )
            engine.state_mgr.clear_holding()
            return ReconcileResult(
                action="cleared_missing_position",
                position_adjusted=True,
                qty_before=old_qty, qty_after=0,
                price_before=old_price, price_after=0.0,
            )

        position_changed = False
        if qty != state.buy_quantity:
            state.buy_quantity = qty
            position_changed = True
        if cost > 0 and abs(cost - state.buy_price) > 1e-6:
            state.buy_price = cost
            position_changed = True

        if position_changed:
            engine.state_mgr.save()

        if not position_changed:
            return ReconcileResult(action="position_consistent")

        return ReconcileResult(
            action="updated_existing_position",
            position_adjusted=True,
            qty_before=old_qty, qty_after=qty,
            price_before=old_price, price_after=cost if cost > 0 else old_price,
        )
