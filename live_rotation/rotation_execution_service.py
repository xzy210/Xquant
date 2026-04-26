"""
ETF rotation execution service.

This module owns order execution, fill reconciliation, and live rotation state
updates. UI signals and notifications remain in RotationEngine via callbacks so
this service can be reused by non-Qt runners later.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, Optional

from common.execution_contract import FillReport, OrderExecutionReport

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
        trade_event_fn: Callable[[bool, dict], None],
        logger_fn: Optional[Callable[[str], None]] = None,
        code_name_fn: Optional[Callable[[str], str]] = None,
        code_name_map_fn: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.config = config
        self.state = state
        self.state_mgr = state_mgr
        self.executor = executor
        self.ledger_service = ledger_service
        self.trade_event_fn = trade_event_fn
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

    def apply_execution_reports(
        self,
        reports: list[OrderExecutionReport],
        *,
        scores: Optional[Dict[str, float]] = None,
        reason: str = "",
    ) -> dict:
        """Apply unified execution reports to ETF rotation state and ledger."""
        result = {"success": True, "trades": []}
        for report in list(reports or []):
            trade = self.apply_execution_report(report, scores=scores or {}, reason=reason)
            if trade:
                result["trades"].append(trade)
                result["success"] = bool(result["success"] and trade.get("success", False))
        return result

    def apply_execution_report(
        self,
        report: OrderExecutionReport,
        *,
        scores: Optional[Dict[str, float]] = None,
        reason: str = "",
    ) -> dict:
        """Apply one unified execution report to ETF rotation state and ledger."""
        intent = report.intent
        if intent is None:
            return {}
        side = intent.side.upper()
        code = self._plain_code(intent.symbol)
        action = "BUY" if side == "BUY" else "SELL"
        fill = self._report_fill(report)
        quantity = int(fill.quantity if fill is not None else intent.quantity or 0)
        price = float(fill.price if fill is not None else intent.price or 0.0)
        order_id = int(report.order_id or 0) if str(report.order_id or "").isdigit() else -1
        trade_reason = reason or intent.reason or report.message
        trade = {
            "success": bool(report.accepted),
            "action": action,
            "code": code,
            "message": report.message,
            "order_id": order_id,
            "price": price,
            "quantity": quantity,
            "reason": trade_reason,
            "partial_fill": bool(report.partial),
            "remaining": 0,
        }

        if not report.accepted:
            self.logger_fn(f"❌ {action}失败: {self.code_name_fn(code)} - {report.message}")
            self._record_trade(action, code, price, 0, order_id, False, report.message, trade_reason)
            self.trade_event_fn(False, trade)
            return trade

        if fill is None and not report.filled:
            trade["success"] = True
            trade["message"] = report.message or "委托已提交，等待成交确认"
            trade["submitted"] = bool(report.submitted or report.accepted)
            self.ledger_service.add_order_record(order_id, "买入" if action == "BUY" else "卖出", code, int(intent.quantity or 0), price, trade_reason)
            self.logger_fn(f"⏳ {action}委托已提交: {self.code_name_fn(code)} {intent.quantity}股 @ {price:.3f}")
            self.trade_event_fn(True, trade)
            return trade

        if action == "BUY":
            self._apply_buy_fill(code, price, quantity, order_id, trade_reason)
            score = float((scores or {}).get(code, 0.0) or 0.0)
            if score:
                self.state.current_score = score
                self.state_mgr.save()
        else:
            self._apply_sell_fill(code, price, quantity, order_id, trade_reason)
            requested = int(intent.quantity or 0)
            remaining = max(0, requested - quantity)
            trade["partial_fill"] = remaining > 0
            trade["remaining"] = remaining

        self.trade_event_fn(True, trade)
        return trade

    @staticmethod
    def _plain_code(symbol: str) -> str:
        return str(symbol or "").split(".")[0].upper()

    @staticmethod
    def _report_fill(report: OrderExecutionReport) -> Optional[FillReport]:
        fills = list(report.fills or ())
        return fills[0] if fills else None

    def _apply_buy_fill(self, code: str, price: float, quantity: int, order_id: int, reason: str) -> None:
        if price <= 0 or quantity <= 0:
            return
        name = self.code_name_map_fn(code)
        fee_info = self.ledger_service.resolve_trade_fees(direction="buy", amount=price * quantity, stock_code=code)
        total_fee = float(fee_info.get("total_fee", 0.0) or 0.0)
        self.ledger_service.add_order_record(order_id, "买入", code, quantity, price, reason)
        self.ledger_service.update_order_record(order_id, {"filled_qty": quantity, "filled_price": price, "commission": float(fee_info.get("commission", 0.0) or 0.0), "filled": True}, pnl=0.0)
        self.ledger_service.add_capital_entry("买入划出", code, name, amount=-(price * quantity + total_fee), commission=total_fee, fee_source="[统一执行]")
        self.state_mgr.update_holding(code, name, self.state.current_score, price, quantity)
        self._record_trade("BUY", code, price, quantity, order_id, True, "", reason)
        self.logger_fn(f"✅ 买入成功: {self.code_name_fn(code)} {quantity}股 @ {price:.3f}")

    def _apply_sell_fill(self, code: str, price: float, quantity: int, order_id: int, reason: str) -> None:
        if price <= 0 or quantity <= 0:
            return
        name = self.code_name_map_fn(code)
        fee_info = self.ledger_service.resolve_trade_fees(direction="sell", amount=price * quantity, stock_code=code)
        total_fee = float(fee_info.get("total_fee", 0.0) or 0.0)
        buy_price_snapshot = float(self.state.buy_price or 0.0)
        pnl = (price - buy_price_snapshot) * quantity
        self.state.total_pnl += pnl
        self.ledger_service.add_order_record(order_id, "卖出", code, quantity, price, reason)
        self.ledger_service.update_order_record(order_id, {"filled_qty": quantity, "filled_price": price, "commission": float(fee_info.get("commission", 0.0) or 0.0), "filled": True}, pnl=pnl)
        self.ledger_service.add_capital_entry("卖出回收", code, name, amount=price * quantity - total_fee, commission=total_fee, fee_source="[统一执行]")
        remaining = max(0, int(self.state.buy_quantity or 0) - quantity)
        if remaining > 0:
            self.state.buy_quantity = remaining
            self.state_mgr.save()
        else:
            self.state_mgr.clear_holding()
        self._record_trade("SELL", code, price, quantity, order_id, True, "", reason, pnl=pnl)
        self.logger_fn(f"✅ 卖出成功: {self.code_name_fn(code)} {quantity}股 @ {price:.3f}, 盈亏 {pnl:+.2f}")

    def _record_trade(
        self,
        action: str,
        code: str,
        price: float,
        quantity: int,
        order_id: int,
        success: bool,
        error_msg: str,
        reason: str,
        *,
        pnl: float = 0.0,
    ) -> None:
        now = datetime.now()
        record = TradeRecord(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            action=action,
            code=code,
            name=self.code_name_map_fn(code),
            price=price,
            quantity=quantity,
            amount=price * quantity if price and quantity else 0,
            reason=reason,
            broker_order_id=order_id,
            success=success,
            error_msg=error_msg,
            pnl=pnl,
        )
        self.state.add_trade(record)
        self.state_mgr.save()



