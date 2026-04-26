"""
ETF rotation status and statistics aggregation service.

This module owns read-only presentation aggregation for current status and
performance statistics. It intentionally avoids trading, ledger mutation, and
runtime scheduling responsibilities.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .config import RotationConfig
from .rotation_ledger_service import RotationLedgerService
from .state_manager import RotationState
from .trade_executor import TradeExecutor


class RotationStatusService:
    """Build read-only status and statistics payloads for UI and strategy center."""

    def __init__(
        self,
        *,
        config: RotationConfig,
        state: RotationState,
        executor: TradeExecutor,
        ledger_service: RotationLedgerService,
        data_dir: Path,
        data_fresh_fn: Callable[[], bool],
        now_fn: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.config = config
        self.state = state
        self.executor = executor
        self.ledger_service = ledger_service
        self.data_dir = Path(data_dir)
        self.data_fresh_fn = data_fresh_fn
        self.now_fn = now_fn

    def update_context(
        self,
        *,
        config: RotationConfig,
        state: RotationState,
        executor: TradeExecutor,
        data_dir: Optional[Path] = None,
    ) -> None:
        """Refresh mutable config, state, executor, and optional data directory."""
        self.config = config
        self.state = state
        self.executor = executor
        if data_dir is not None:
            self.data_dir = Path(data_dir)

    def get_status_summary(self) -> dict:
        """Return current strategy status summary."""
        state = self.state
        current_price = 0.0
        unrealized_pnl = 0.0
        price_is_realtime = False
        if state.current_holding:
            current_price = self.executor.get_current_price(state.current_holding)
            if current_price > 0:
                price_is_realtime = True
            else:
                current_price = state.buy_price
            if current_price > 0 and state.buy_price > 0:
                unrealized_pnl = (current_price - state.buy_price) * state.buy_quantity

        return {
            "holding": state.current_holding,
            "holding_name": state.current_holding_name,
            "buy_price": state.buy_price,
            "buy_date": state.buy_date,
            "buy_quantity": state.buy_quantity,
            "current_price": current_price,
            "price_is_realtime": price_is_realtime,
            "unrealized_pnl": unrealized_pnl,
            "last_signal": state.last_signal,
            "last_check": f"{state.last_check_date} {state.last_check_time}",
            "last_scores": state.last_scores,
            "trades_today": state.get_trades_today(),
            "auto_enabled": self.config.auto_enabled,
            "executor_connected": self.executor.is_connected(),
            "cooldown_remaining": state.cooldown_remaining,
            "holding_high_price": state.holding_high_price,
            "data_fresh": self.data_fresh_fn(),
            "data_dir": str(self.data_dir),
            "dedicated_cash": round(self.ledger_service.ledger_available_cash(), 2),
            "use_dedicated_capital": self.config.use_dedicated_capital,
            "dedicated_capital": self.config.dedicated_capital,
        }

    def get_statistics(self) -> dict:
        """Calculate live performance statistics from state trade history."""
        history = self.state.trade_history
        sell_records = [
            record
            for record in history
            if record.get("action") in ("SELL", "SELL_ALL") and record.get("success", True)
        ]

        total_trades = len(sell_records)
        win_trades = sum(1 for record in sell_records if record.get("pnl", 0) > 0)
        loss_trades = sum(1 for record in sell_records if record.get("pnl", 0) < 0)
        win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0.0
        total_trade_pnl = sum(record.get("pnl", 0) for record in sell_records)
        avg_pnl = total_trade_pnl / total_trades if total_trades > 0 else 0.0
        best_trade = max((record.get("pnl", 0) for record in sell_records), default=0.0)
        worst_trade = min((record.get("pnl", 0) for record in sell_records), default=0.0)

        hold_days_list = []
        for sell in sell_records:
            code, sell_date = sell.get("code", ""), sell.get("date", "")
            if code and sell_date:
                for record in reversed(history):
                    if (
                        record.get("action") == "BUY"
                        and record.get("code") == code
                        and record.get("date", "") <= sell_date
                    ):
                        try:
                            buy_date = datetime.strptime(record["date"], "%Y-%m-%d")
                            sell_day = datetime.strptime(sell_date, "%Y-%m-%d")
                            hold_days_list.append((sell_day - buy_date).days)
                        except Exception:
                            pass
                        break
        avg_hold_days = (
            sum(hold_days_list) / len(hold_days_list) if hold_days_list else 0.0
        )

        current_hold_days = 0
        if self.state.buy_date:
            try:
                buy_date = datetime.strptime(self.state.buy_date, "%Y-%m-%d")
                current_hold_days = (self.now_fn() - buy_date).days
            except Exception:
                pass

        equity_values = [value for _, value in sorted(self.state.daily_equity.items())]
        max_drawdown = 0.0
        if len(equity_values) > 1:
            peak = equity_values[0]
            for value in equity_values[1:]:
                if value > peak:
                    peak = value
                if peak > 0:
                    max_drawdown = max(max_drawdown, (peak - value) / peak)

        current_equity = self.ledger_service.ledger_available_cash()
        if self.state.current_holding and self.state.buy_quantity > 0:
            current_price = self.executor.get_current_price(self.state.current_holding)
            if current_price > 0:
                current_equity += current_price * self.state.buy_quantity
            else:
                today = self.now_fn().strftime("%Y-%m-%d")
                today_snapshot = self.state.daily_equity.get(today, 0)
                if today_snapshot > 0:
                    current_equity = today_snapshot
                elif self.state.buy_price > 0:
                    current_equity += self.state.buy_price * self.state.buy_quantity

        initial_capital = self.config.dedicated_capital
        total_return_pct = (
            (current_equity - initial_capital) / initial_capital * 100
            if initial_capital > 0
            else 0.0
        )

        return {
            "total_trades": total_trades,
            "win_trades": win_trades,
            "loss_trades": loss_trades,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "total_pnl": self.state.total_pnl,
            "total_return_pct": total_return_pct,
            "current_equity": current_equity,
            "initial_capital": initial_capital,
            "max_drawdown": max_drawdown * 100,
            "avg_hold_days": avg_hold_days,
            "current_hold_days": current_hold_days,
        }
