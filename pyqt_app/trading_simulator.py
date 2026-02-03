"""
trading_simulator.py - 股票模拟交易训练系统

提供模拟交易的核心功能：账户管理、交易记录、持仓管理等
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple
from enum import Enum
import pandas as pd
import json
from pathlib import Path


class TradeAction(Enum):
    """交易动作枚举"""
    BUY = "买入"
    SELL = "卖出"


@dataclass
class Trade:
    """交易记录"""
    trade_id: int
    timestamp: str
    date: str  # 交易日期
    action: TradeAction
    stock_code: str
    stock_name: str
    price: float
    quantity: int
    amount: float  # 交易金额
    commission: float  # 手续费
    note: str = ""
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "trade_id": self.trade_id,
            "timestamp": self.timestamp,
            "date": self.date,
            "action": self.action.value,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "price": self.price,
            "quantity": self.quantity,
            "amount": self.amount,
            "commission": self.commission,
            "note": self.note
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Trade':
        """从字典创建"""
        return cls(
            trade_id=data["trade_id"],
            timestamp=data["timestamp"],
            date=data["date"],
            action=TradeAction(data["action"]),
            stock_code=data["stock_code"],
            stock_name=data["stock_name"],
            price=data["price"],
            quantity=data["quantity"],
            amount=data["amount"],
            commission=data["commission"],
            note=data.get("note", "")
        )


@dataclass
class Position:
    """持仓信息"""
    stock_code: str
    stock_name: str
    quantity: int  # 持仓数量（股）
    avg_cost: float  # 平均成本价
    total_cost: float  # 总成本（含手续费）
    current_price: float = 0.0  # 当前价格
    
    @property
    def market_value(self) -> float:
        """市值"""
        return self.quantity * self.current_price
    
    @property
    def profit_loss(self) -> float:
        """盈亏金额"""
        return self.market_value - self.total_cost
    
    @property
    def profit_loss_pct(self) -> float:
        """盈亏比例"""
        if self.total_cost == 0:
            return 0.0
        return (self.profit_loss / self.total_cost) * 100
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "total_cost": self.total_cost,
            "current_price": self.current_price
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Position':
        """从字典创建"""
        return cls(
            stock_code=data["stock_code"],
            stock_name=data["stock_name"],
            quantity=data["quantity"],
            avg_cost=data["avg_cost"],
            total_cost=data["total_cost"],
            current_price=data.get("current_price", 0.0)
        )


@dataclass
class TradingAccount:
    """交易账户"""
    initial_capital: float  # 初始资金
    available_cash: float  # 可用资金
    commission_rate: float = 0.001  # 手续费率（默认千分之一）
    min_commission: float = 5.0  # 最低手续费
    positions: Dict[str, Position] = field(default_factory=dict)  # 持仓 {股票代码: Position}
    trade_history: List[Trade] = field(default_factory=list)  # 交易历史
    next_trade_id: int = 1
    
    @property
    def total_market_value(self) -> float:
        """持仓市值"""
        return sum(pos.market_value for pos in self.positions.values())
    
    @property
    def total_assets(self) -> float:
        """总资产"""
        return self.available_cash + self.total_market_value
    
    @property
    def total_profit_loss(self) -> float:
        """总盈亏"""
        return self.total_assets - self.initial_capital
    
    @property
    def total_profit_loss_pct(self) -> float:
        """总盈亏比例"""
        if self.initial_capital == 0:
            return 0.0
        return (self.total_profit_loss / self.initial_capital) * 100
    
    def calculate_commission(self, amount: float) -> float:
        """计算手续费"""
        commission = amount * self.commission_rate
        return max(commission, self.min_commission)
    
    def can_buy(self, price: float, quantity: int) -> Tuple[bool, str]:
        """
        检查是否可以买入
        返回: (是否可以, 原因)
        """
        if quantity <= 0:
            return False, "买入数量必须大于0"
        
        if quantity % 100 != 0:
            return False, "买入数量必须是100的整数倍（1手=100股）"
        
        amount = price * quantity
        commission = self.calculate_commission(amount)
        total_cost = amount + commission
        
        if total_cost > self.available_cash:
            return False, f"资金不足。需要: {total_cost:.2f}, 可用: {self.available_cash:.2f}"
        
        return True, "可以买入"
    
    def can_sell(self, stock_code: str, quantity: int) -> Tuple[bool, str]:
        """
        检查是否可以卖出
        返回: (是否可以, 原因)
        """
        if quantity <= 0:
            return False, "卖出数量必须大于0"
        
        if stock_code not in self.positions:
            return False, "没有持仓"
        
        position = self.positions[stock_code]
        if quantity > position.quantity:
            return False, f"持仓不足。可卖: {position.quantity}, 想卖: {quantity}"
        
        return True, "可以卖出"
    
    def buy(
        self,
        stock_code: str,
        stock_name: str,
        price: float,
        quantity: int,
        trade_date: str,
        note: str = ""
    ) -> Tuple[bool, str, Optional[Trade]]:
        """
        买入股票
        返回: (是否成功, 消息, 交易记录)
        """
        can_buy, reason = self.can_buy(price, quantity)
        if not can_buy:
            return False, reason, None
        
        amount = price * quantity
        commission = self.calculate_commission(amount)
        total_cost = amount + commission
        
        # 更新可用资金
        self.available_cash -= total_cost
        
        # 更新持仓
        if stock_code in self.positions:
            # 已有持仓，更新平均成本
            position = self.positions[stock_code]
            new_quantity = position.quantity + quantity
            new_total_cost = position.total_cost + total_cost
            position.quantity = new_quantity
            position.total_cost = new_total_cost
            position.avg_cost = new_total_cost / new_quantity
        else:
            # 新建持仓
            self.positions[stock_code] = Position(
                stock_code=stock_code,
                stock_name=stock_name,
                quantity=quantity,
                avg_cost=price + commission / quantity,
                total_cost=total_cost,
                current_price=price
            )
        
        # 记录交易
        trade = Trade(
            trade_id=self.next_trade_id,
            timestamp=datetime.now().isoformat(),
            date=trade_date,
            action=TradeAction.BUY,
            stock_code=stock_code,
            stock_name=stock_name,
            price=price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            note=note
        )
        self.trade_history.append(trade)
        self.next_trade_id += 1
        
        return True, f"买入成功：{stock_name} {quantity}股 @{price:.2f}", trade
    
    def sell(
        self,
        stock_code: str,
        stock_name: str,
        price: float,
        quantity: int,
        trade_date: str,
        note: str = ""
    ) -> Tuple[bool, str, Optional[Trade]]:
        """
        卖出股票
        返回: (是否成功, 消息, 交易记录)
        """
        can_sell, reason = self.can_sell(stock_code, quantity)
        if not can_sell:
            return False, reason, None
        
        amount = price * quantity
        commission = self.calculate_commission(amount)
        net_amount = amount - commission
        
        # 更新可用资金
        self.available_cash += net_amount
        
        # 更新持仓
        position = self.positions[stock_code]
        if quantity == position.quantity:
            # 清仓
            del self.positions[stock_code]
        else:
            # 部分卖出
            sell_cost = position.total_cost * (quantity / position.quantity)
            position.quantity -= quantity
            position.total_cost -= sell_cost
            position.avg_cost = position.total_cost / position.quantity
        
        # 记录交易
        trade = Trade(
            trade_id=self.next_trade_id,
            timestamp=datetime.now().isoformat(),
            date=trade_date,
            action=TradeAction.SELL,
            stock_code=stock_code,
            stock_name=stock_name,
            price=price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            note=note
        )
        self.trade_history.append(trade)
        self.next_trade_id += 1
        
        return True, f"卖出成功：{stock_name} {quantity}股 @{price:.2f}", trade
    
    def update_position_prices(self, prices: Dict[str, float]):
        """更新持仓的当前价格"""
        for stock_code, price in prices.items():
            if stock_code in self.positions:
                self.positions[stock_code].current_price = price
    
    def to_dict(self) -> Dict:
        """转换为字典（用于保存）"""
        return {
            "initial_capital": self.initial_capital,
            "available_cash": self.available_cash,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "positions": {code: pos.to_dict() for code, pos in self.positions.items()},
            "trade_history": [trade.to_dict() for trade in self.trade_history],
            "next_trade_id": self.next_trade_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'TradingAccount':
        """从字典创建（用于加载）"""
        account = cls(
            initial_capital=data["initial_capital"],
            available_cash=data["available_cash"],
            commission_rate=data["commission_rate"],
            min_commission=data["min_commission"],
            next_trade_id=data["next_trade_id"]
        )
        account.positions = {
            code: Position.from_dict(pos_data)
            for code, pos_data in data["positions"].items()
        }
        account.trade_history = [
            Trade.from_dict(trade_data)
            for trade_data in data["trade_history"]
        ]
        return account


class TradingSimulator:
    """模拟交易训练系统"""
    
    def __init__(
        self,
        stock_code: str,
        stock_name: str,
        data: pd.DataFrame,
        initial_capital: float = 1000000,
        commission_rate: float = 0.001,
        start_date: Optional[str] = None
    ):
        """
        初始化模拟交易系统
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            data: 股票数据（DataFrame，索引为日期）
            initial_capital: 初始资金
            commission_rate: 手续费率
            start_date: 开始日期（如果为None，从第一个交易日开始）
        """
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.data = data.sort_index()
        self.dates = self.data.index.tolist()
        
        # 初始化账户
        self.account = TradingAccount(
            initial_capital=initial_capital,
            available_cash=initial_capital,
            commission_rate=commission_rate
        )
        
        # 设置当前日期索引
        if start_date:
            start_date_dt = pd.to_datetime(start_date)
            try:
                self.current_index = self.dates.index(start_date_dt)
            except ValueError:
                # 如果指定日期不存在，找到最接近的日期
                self.current_index = 0
                for i, d in enumerate(self.dates):
                    if d >= start_date_dt:
                        self.current_index = i
                        break
        else:
            self.current_index = 0
    
    @property
    def current_date(self) -> pd.Timestamp:
        """当前交易日"""
        return self.dates[self.current_index]
    
    @property
    def current_data(self) -> pd.Series:
        """当前日期的数据"""
        return self.data.iloc[self.current_index]
    
    @property
    def visible_data(self) -> pd.DataFrame:
        """当前可见的数据（到当前日期为止）"""
        return self.data.iloc[:self.current_index + 1]
    
    @property
    def can_go_next(self) -> bool:
        """是否可以前进到下一日"""
        return self.current_index < len(self.dates) - 1
    
    @property
    def can_go_prev(self) -> bool:
        """是否可以回退到上一日"""
        return self.current_index > 0
    
    def next_day(self) -> bool:
        """前进到下一个交易日"""
        if self.can_go_next:
            self.current_index += 1
            # 更新持仓价格
            current_price = self.current_data['Close']
            self.account.update_position_prices({self.stock_code: current_price})
            return True
        return False
    
    def prev_day(self) -> bool:
        """回退到上一个交易日（仅用于查看，不建议在正式训练中使用）"""
        if self.can_go_prev:
            self.current_index -= 1
            # 更新持仓价格
            current_price = self.current_data['Close']
            self.account.update_position_prices({self.stock_code: current_price})
            return True
        return False
    
    def jump_to_date(self, target_date: str) -> bool:
        """跳转到指定日期"""
        target_date_dt = pd.to_datetime(target_date)
        try:
            self.current_index = self.dates.index(target_date_dt)
            current_price = self.current_data['Close']
            self.account.update_position_prices({self.stock_code: current_price})
            return True
        except ValueError:
            return False
    
    def buy_stock(self, price: float, quantity: int, note: str = "") -> Tuple[bool, str]:
        """买入当前股票"""
        date_str = self.current_date.strftime("%Y-%m-%d")
        success, message, trade = self.account.buy(
            self.stock_code,
            self.stock_name,
            price,
            quantity,
            date_str,
            note
        )
        return success, message
    
    def sell_stock(self, price: float, quantity: int, note: str = "") -> Tuple[bool, str]:
        """卖出当前股票"""
        date_str = self.current_date.strftime("%Y-%m-%d")
        success, message, trade = self.account.sell(
            self.stock_code,
            self.stock_name,
            price,
            quantity,
            date_str,
            note
        )
        return success, message
    
    def get_available_quantity(self) -> int:
        """获取当前股票的可卖数量"""
        if self.stock_code in self.account.positions:
            return self.account.positions[self.stock_code].quantity
        return 0
    
    def get_max_buy_quantity(self, price: float) -> int:
        """计算最大可买数量（股）"""
        if price <= 0:
            return 0
        
        # 预留手续费空间
        available = self.account.available_cash * 0.999  # 预留0.1%的手续费空间
        max_amount = available / (1 + self.account.commission_rate)
        max_quantity = int(max_amount / price)
        
        # 向下取整到100的整数倍
        max_quantity = (max_quantity // 100) * 100
        
        return max_quantity
    
    def save(self, filepath: str):
        """保存模拟交易状态"""
        save_data = {
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "current_index": self.current_index,
            "current_date": self.current_date.isoformat(),
            "account": self.account.to_dict(),
            "timestamp": datetime.now().isoformat()
        }
        
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def load(cls, filepath: str, data: pd.DataFrame) -> Optional['TradingSimulator']:
        """加载模拟交易状态"""
        filepath = Path(filepath)
        if not filepath.exists():
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                save_data = json.load(f)
            
            simulator = cls(
                stock_code=save_data["stock_code"],
                stock_name=save_data["stock_name"],
                data=data,
                initial_capital=save_data["account"]["initial_capital"],
                commission_rate=save_data["account"]["commission_rate"]
            )
            
            simulator.current_index = save_data["current_index"]
            simulator.account = TradingAccount.from_dict(save_data["account"])
            
            return simulator
        except Exception as e:
            print(f"加载失败: {e}")
            return None
    
    def reset(self):
        """重置模拟交易（清空账户和交易记录）"""
        initial_capital = self.account.initial_capital
        commission_rate = self.account.commission_rate
        
        self.account = TradingAccount(
            initial_capital=initial_capital,
            available_cash=initial_capital,
            commission_rate=commission_rate
        )
        self.current_index = 0

