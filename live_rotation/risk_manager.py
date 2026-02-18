"""
ETF轮动实盘 - 风控模块

交易前检查：交易时段、冷却期、最大交易次数、亏损限制等。
"""
import logging
from datetime import datetime, time as dtime
from typing import Optional, Tuple

from .config import RotationConfig
from .state_manager import RotationState

logger = logging.getLogger(__name__)


class RiskManager:
    """轮动策略风控管理"""

    def __init__(self, config: RotationConfig):
        self.config = config

    def update_config(self, config: RotationConfig):
        self.config = config

    def pre_trade_check(self, state: RotationState,
                        action: str,
                        current_price: float = 0.0) -> Tuple[bool, str]:
        """
        交易前风控检查

        Args:
            state: 当前策略状态
            action: 交易动作 (BUY / SELL / SWITCH)
            current_price: 当前价格（用于亏损检查）

        Returns:
            (通过, 原因)
        """
        if not self.config.enable_risk_check:
            return True, "风控已禁用"

        # 1. 交易时段检查
        ok, msg = self._check_trading_time()
        if not ok:
            return False, msg

        # 2. 每日交易次数检查
        ok, msg = self._check_daily_trades(state)
        if not ok:
            return False, msg

        # 3. 最少持有天数检查（仅对卖出有效）
        if action in ("SELL", "SWITCH", "SELL_ALL"):
            ok, msg = self._check_hold_days(state)
            if not ok:
                return False, msg

        # 4. 单笔亏损检查（仅对卖出有效）
        if action in ("SELL", "SWITCH", "SELL_ALL") and current_price > 0:
            ok, msg = self._check_loss_limit(state, current_price)
            if not ok:
                logger.warning(f"亏损超限警告: {msg}（仍允许执行止损）")
                # 亏损超限不阻止交易，但记录警告

        return True, "风控检查通过"

    def _check_trading_time(self) -> Tuple[bool, str]:
        """检查是否在交易时段"""
        now = datetime.now()
        weekday = now.weekday()
        if weekday >= 5:
            return False, f"非交易日（周{weekday + 1}）"

        try:
            h, m = map(int, self.config.trading_start.split(":"))
            start = dtime(h, m)
            h, m = map(int, self.config.trading_end.split(":"))
            end = dtime(h, m)
        except ValueError:
            return True, "交易时间配置异常，跳过检查"

        current = now.time()

        # 午休时段 11:30-13:00
        lunch_start = dtime(11, 30)
        lunch_end = dtime(13, 0)

        if current < start:
            return False, f"未到开盘时间（{self.config.trading_start}）"
        if current > end:
            return False, f"已过交易截止时间（{self.config.trading_end}）"
        if lunch_start <= current <= lunch_end:
            return False, "午休时段（11:30-13:00）"

        return True, "在交易时段内"

    def _check_daily_trades(self, state: RotationState) -> Tuple[bool, str]:
        """检查每日交易次数"""
        today_count = state.get_trades_today()
        if today_count >= self.config.max_trades_per_day:
            return False, f"今日交易次数已达上限（{today_count}/{self.config.max_trades_per_day}）"
        return True, "交易次数正常"

    def _check_hold_days(self, state: RotationState) -> Tuple[bool, str]:
        """检查最少持有天数"""
        if self.config.min_hold_days <= 0:
            return True, "无持有限制"

        if not state.buy_date:
            return True, "无持仓记录"

        try:
            buy_dt = datetime.strptime(state.buy_date, "%Y-%m-%d")
            hold_days = (datetime.now() - buy_dt).days
            if hold_days < self.config.min_hold_days:
                return False, (f"持有天数不足"
                               f"（{hold_days}/{self.config.min_hold_days}天）")
        except ValueError:
            pass

        return True, "持有天数满足"

    def _check_loss_limit(self, state: RotationState,
                          current_price: float) -> Tuple[bool, str]:
        """检查亏损是否超限"""
        if state.buy_price <= 0:
            return True, "无买入价记录"

        loss_pct = (current_price - state.buy_price) / state.buy_price * 100
        if loss_pct < -self.config.max_single_loss_pct:
            return True, (f"亏损 {loss_pct:.2f}% 超过 "
                          f"-{self.config.max_single_loss_pct}%（允许止损卖出）")
        return True, "亏损在限制范围内"

    def is_trading_time(self) -> bool:
        """快速判断是否在交易时段"""
        ok, _ = self._check_trading_time()
        return ok
