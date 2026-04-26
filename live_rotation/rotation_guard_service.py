"""
ETF rotation guard service.

This module owns pre-decision protections and rebalance-period filtering for the
live ETF rotation workflow. It intentionally avoids Qt dependencies so the same
rules can later be reused by backtest or other strategy runners.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Tuple

from .config import RotationConfig
from .state_manager import RotationState


@dataclass(frozen=True)
class GuardSignal:
    """A guard-produced signal result."""

    signal: str
    reason: str
    executed: bool = False

    def to_result(self) -> dict:
        return {
            "signal": self.signal,
            "reason": self.reason,
            "executed": self.executed,
        }


class RotationGuardService:
    """Evaluate account protection, trailing stop, and rebalance guards."""

    def __init__(
        self,
        *,
        config: RotationConfig,
        state: RotationState,
        state_saver: Callable[[], None],
        total_asset_fn: Callable[[], float],
        current_price_fn: Callable[[str], float],
        logger_fn: Optional[Callable[[str], None]] = None,
        code_name_fn: Optional[Callable[[str], str]] = None,
        now_fn: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.config = config
        self.state = state
        self.state_saver = state_saver
        self.total_asset_fn = total_asset_fn
        self.current_price_fn = current_price_fn
        self.logger_fn = logger_fn or (lambda message: None)
        self.code_name_fn = code_name_fn or (lambda code: code)
        self.now_fn = now_fn

    def update_context(self, *, config: RotationConfig, state: RotationState) -> None:
        """Refresh mutable config/state references."""
        self.config = config
        self.state = state

    def in_drawdown_cooldown(self) -> bool:
        """Check drawdown cooldown and decrement it at most once per day."""
        if not self.config.enable_drawdown_protection:
            return False
        if self.state.cooldown_remaining <= 0:
            return False

        today = self.now_fn().strftime("%Y-%m-%d")
        if self.state.cooldown_last_decrement_date != today:
            self.state.cooldown_last_decrement_date = today
            self.state.cooldown_remaining -= 1

            if self.state.cooldown_remaining <= 0:
                self.state.cooldown_remaining = 0
                total = self.total_asset_fn()
                if total > 0:
                    self.state.account_peak = total
                self.logger_fn("✅ 回撤保护冷却期结束，账户峰值重置，恢复交易")
                self.state_saver()
                return False

            self.state_saver()

        return self.state.cooldown_remaining > 0

    def check_drawdown_protection(self) -> Tuple[bool, dict]:
        """Check max account drawdown protection and return a pure signal result."""
        if not self.config.enable_drawdown_protection:
            return False, {}
        if not self.state.current_holding:
            return False, {}

        total = self.total_asset_fn()
        if total <= 0:
            return False, {}

        if self.state.account_peak <= 0:
            self.state.account_peak = total
            self.state_saver()
            return False, {}

        if total > self.state.account_peak:
            self.state.account_peak = total
            self.state_saver()

        drawdown = (self.state.account_peak - total) / self.state.account_peak
        if drawdown < self.config.max_drawdown_pct:
            return False, {}

        reason = (
            f"账户回撤保护: 回撤 {drawdown * 100:.1f}% >= "
            f"{self.config.max_drawdown_pct * 100:.0f}%, "
            f"峰值={self.state.account_peak:,.0f}, "
            f"当前={total:,.0f}"
        )
        self.logger_fn(f"🔴 {reason}")

        self.state.cooldown_remaining = self.config.drawdown_cooldown_days
        self.state.cooldown_last_decrement_date = ""
        self.state_saver()
        self.logger_fn(f"⏸ 进入冷却期 {self.config.drawdown_cooldown_days} 天")

        return True, GuardSignal("DRAWDOWN_STOP", reason, False).to_result()

    def check_trailing_stop(self) -> Tuple[bool, dict]:
        """Check trailing stop and return a pure signal result."""
        if not self.config.enable_trailing_stop:
            return False, {}
        if not self.state.current_holding:
            return False, {}

        price = self.current_price_fn(self.state.current_holding)
        if price <= 0:
            return False, {}

        if price > self.state.holding_high_price:
            self.state.holding_high_price = price
            self.state_saver()

        if self.state.holding_high_price <= 0:
            return False, {}

        drop = (self.state.holding_high_price - price) / self.state.holding_high_price
        if drop < self.config.trailing_stop_pct:
            return False, {}

        reason = (
            f"移动止盈: {self.code_name_fn(self.state.current_holding)} "
            f"从最高价 {self.state.holding_high_price:.3f} "
            f"回撤 {drop * 100:.1f}% >= "
            f"{self.config.trailing_stop_pct * 100:.0f}%"
        )
        self.logger_fn(f"🟡 {reason}")

        return True, GuardSignal("TRAILING_STOP", reason, False).to_result()

    def is_rebalance_day(self) -> bool:
        """Check whether current check count satisfies the rebalance period."""
        period = max(1, self.config.rebalance_period)
        if period <= 1:
            return True
        return self.state.check_count % period == 0

    def update_check_count(self) -> None:
        """Increment signal-check count once per trading day."""
        today = self.now_fn().strftime("%Y-%m-%d")
        if self.state.last_check_date != today:
            self.state.check_count += 1

    def filter_rebalance_signal(
        self,
        signal: str,
        target: Optional[str],
        reason: str,
    ) -> Tuple[str, Optional[str], str, bool]:
        """Apply rebalance-period filtering to actionable rebalance signals."""
        if signal not in ("SWITCH", "BUY") or self.is_rebalance_day():
            return signal, target, reason, False

        original = signal
        filtered_reason = (
            f"非调仓日（周期={self.config.rebalance_period}天），"
            f"原信号={original}，暂不执行"
        )
        return "HOLD", target, filtered_reason, True
