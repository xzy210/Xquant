from typing import Dict, List, Optional

import pandas as pd

from common.execution_contract import FillReport, OrderExecutionReport, OrderIntent
from .broker import FeeModelConfig, SimulationBroker
from .models import Position, TradeRecord, TradeResult

class Context:
    """
    策略运行上下文
    模拟账户资金、持仓，并提供下单接口
    """
    def __init__(self, initial_cash=100000.0, commission_rate=0.0003,
                 buy_commission_rate=None, sell_commission_rate=None,
                 min_commission=5.0, broker: Optional[SimulationBroker] = None):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {}
        # Backward compatible attributes; fee calculation is delegated to broker.
        self.buy_commission_rate = buy_commission_rate if buy_commission_rate is not None else commission_rate
        self.sell_commission_rate = sell_commission_rate if sell_commission_rate is not None else commission_rate
        self.min_commission = min_commission
        fee_config = FeeModelConfig()
        if buy_commission_rate is not None or sell_commission_rate is not None:
            fee_config = FeeModelConfig(
                buy_commission_rate=buy_commission_rate,
                sell_commission_rate=sell_commission_rate,
                min_commission=min_commission,
            )
        self.broker = broker or SimulationBroker(fee_config=fee_config)
        self.trade_history: List[TradeRecord] = []
        self.closed_trades: List[TradeResult] = []
        self.execution_reports: List[OrderExecutionReport] = []
        self.current_dt = None
        self.current_prices = {} # symbol -> price
        self.current_bars = {} # symbol -> current bar

    def before_trading_day(self, current_dt, bars: Optional[Dict[str, object]] = None):
        """Refresh market snapshot and T+1 sellable quantity before strategy callbacks."""
        self.current_dt = current_dt
        self.current_bars = dict(bars or {})
        for symbol, pos in self.positions.items():
            if self._is_same_trading_day(pos.last_buy_date, current_dt):
                continue
            pos.sellable_quantity = int(pos.quantity or 0)

    def order(self, symbol: str, quantity: int, price: float = None, reason: str = ""):
        """
        下单函数 (正数为买，负数为卖)
        """
        report = self._execute_order(symbol, quantity, price, reason)
        self.execution_reports.append(report)
        return bool(report.accepted and report.filled)

    def place_order_intent(self, intent: OrderIntent) -> OrderExecutionReport:
        """Execute a unified order intent in backtest mode and return a report."""
        report = self._execute_order(
            intent.symbol,
            intent.signed_quantity,
            intent.price,
            intent.reason,
            intent=intent,
        )
        self.execution_reports.append(report)
        return report

    def _execute_order(self, symbol: str, quantity: int, price: float = None, reason: str = "", intent: Optional[OrderIntent] = None) -> OrderExecutionReport:
        """Internal order execution implementation shared by legacy and unified APIs."""
        if intent is None:
            side = "buy" if int(quantity or 0) > 0 else "sell"
            intent = OrderIntent(
                symbol=symbol,
                side=side,
                quantity=abs(int(quantity or 0)),
                price=price,
                reason=reason,
                source="backtest",
                trigger="strategy",
            )
        
        if price is None:
            price = self.current_prices.get(symbol)
            if price is None:
                print(f"Error: No price for {symbol}")
                return self._build_execution_report(intent, False, "rejected", "No price for symbol")

        price = float(price or 0.0)
        quantity = int(quantity or 0)
        if price <= 0 or quantity == 0:
            return self._build_execution_report(intent, False, "rejected", "Invalid price or quantity")

        direction = "buy" if quantity > 0 else "sell"
        requested_quantity = self._normalize_order_quantity(quantity)
        if requested_quantity == 0:
            return self._build_execution_report(intent, False, "rejected", "Order quantity is below min lot")

        if direction == "sell":
            if symbol not in self.positions:
                return self._build_execution_report(intent, False, "rejected", "No position to sell")
            pos = self.positions[symbol]
            requested_quantity = -min(abs(requested_quantity), int(pos.quantity or 0), int(pos.sellable_quantity or 0))
            requested_quantity = -self._round_sell_quantity(symbol, abs(requested_quantity))
            if requested_quantity == 0:
                return self._build_execution_report(intent, False, "rejected", "No sellable quantity")

        match = self.broker.match_order(
            symbol=symbol,
            quantity=requested_quantity,
            price=price,
            bar=self.current_bars.get(symbol),
        )
        if match.blocked_reason or not match.matched:
            return self._build_execution_report(
                intent,
                False,
                "blocked",
                match.blocked_reason or "Order not matched",
                blocked_reason=match.blocked_reason,
                metadata={"match": match},
            )

        fill_qty = int(match.filled_quantity or 0)
        fill_price = float(match.fill_price or 0.0)
        if fill_qty <= 0 or fill_price <= 0:
            return self._build_execution_report(intent, False, "rejected", "Invalid fill")

        # 买入逻辑
        if direction == "buy":
            cost = fill_qty * fill_price
            fees = self._estimate_trade_fees("buy", cost, symbol)
            total_cost = cost + fees["total_fee"]

            if self.cash + 1e-6 < total_cost:
                return self._build_execution_report(intent, False, "rejected", "Insufficient cash")

            self.cash -= total_cost
            
            # 更新持仓：与实盘策略预算账本一致，avg_price 不摊入手续费
            if symbol in self.positions:
                pos = self.positions[symbol]
                new_total_cost = (pos.quantity * pos.avg_price) + cost
                new_qty = pos.quantity + fill_qty
                pos.avg_price = new_total_cost / new_qty
                pos.quantity = new_qty
                pos.last_buy_date = self.current_dt
                pos.last_price = fill_price
            else:
                self.positions[symbol] = Position(
                    symbol,
                    fill_qty,
                    fill_price,
                    sellable_quantity=0,
                    last_buy_date=self.current_dt,
                    last_price=fill_price,
                )

            trade = self._record_trade(symbol, 'BUY', fill_price, fill_qty, fees, reason)
            fill = FillReport.from_backtest_trade(trade, intent_id=intent.intent_id, strategy_id=intent.strategy_id)
            return self._build_execution_report(
                intent,
                True,
                "filled",
                "Order filled",
                fills=[fill],
                submitted=True,
                filled=True,
                partial=bool(match.partial),
                metadata={"match": match},
            )

        # 卖出逻辑
        if symbol not in self.positions:
            return self._build_execution_report(intent, False, "rejected", "No position to sell")

        pos = self.positions[symbol]
        sell_qty = min(fill_qty, int(pos.quantity or 0), int(pos.sellable_quantity or 0))
        sell_qty = self._round_sell_quantity(symbol, sell_qty)
        if sell_qty <= 0:
            return self._build_execution_report(intent, False, "rejected", "No sellable quantity")

        revenue = sell_qty * fill_price
        fees = self._estimate_trade_fees("sell", revenue, symbol)
        net_income = revenue - fees["total_fee"]

        self.cash += net_income
        
        # 记录平仓盈亏：卖出时扣除佣金、印花税、过户费；买入费用只扣现金，不摊入成本
        total_fee = fees["total_fee"]
        pnl = (fill_price - pos.avg_price) * sell_qty - total_fee
        base_cost = pos.avg_price * sell_qty
        pnl_pct = pnl / base_cost if base_cost > 0 else 0.0
        
        self.closed_trades.append(TradeResult(
            symbol=symbol,
            entry_date=pos.last_buy_date,
            exit_date=self.current_dt,
            entry_price=pos.avg_price,
            exit_price=fill_price,
            quantity=sell_qty,
            pnl=pnl,
            pnl_pct=pnl_pct,
            hold_days=0
        ))

        pos.quantity -= sell_qty
        pos.sellable_quantity = max(int(pos.sellable_quantity or 0) - sell_qty, 0)
        pos.last_price = fill_price
        if pos.quantity <= 0:
            del self.positions[symbol]

        trade = self._record_trade(symbol, 'SELL', fill_price, sell_qty, fees, reason)
        fill = FillReport.from_backtest_trade(trade, intent_id=intent.intent_id, strategy_id=intent.strategy_id)
        return self._build_execution_report(
            intent,
            True,
            "filled",
            "Order filled",
            fills=[fill],
            submitted=True,
            filled=True,
            partial=bool(match.partial),
            metadata={"match": match},
        )

    def order_target(self, symbol: str, target_quantity: int, price: float = None, reason: str = ""):
        """
        下单到目标持仓数量
        target_quantity: 目标持仓数量 (0表示清仓)
        """
        if price is None:
            price = self.current_prices.get(symbol)
            if price is None:
                print(f"Error: No price for {symbol}")
                return False
        
        current_qty = self.positions[symbol].quantity if symbol in self.positions else 0
        target_quantity = self._round_target_quantity(symbol, int(target_quantity or 0))
        diff_qty = target_quantity - current_qty
        
        if diff_qty != 0:
            return self.order(symbol, diff_qty, price, reason)
        return True

    def order_target_percent(self, symbol: str, target_percent: float, price: float = None, reason: str = ""):
        """按目标仓位比例下单"""
        if price is None:
            price = self.current_prices.get(symbol)
        if price is None or price <= 0:
            return False
        
        total_value = self.cash
        for s, pos in self.positions.items():
            p = self.current_prices.get(s, pos.last_price or pos.avg_price)
            total_value += pos.quantity * p
            pos.last_price = p
            
        target_value = total_value * target_percent
        current_hold_value = self.positions[symbol].quantity * price if symbol in self.positions else 0
            
        diff_value = target_value - current_hold_value
        quantity = self._normalize_order_quantity(int(diff_value / price))
        
        if quantity != 0:
            return self.order(symbol, quantity, price, reason)
        return False

    def _normalize_order_quantity(self, quantity: int) -> int:
        """Match live gateway requirement: orders are submitted in configured lots."""
        if quantity == 0:
            return 0
        sign = 1 if quantity > 0 else -1
        lots = abs(int(quantity)) // self.broker.min_lot
        return sign * lots * self.broker.min_lot

    def _round_target_quantity(self, symbol: str, target_quantity: int) -> int:
        if target_quantity <= 0:
            return 0
        return (target_quantity // self.broker.min_lot) * self.broker.min_lot

    def _round_sell_quantity(self, symbol: str, quantity: int) -> int:
        if quantity <= 0:
            return 0
        pos = self.positions.get(symbol)
        if pos and quantity >= int(pos.quantity or 0):
            return int(pos.quantity or 0)
        return (quantity // self.broker.min_lot) * self.broker.min_lot

    def _estimate_trade_fees(self, direction: str, amount: float, symbol: str) -> Dict[str, float]:
        return self.broker.estimate_trade_fees(
            direction=direction,
            amount=amount,
            stock_code=symbol,
        )

    def _build_execution_report(
        self,
        intent: OrderIntent,
        accepted: bool,
        status: str,
        message: str,
        *,
        fills: Optional[List[FillReport]] = None,
        blocked_reason: str = "",
        submitted: bool = False,
        filled: bool = False,
        partial: bool = False,
        metadata: Optional[Dict[str, object]] = None,
    ) -> OrderExecutionReport:
        return OrderExecutionReport(
            intent=intent,
            accepted=accepted,
            status=status,
            message=message,
            execution_mode="backtest",
            fills=tuple(fills or ()),
            blocked_reason=blocked_reason,
            submitted=submitted,
            filled=filled,
            partial=partial,
            metadata={
                **dict(metadata or {}),
                "current_dt": self.current_dt,
            },
        )

    @staticmethod
    def _is_same_trading_day(left, right) -> bool:
        if left is None or right is None:
            return False
        try:
            return pd.Timestamp(left).date() == pd.Timestamp(right).date()
        except Exception:
            return str(left).split(" ")[0] == str(right).split(" ")[0]

    def _record_trade(self, symbol, action, price, qty, fees, reason):
        trade = TradeRecord(
            symbol=symbol,
            action=action,
            date=self.current_dt,
            price=price,
            quantity=qty,
            commission=fees["commission"],
            stamp_tax=fees.get("stamp_tax", 0.0),
            transfer_fee=fees.get("transfer_fee", 0.0),
            total_fee=fees.get("total_fee", fees["commission"]),
            reason=reason,
            cash_after=self.cash
        )
        self.trade_history.append(trade)
        return trade
