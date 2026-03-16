from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class StartupReconciler:
    """Reconciles persisted state with broker positions after broker becomes ready."""

    def reconcile(self, engine):
        state = engine.state
        executor = engine.executor
        if state.current_holding:
            qty, cost = executor.query_position(state.current_holding)
            if qty <= 0:
                logger.warning("启动对账发现持仓丢失，清空本地状态: %s", state.current_holding)
                engine.state_mgr.clear_holding()
                return "cleared_missing_position"
            if qty != state.buy_quantity or (cost and abs(cost - state.buy_price) > 1e-6):
                state.buy_quantity = qty
                if cost > 0:
                    state.buy_price = cost
                engine.state_mgr.save()
                return "updated_existing_position"
            return "position_consistent"
        return "no_position"
