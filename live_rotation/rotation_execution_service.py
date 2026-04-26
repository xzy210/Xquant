"""
ETF rotation execution service.

This module owns order execution, fill reconciliation, and live rotation state
updates. UI signals and notifications remain in RotationEngine via callbacks so
this service can be reused by non-Qt runners later.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

from .config import RotationConfig
from .rotation_ledger_service import RotationLedgerService
from .state_manager import RotationState, StateManager, TradeRecord
from .trade_executor import TradeExecutor


class RotationExecutionService:
    """Execute ETF rotation signals and update rotation state."""

    def __init__(
        self,
        *,
        config: RotationConfig,
        state: RotationState,
        state_mgr: StateManager,
        executor: TradeExecutor,
        ledger_service: RotationLedgerService,
        ensure_price_fn: Callable[[str], float],
        preflight_risk_fn: Callable[..., Tuple[bool, str]],
        confirm_fill_fn: Callable[[int, int, float], dict],
        trade_event_fn: Callable[[bool, dict], None],
        partial_switch_stop_fn: Callable[[dict, int, str, str], None],
        logger_fn: Optional[Callable[[str], None]] = None,
        code_name_fn: Optional[Callable[[str], str]] = None,
        code_name_map_fn: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.config = config
        self.state = state
        self.state_mgr = state_mgr
        self.executor = executor
        self.ledger_service = ledger_service
        self.ensure_price_fn = ensure_price_fn
        self.preflight_risk_fn = preflight_risk_fn
        self.confirm_fill_fn = confirm_fill_fn
        self.trade_event_fn = trade_event_fn
        self.partial_switch_stop_fn = partial_switch_stop_fn
        self.logger_fn = logger_fn or (lambda message: None)
        self.code_name_fn = code_name_fn or (lambda code: code)
        self.code_name_map_fn = code_name_map_fn or (lambda code: "")

    def update_context(
        self,
        *,
        config: RotationConfig,
        state: RotationState,
        executor: TradeExecutor,
    ) -> None:
        """Refresh mutable config, state, and executor references."""
        self.config = config
        self.state = state
        self.executor = executor
        self.ledger_service.update_context(
            config=self.config,
            state=self.state,
            executor=self.executor,
        )

    def execute_signal(
        self,
        signal: str,
        target: Optional[str],
        scores: Dict[str, float],
        reason: str,
    ) -> dict:
        """Execute a rotation decision signal."""
        result = {'success': False, 'trades': []}

        if signal == "SELL_ALL":
            if self.state.current_holding:
                trade = self.sell_all(reason=reason)
                result['trades'].append(trade)
                result['success'] = trade.get('success', False)

        elif signal == "SWITCH":
            if self.state.current_holding:
                sell_result = self.sell_all(reason=f"轮动切换: {reason}")
                result['trades'].append(sell_result)

                if not sell_result.get('success', False):
                    result['success'] = False
                    result['reason'] = f"轮动中止: 卖出失败 - {sell_result.get('message', '')}"
                    self.logger_fn("⚠ 轮动切换中止: 卖出失败，持仓保持不变")
                    return result

                if sell_result.get('partial_fill', False):
                    remaining = int(sell_result.get('remaining', 0) or 0)
                    message = (
                        f"卖出部分成交（剩余 {remaining} 股），"
                        f"已中止轮动切换，请确认持仓后再执行"
                    )
                    self.logger_fn(f"⚠ {message}")
                    result['success'] = False
                    result['reason'] = message
                    self.partial_switch_stop_fn(sell_result, remaining, message, reason)
                    return result

            if target:
                buy_amount = self.ledger_service.available_cash()
                buy_result = self.buy(target, buy_amount, reason=f"轮动买入: {reason}")
                result['trades'].append(buy_result)
                result['success'] = buy_result.get('success', False)

                if buy_result.get('success'):
                    self.state.current_score = scores.get(target, 0)
                    self.state_mgr.save()

        elif signal == "BUY":
            if target:
                buy_amount = self.ledger_service.available_cash()
                buy_result = self.buy(target, buy_amount, reason=f"建仓买入: {reason}")
                result['trades'].append(buy_result)
                result['success'] = buy_result.get('success', False)

                if buy_result.get('success'):
                    self.state.current_score = scores.get(target, 0)
                    self.state_mgr.save()

        return result

    def buy(
        self,
        code: str,
        amount: float,
        reason: str = "",
        price: Optional[float] = None,
    ) -> dict:
        """Execute a buy order and update rotation state."""
        result = {'success': False, 'action': 'BUY', 'code': code, 'message': ''}

        ok, msg = self.preflight_risk_fn(is_sell=False)
        if not ok:
            result['message'] = f"风控: {msg}"
            self.logger_fn(f"⚠ 买入被风控拦截: {msg}")
            return result

        buy_amount = amount * self.config.cash_ratio
        if buy_amount < self.config.min_trade_amount:
            result['message'] = f"金额过小 ({buy_amount:.2f})"
            self.logger_fn(f"⚠ 买入金额不足: {buy_amount:.2f}")
            return result

        self.ensure_price_fn(code)

        order_price = float(price) if price is not None and float(price) > 0 else None
        success, message, order_id, price, qty = self.executor.buy(
            code,
            buy_amount,
            price=order_price,
        )
        result['success'] = success
        result['message'] = message
        result['order_id'] = order_id
        result['price'] = price
        result['quantity'] = qty
        result['reason'] = reason

        name = self.code_name_map_fn(code)
        now = datetime.now()

        if success:
            self.ledger_service.add_order_record(order_id, "买入", code, qty, price, reason)
            fill = self.confirm_fill_fn(order_id, qty, price)

            actual_qty = fill['filled_qty'] if fill['filled_qty'] > 0 else qty
            actual_price = fill['filled_price'] if fill['filled_price'] > 0 else price
            actual_cost = actual_price * actual_qty

            fee_info = self.ledger_service.resolve_trade_fees(
                direction="buy",
                amount=actual_cost,
                stock_code=code,
                actual_commission=float(fill.get("commission", -1.0) or -1.0),
            )
            buy_commission = float(fee_info.get("commission", 0.0) or 0.0)
            total_fee = float(fee_info.get("total_fee", 0.0) or 0.0)
            fee_label = "[实际佣金]" if fill['commission'] >= 0 else "[估算]"

            total_cost = actual_cost + total_fee
            self.logger_fn(
                f"✅ 买入成功: {self.code_name_fn(code)} "
                f"{actual_qty}股 @ {actual_price:.3f}，"
                f"花费 {total_cost:,.2f} 元（费用 {total_fee:.2f}，佣金 {buy_commission:.2f} {fee_label}）"
            )
            self.state_mgr.update_holding(code, name, 0, actual_price, actual_qty)
            self.ledger_service.update_order_record(order_id, fill, pnl=0.0)
            self.ledger_service.add_capital_entry(
                "买入划出", code, name,
                amount=-total_cost,
                commission=total_fee,
                fee_source=fee_label,
            )
            self.ledger_service.sync_unified_ledger_on_buy(
                code=code,
                name=name,
                price=actual_price,
                volume=actual_qty,
                commission=float(buy_commission or 0.0),
                stamp_tax=float(fee_info.get("stamp_tax", 0.0) or 0.0),
                transfer_fee=float(fee_info.get("transfer_fee", 0.0) or 0.0),
                broker_order_id=int(order_id or -1),
                reason=reason,
            )

            result['price'] = actual_price
            result['quantity'] = actual_qty
            price, qty = actual_price, actual_qty
        else:
            self.logger_fn(f"❌ 买入失败: {self.code_name_fn(code)} - {message}")

        record = TradeRecord(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            action="BUY",
            code=code,
            name=name,
            price=price,
            quantity=qty,
            amount=price * qty if price and qty else 0,
            reason=reason,
            broker_order_id=order_id,
            success=success,
            error_msg="" if success else message,
        )
        self.state.add_trade(record)
        self.state_mgr.save()
        self.trade_event_fn(success, result)
        return result

    def sell(
        self,
        code: str,
        quantity: int,
        reason: str = "",
        price: Optional[float] = None,
    ) -> dict:
        """Execute a sell order and update rotation state."""
        result = {
            'success': False,
            'action': 'SELL',
            'code': code,
            'message': '',
            'partial_fill': False,
            'remaining': 0,
        }

        current_price = (
            float(price)
            if price is not None and float(price) > 0
            else self.ensure_price_fn(code)
        )

        ok, msg = self.preflight_risk_fn(is_sell=True, current_price=current_price)
        if not ok:
            result['message'] = f"风控: {msg}"
            self.logger_fn(f"⚠ 卖出被风控拦截: {msg}")
            return result

        order_price = float(price) if price is not None and float(price) > 0 else None
        success, message, order_id = self.executor.sell(code, quantity, price=order_price)
        result['success'] = success
        result['message'] = message
        result['order_id'] = order_id
        result['price'] = current_price
        result['quantity'] = quantity
        result['reason'] = reason

        name = self.code_name_map_fn(code)
        now = datetime.now()
        actual_sold = quantity
        actual_price = current_price
        buy_price_snapshot = self.state.buy_price

        if success:
            self.ledger_service.add_order_record(order_id, "卖出", code, quantity, current_price, reason)
            fill = self.confirm_fill_fn(order_id, quantity, current_price)

            actual_sold = fill['filled_qty'] if fill['filled_qty'] > 0 else quantity
            actual_price = fill['filled_price'] if fill['filled_price'] > 0 else current_price
            remaining_qty = max(0, quantity - actual_sold)

            proceeds = actual_price * actual_sold
            fee_info = self.ledger_service.resolve_trade_fees(
                direction="sell",
                amount=proceeds,
                stock_code=code,
                actual_commission=float(fill.get("commission", -1.0) or -1.0),
            )
            sell_commission = float(fee_info.get("commission", 0.0) or 0.0)
            total_fee = float(fee_info.get("total_fee", 0.0) or 0.0)
            fee_label = "[实际佣金]" if fill['commission'] >= 0 else "[估算]"
            net_proceeds = proceeds - total_fee

            pnl = (actual_price - buy_price_snapshot) * actual_sold
            self.state.total_pnl += pnl

            if remaining_qty > 0:
                result['partial_fill'] = True
                result['remaining'] = remaining_qty
                self.logger_fn(
                    f"⚠ 卖出部分成交: 委托 {quantity} 股, "
                    f"成交 {actual_sold} 股, 剩余 {remaining_qty} 股"
                )
                self.state.buy_quantity = remaining_qty
                self.state_mgr.save()
            else:
                self.state_mgr.clear_holding()

            self.logger_fn(
                f"✅ 卖出成功: {self.code_name_fn(code)} "
                f"{actual_sold}股 @ {actual_price:.3f}, "
                f"盈亏 {pnl:+.2f}, 费用 {total_fee:.2f}（佣金 {sell_commission:.2f} {fee_label}）"
            )
            self.ledger_service.update_order_record(order_id, fill, pnl=pnl)
            self.ledger_service.add_capital_entry(
                "卖出回收", code, name,
                amount=net_proceeds,
                commission=total_fee,
                fee_source=fee_label,
            )

            if actual_sold > 0:
                self.ledger_service.sync_unified_ledger_on_sell(
                    code=code,
                    name=name,
                    price=actual_price,
                    volume=actual_sold,
                    commission=float(sell_commission or 0.0),
                    stamp_tax=float(fee_info.get("stamp_tax", 0.0) or 0.0),
                    transfer_fee=float(fee_info.get("transfer_fee", 0.0) or 0.0),
                    broker_order_id=int(order_id or -1),
                    reason=reason,
                )

            result['price'] = actual_price
            result['quantity'] = actual_sold
        else:
            self.logger_fn(f"❌ 卖出失败: {self.code_name_fn(code)} - {message}")
            actual_sold = 0
            actual_price = current_price
            pnl = 0.0

        record = TradeRecord(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            action="SELL",
            code=code,
            name=name,
            price=actual_price,
            quantity=actual_sold,
            amount=actual_price * actual_sold,
            reason=reason,
            broker_order_id=order_id,
            success=success,
            error_msg="" if success else message,
            pnl=pnl,
        )
        self.state.add_trade(record)
        self.state_mgr.save()
        self.trade_event_fn(success, result)
        return result

    def sell_all(self, reason: str = "") -> dict:
        """Sell all current holding with real sellable quantity preferred."""
        code = self.state.current_holding
        if not code:
            return {'success': True, 'message': '无持仓'}

        real_qty, _ = self.executor.query_sellable_position(code)
        qty = real_qty if real_qty > 0 else self.state.buy_quantity

        if qty <= 0:
            self.logger_fn("⚠ 持仓数量为0，跳过卖出")
            self.state_mgr.clear_holding()
            return {'success': True, 'message': '持仓数量为0'}

        return self.sell(code, qty, reason=reason)
