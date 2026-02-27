import pandas as pd
from typing import Dict, List
from .models import Position, TradeRecord, TradeResult

class Context:
    """
    策略运行上下文
    模拟账户资金、持仓，并提供下单接口
    """
    def __init__(self, initial_cash=100000.0, commission_rate=0.0003,
                 buy_commission_rate=None, sell_commission_rate=None,
                 min_commission=5.0):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {}
        # 支持买卖独立费率；若未单独指定则使用 commission_rate
        self.buy_commission_rate = buy_commission_rate if buy_commission_rate is not None else commission_rate
        self.sell_commission_rate = sell_commission_rate if sell_commission_rate is not None else commission_rate
        self.min_commission = min_commission
        self.trade_history: List[TradeRecord] = []
        self.closed_trades: List[TradeResult] = []
        self.current_dt = None
        self.current_prices = {} # symbol -> price

    def order(self, symbol: str, quantity: int, price: float = None, reason: str = ""):
        """
        下单函数 (正数为买，负数为卖)
        """
        if price is None:
            price = self.current_prices.get(symbol)
            if price is None:
                print(f"Error: No price for {symbol}")
                return False

        if quantity == 0:
            return False

        # 买入逻辑
        if quantity > 0:
            cost = quantity * price
            commission = max(self.min_commission, cost * self.buy_commission_rate)
            total_cost = cost + commission

            if self.cash < total_cost:
                # 资金不足，尝试调整数量
                # print(f"资金不足买入 {symbol}, 需 {total_cost}, 有 {self.cash}")
                return False

            self.cash -= total_cost
            
            # 更新持仓
            if symbol in self.positions:
                pos = self.positions[symbol]
                new_total_cost = (pos.quantity * pos.avg_price) + cost
                new_qty = pos.quantity + quantity
                pos.avg_price = new_total_cost / new_qty
                pos.quantity = new_qty
            else:
                self.positions[symbol] = Position(symbol, quantity, price)

            self._record_trade(symbol, 'BUY', price, quantity, commission, reason)
            return True

        # 卖出逻辑
        else:
            abs_qty = abs(quantity)
            if symbol not in self.positions or self.positions[symbol].quantity < abs_qty:
                # print(f"持仓不足卖出 {symbol}")
                return False

            pos = self.positions[symbol]
            revenue = abs_qty * price
            commission = max(self.min_commission, revenue * self.sell_commission_rate)
            net_income = revenue - commission

            self.cash += net_income
            
            # 记录平仓盈亏
            pnl = (price - pos.avg_price) * abs_qty - commission
            pnl_pct = pnl / (pos.avg_price * abs_qty)
            
            self.closed_trades.append(TradeResult(
                symbol=symbol,
                entry_date=None, # 简化处理，暂不追踪具体哪一笔
                exit_date=self.current_dt,
                entry_price=pos.avg_price,
                exit_price=price,
                quantity=abs_qty,
                pnl=pnl,
                pnl_pct=pnl_pct,
                hold_days=0
            ))

            pos.quantity -= abs_qty
            if pos.quantity == 0:
                del self.positions[symbol]

            self._record_trade(symbol, 'SELL', price, abs_qty, commission, reason)
            return True

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
        
        # 计算当前持仓
        current_qty = 0
        if symbol in self.positions:
            current_qty = self.positions[symbol].quantity
        
        # 计算需要调整的数量
        diff_qty = target_quantity - current_qty
        
        if diff_qty != 0:
            return self.order(symbol, diff_qty, price, reason)
        return True  # 已经是目标持仓，无需操作

    def order_target_percent(self, symbol: str, target_percent: float, price: float = None, reason: str = ""):
        """按目标仓位比例下单"""
        if price is None:
            price = self.current_prices.get(symbol)
        
        # 计算当前总资产 (现金 + 持仓市值)
        total_value = self.cash
        for s, pos in self.positions.items():
            p = self.current_prices.get(s, pos.avg_price)
            total_value += pos.quantity * p
            
        target_value = total_value * target_percent
        current_hold_value = 0
        if symbol in self.positions:
            current_hold_value = self.positions[symbol].quantity * price
            
        diff_value = target_value - current_hold_value
        quantity = int(diff_value / price / 100) * 100 # 向下取整到手
        
        if quantity != 0:
            return self.order(symbol, quantity, price, reason)
        return False

    def _record_trade(self, symbol, action, price, qty, comm, reason):
        self.trade_history.append(TradeRecord(
            symbol=symbol,
            action=action,
            date=self.current_dt,
            price=price,
            quantity=qty,
            commission=comm,
            reason=reason,
            cash_after=self.cash
        ))

