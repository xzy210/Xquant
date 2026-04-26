from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Position:
    """持仓信息"""
    symbol: str
    quantity: int
    avg_price: float
    sellable_quantity: int = 0
    last_buy_date: Optional[datetime] = None
    last_price: float = 0.0
    
    @property
    def market_value(self):
        return 0.0 # 需要外部注入当前价格计算，或在Context中计算

@dataclass
class TradeRecord:
    """交易记录"""
    symbol: str
    action: str  # 'BUY' or 'SELL'
    date: datetime
    price: float
    quantity: int
    commission: float
    reason: str
    cash_after: float
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    total_fee: float = 0.0
    blocked_reason: str = ""

@dataclass
class TradeResult:
    """已平仓交易的盈亏结果"""
    symbol: str
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float       # 盈亏金额
    pnl_pct: float   # 盈亏比例
    hold_days: int
