# trade_record_service.py - 本地交易记录服务
"""
本地交易记录持久化存储服务

功能：
- 使用 SQLite 数据库存储所有交易记录
- 支持按日期、股票、策略等条件查询
- 提供统计分析功能（胜率、盈亏比等）
- 支持导出到 CSV/Excel
- 自动在下单成功后记录

数据表结构：
- trades: 交易记录主表
- daily_summary: 每日汇总表（自动计算）
"""

import sqlite3
import logging
import json
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal

# 设置日志
logger = logging.getLogger(__name__)

# 自动止损服务引用（延迟导入避免循环依赖）
_auto_stop_loss_service_getter = None

def set_auto_stop_loss_service_getter(getter):
    """设置自动止损服务的获取函数"""
    global _auto_stop_loss_service_getter
    _auto_stop_loss_service_getter = getter


class TradeDirection(Enum):
    """交易方向"""
    BUY = "buy"
    SELL = "sell"


class TradeSource(Enum):
    """交易来源/策略"""
    MANUAL = "manual"              # 手动下单
    CONDITIONAL = "conditional"    # 条件单
    ETF_GRID = "etf_grid"         # ETF网格策略
    AI_AGENT = "ai_agent"         # AI智能交易
    BROKER_SYNC = "broker_sync"   # 券商成交同步
    OTHER = "other"               # 其他


@dataclass
class TradeRecord:
    """交易记录数据结构"""
    id: int = 0                        # 数据库自增ID
    trade_id: str = ""                 # 唯一交易标识
    broker_order_id: int = -1          # 券商委托单号
    stock_code: str = ""               # 股票代码
    stock_name: str = ""               # 股票名称
    direction: str = ""                # 交易方向: buy/sell
    price: float = 0.0                 # 成交价格
    volume: int = 0                    # 成交数量
    amount: float = 0.0                # 成交金额
    commission: float = 0.0            # 手续费（佣金）
    stamp_tax: float = 0.0             # 印花税
    transfer_fee: float = 0.0          # 过户费
    trade_date: str = ""               # 成交日期
    source: str = "manual"             # 来源/策略
    strategy_id: str = ""              # 策略标识
    virtual_account_id: str = ""       # 虚拟子账户标识
    intent_id: str = ""                # 下单意图标识
    remark: str = ""                   # 备注
    created_at: str = ""               # 记录创建时间
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.trade_date:
            self.trade_date = datetime.now().strftime("%Y-%m-%d")
        if not self.trade_id:
            self.trade_id = f"{self.trade_date}_{self.stock_code}_{self.direction}_{datetime.now().strftime('%H%M%S%f')[:10]}"
    
    @property
    def total_fee(self) -> float:
        """总费用（佣金+印花税+过户费）"""
        return self.commission + self.stamp_tax + self.transfer_fee
    
    @property
    def direction_display(self) -> str:
        """方向显示"""
        return "买入" if self.direction == TradeDirection.BUY.value else "卖出"
    
    @property
    def source_display(self) -> str:
        """来源显示"""
        source_map = {
            TradeSource.MANUAL.value: "手动",
            TradeSource.CONDITIONAL.value: "条件单",
            TradeSource.ETF_GRID.value: "ETF网格",
            TradeSource.AI_AGENT.value: "AI智能",
            TradeSource.BROKER_SYNC.value: "成交同步",
            TradeSource.OTHER.value: "其他",
        }
        return source_map.get(self.source, self.source)
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'TradeRecord':
        """从字典创建"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    @classmethod
    def from_row(cls, row: tuple, columns: List[str]) -> 'TradeRecord':
        """从数据库行创建"""
        data = dict(zip(columns, row))
        return cls.from_dict(data)


@dataclass
class OrderRecord:
    """委托生命周期记录"""
    id: int = 0
    request_id: str = ""
    broker_order_id: int = -1
    fingerprint: str = ""
    stock_code: str = ""
    stock_name: str = ""
    direction: str = ""
    price: float = 0.0
    price_type: int = 0
    order_volume: int = 0
    source: str = "manual"
    trigger: str = "manual"
    strategy_name: str = ""
    strategy_id: str = ""
    virtual_account_id: str = ""
    intent_id: str = ""
    execution_mode: str = "live"
    status: str = "created"
    validation_message: str = ""
    order_status_code: int = 0
    order_status_text: str = ""
    executed_price: float = 0.0
    executed_volume: int = 0
    linked_trade_record_id: int = 0
    decision_record_id: str = ""
    remark: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "OrderRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


_BROKER_ORDER_STATUS_MAP = {
    48: "unreported",
    49: "pending",
    50: "submitted",
    51: "submitted",
    52: "partial_fill",
    53: "cancelled",
    54: "cancelled",
    55: "partial_fill",
    56: "filled",
    57: "rejected",
}


@dataclass
class DailyPnlSnapshot:
    """每日盈亏快照数据结构"""
    id: int = 0                        # 数据库自增ID
    snapshot_date: str = ""             # 快照日期 YYYY-MM-DD
    total_asset: float = 0.0           # 总资产
    cash: float = 0.0                  # 可用资金
    market_value: float = 0.0          # 持仓市值
    total_profit: float = 0.0          # 当日总盈亏（相对于前一日总资产）
    total_profit_pct: float = 0.0      # 当日总收益率 %
    cumulative_return: float = 0.0     # 累计收益率 %
    position_count: int = 0            # 持仓数量
    remark: str = ""                   # 备注
    created_at: str = ""               # 创建时间
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.snapshot_date:
            self.snapshot_date = datetime.now().strftime("%Y-%m-%d")
    
    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'snapshot_date': self.snapshot_date,
            'total_asset': self.total_asset,
            'cash': self.cash,
            'market_value': self.market_value,
            'total_profit': self.total_profit,
            'total_profit_pct': self.total_profit_pct,
            'cumulative_return': self.cumulative_return,
            'position_count': self.position_count,
            'remark': self.remark,
            'created_at': self.created_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'DailyPnlSnapshot':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class DailyPositionSnapshot:
    """日终持仓明细快照"""
    id: int = 0
    snapshot_date: str = ""
    stock_code: str = ""
    stock_name: str = ""
    volume: int = 0
    can_use_volume: int = 0
    open_price: float = 0.0
    market_value: float = 0.0
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.snapshot_date:
            self.snapshot_date = datetime.now().strftime("%Y-%m-%d")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DailyPositionSnapshot":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class StrategyDailyPnlSnapshot:
    id: int = 0
    snapshot_date: str = ""
    strategy_id: str = ""
    strategy_name: str = ""
    virtual_account_id: str = ""
    total_asset: float = 0.0
    cash: float = 0.0
    market_value: float = 0.0
    position_count: int = 0
    remark: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.snapshot_date:
            self.snapshot_date = datetime.now().strftime("%Y-%m-%d")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyDailyPnlSnapshot":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class StrategyPositionSnapshot:
    id: int = 0
    snapshot_date: str = ""
    strategy_id: str = ""
    strategy_name: str = ""
    virtual_account_id: str = ""
    stock_code: str = ""
    stock_name: str = ""
    volume: int = 0
    can_use_volume: int = 0
    open_price: float = 0.0
    market_value: float = 0.0
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.snapshot_date:
            self.snapshot_date = datetime.now().strftime("%Y-%m-%d")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyPositionSnapshot":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class StrategyDailyTradeSummary:
    id: int = 0
    snapshot_date: str = ""
    strategy_id: str = ""
    strategy_name: str = ""
    virtual_account_id: str = ""
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    total_buy_amount: float = 0.0
    total_sell_amount: float = 0.0
    total_commission: float = 0.0
    remark: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.snapshot_date:
            self.snapshot_date = datetime.now().strftime("%Y-%m-%d")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyDailyTradeSummary":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TradeSummary:
    """交易统计摘要"""
    total_trades: int = 0              # 总交易次数
    buy_count: int = 0                 # 买入次数
    sell_count: int = 0                # 卖出次数
    total_buy_amount: float = 0.0      # 总买入金额
    total_sell_amount: float = 0.0     # 总卖出金额
    total_commission: float = 0.0      # 总手续费
    win_count: int = 0                 # 盈利次数（卖出价>买入价）
    loss_count: int = 0                # 亏损次数
    total_profit: float = 0.0          # 总盈亏
    win_rate: float = 0.0              # 胜率
    avg_profit: float = 0.0            # 平均盈亏
    max_profit: float = 0.0            # 最大单笔盈利
    max_loss: float = 0.0              # 最大单笔亏损
    

class TradeRecordService(QObject):
    """
    交易记录服务
    
    负责管理交易记录的存储、查询和统计
    
    信号：
        record_added: 新增记录信号
        records_changed: 记录变化信号
        log_message: 日志消息信号
    """
    
    record_added = pyqtSignal(object)  # TradeRecord
    records_changed = pyqtSignal()
    pnl_snapshot_saved = pyqtSignal(object)  # DailyPnlSnapshot
    log_message = pyqtSignal(str)
    
    DB_FILE = "trade_records.db"
    
    # 手续费率配置（可以在初始化时修改）
    COMMISSION_RATE = 0.00025  # 券商佣金 0.025%
    STAMP_TAX_RATE = 0.001     # 印花税 0.1%（仅卖出）
    TRANSFER_FEE_RATE = 0.00002  # 过户费 0.002%（仅上海）
    MIN_COMMISSION = 5.0       # 最低佣金
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 数据库路径
        self.data_dir = Path(__file__).parent.parent / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / self.DB_FILE
        
        # 初始化数据库
        self._init_database()
        self._init_pnl_table()
        self._init_position_snapshot_table()
        self._init_strategy_snapshot_tables()
        
        logger.info(f"交易记录服务初始化完成，数据库路径: {self.db_path}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _column_exists(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return any(str(row[1]) == column_name for row in cursor.fetchall())

    def _ensure_column(self, cursor: sqlite3.Cursor, table_name: str, column_name: str, definition: str) -> None:
        if not self._column_exists(cursor, table_name, column_name):
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
    
    def _init_database(self):
        """初始化数据库表"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 创建交易记录表（不含具体交易时间，只保留日期）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE NOT NULL,
                broker_order_id INTEGER DEFAULT -1,
                stock_code TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                direction TEXT NOT NULL,
                price REAL NOT NULL,
                volume INTEGER NOT NULL,
                amount REAL NOT NULL,
                commission REAL DEFAULT 0,
                stamp_tax REAL DEFAULT 0,
                transfer_fee REAL DEFAULT 0,
                trade_date TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                strategy_id TEXT DEFAULT '',
                virtual_account_id TEXT DEFAULT '',
                intent_id TEXT DEFAULT '',
                remark TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        ''')
        self._ensure_column(cursor, 'trades', 'strategy_id', "TEXT DEFAULT ''")
        self._ensure_column(cursor, 'trades', 'virtual_account_id', "TEXT DEFAULT ''")
        self._ensure_column(cursor, 'trades', 'intent_id', "TEXT DEFAULT ''")
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_date ON trades(trade_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_code ON trades(stock_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_direction ON trades(direction)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_source ON trades(source)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_strategy_id ON trades(strategy_id)')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                broker_order_id INTEGER DEFAULT -1,
                fingerprint TEXT DEFAULT '',
                stock_code TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                direction TEXT NOT NULL,
                price REAL DEFAULT 0,
                price_type INTEGER DEFAULT 0,
                order_volume INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual',
                trigger TEXT DEFAULT 'manual',
                strategy_name TEXT DEFAULT '',
                strategy_id TEXT DEFAULT '',
                virtual_account_id TEXT DEFAULT '',
                intent_id TEXT DEFAULT '',
                execution_mode TEXT DEFAULT 'live',
                status TEXT DEFAULT 'created',
                validation_message TEXT DEFAULT '',
                order_status_code INTEGER DEFAULT 0,
                order_status_text TEXT DEFAULT '',
                executed_price REAL DEFAULT 0,
                executed_volume INTEGER DEFAULT 0,
                linked_trade_record_id INTEGER DEFAULT 0,
                decision_record_id TEXT DEFAULT '',
                remark TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        self._ensure_column(cursor, 'order_records', 'strategy_id', "TEXT DEFAULT ''")
        self._ensure_column(cursor, 'order_records', 'virtual_account_id', "TEXT DEFAULT ''")
        self._ensure_column(cursor, 'order_records', 'intent_id', "TEXT DEFAULT ''")
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_request_id ON order_records(request_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_broker_order_id ON order_records(broker_order_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_fingerprint ON order_records(fingerprint)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_created_at ON order_records(created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_strategy_id ON order_records(strategy_id)')
        
        # 创建每日汇总视图（先删除再创建，以便更新结构）
        cursor.execute('DROP VIEW IF EXISTS daily_summary')
        cursor.execute('''
            CREATE VIEW daily_summary AS
            SELECT 
                trade_date,
                COUNT(*) as trade_count,
                SUM(CASE WHEN direction = 'buy' THEN 1 ELSE 0 END) as buy_count,
                SUM(CASE WHEN direction = 'sell' THEN 1 ELSE 0 END) as sell_count,
                SUM(CASE WHEN direction = 'buy' THEN amount ELSE 0 END) as total_buy,
                SUM(CASE WHEN direction = 'sell' THEN amount ELSE 0 END) as total_sell,
                SUM(commission + stamp_tax + transfer_fee) as total_fee
            FROM trades
            GROUP BY trade_date
            ORDER BY trade_date DESC
        ''')
        
        conn.commit()
        conn.close()
        
        logger.info("数据库表初始化完成")
    
    def _log(self, message: str):
        """发送日志"""
        logger.info(message)
        self.log_message.emit(f"[交易记录] {message}")
    
    def _trigger_auto_stop_loss(self, stock_code: str, stock_name: str, 
                                price: float, volume: int, source: str):
        """
        触发自动止损（买入成交后）
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            price: 买入价格（成本价）
            volume: 买入数量
            source: 交易来源
        """
        global _auto_stop_loss_service_getter
        if _auto_stop_loss_service_getter is None:
            return
        
        try:
            auto_stop_loss_service = _auto_stop_loss_service_getter()
            if auto_stop_loss_service and auto_stop_loss_service.is_enabled:
                auto_stop_loss_service.on_buy_trade_added(
                    stock_code, stock_name, price, volume, source
                )
        except Exception as e:
            logger.error(f"触发自动止损失败: {e}")
    
    def calculate_commission(self, direction: str, price: float, volume: int,
                            stock_code: str = "") -> float:
        """
        计算交易手续费
        
        Args:
            direction: 交易方向
            price: 成交价格
            volume: 成交数量
            stock_code: 股票代码（用于判断上海/深圳）
            
        Returns:
            预估手续费
        """
        amount = price * volume
        commission = 0.0
        
        # 券商佣金
        broker_fee = max(amount * self.COMMISSION_RATE, self.MIN_COMMISSION)
        commission += broker_fee
        
        # 印花税（仅卖出）
        if direction == TradeDirection.SELL.value:
            commission += amount * self.STAMP_TAX_RATE
        
        # 过户费（仅上海股票，6开头）
        if stock_code.startswith('6'):
            commission += amount * self.TRANSFER_FEE_RATE
        
        return round(commission, 2)
    
    def add_record(self,
                   stock_code: str,
                   stock_name: str,
                   direction: str,
                   price: float,
                   volume: int,
                   broker_order_id: int = -1,
                   trade_date: str = None,
                   source: str = "manual",
                   strategy_id: str = "",
                   virtual_account_id: str = "",
                   intent_id: str = "",
                   remark: str = "",
                   commission: float = None,
                   stamp_tax: float = None,
                   transfer_fee: float = None) -> Optional[TradeRecord]:
        """
        添加交易记录
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            direction: 交易方向 (buy/sell)
            price: 成交价格
            volume: 成交数量
            broker_order_id: 券商委托单号
            trade_date: 成交日期 (YYYY-MM-DD)
            source: 来源/策略
            remark: 备注
            commission: 手续费/佣金（可选，不传则自动计算）
            stamp_tax: 印花税（可选）
            transfer_fee: 过户费（可选）
            
        Returns:
            创建的交易记录对象
        """
        # 处理股票代码（去掉后缀）
        code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        
        # 处理日期
        if not trade_date:
            trade_date = datetime.now().strftime("%Y-%m-%d")
        
        # 计算金额
        amount = round(price * volume, 2)
        
        # 计算费用（如果未提供）
        if commission is None:
            commission = max(amount * self.COMMISSION_RATE, self.MIN_COMMISSION)
        if stamp_tax is None:
            stamp_tax = amount * self.STAMP_TAX_RATE if direction == TradeDirection.SELL.value else 0
        if transfer_fee is None:
            transfer_fee = amount * self.TRANSFER_FEE_RATE if code.startswith('6') else 0
        
        # 创建记录
        record = TradeRecord(
            broker_order_id=broker_order_id,
            stock_code=code,
            stock_name=stock_name,
            direction=direction,
            price=price,
            volume=volume,
            amount=amount,
            commission=round(commission, 2),
            stamp_tax=round(stamp_tax, 2),
            transfer_fee=round(transfer_fee, 2),
            trade_date=trade_date,
            source=source,
            strategy_id=strategy_id,
            virtual_account_id=virtual_account_id,
            intent_id=intent_id,
            remark=remark
        )
        
        # 保存到数据库
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO trades (
                    trade_id, broker_order_id, stock_code, stock_name, direction,
                    price, volume, amount, commission, stamp_tax, transfer_fee,
                    trade_date, source, strategy_id, virtual_account_id, intent_id, remark, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                record.trade_id, record.broker_order_id, record.stock_code,
                record.stock_name, record.direction, record.price, record.volume,
                record.amount, record.commission, record.stamp_tax, record.transfer_fee,
                record.trade_date, record.source, record.strategy_id, record.virtual_account_id,
                record.intent_id, record.remark, record.created_at
            ))
            
            record.id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            self._log(f"新增交易记录: {stock_name}({code}) {record.direction_display} "
                     f"{volume}股 @ {price:.3f}")
            
            self.record_added.emit(record)
            self.records_changed.emit()
            
            # 触发自动止损（仅买入时）
            if direction == TradeDirection.BUY.value:
                self._trigger_auto_stop_loss(code, stock_name, price, volume, source)
            
            return record
            
        except sqlite3.IntegrityError as e:
            logger.warning(f"交易记录已存在: {record.trade_id}")
            return None
        except Exception as e:
            logger.error(f"保存交易记录失败: {e}")
            return None

    def add_order_record(
        self,
        *,
        request_id: str,
        stock_code: str,
        stock_name: str,
        direction: str,
        order_volume: int,
        price: float,
        price_type: int,
        source: str,
        trigger: str,
        strategy_name: str = "",
        strategy_id: str = "",
        virtual_account_id: str = "",
        intent_id: str = "",
        execution_mode: str = "live",
        status: str = "created",
        broker_order_id: int = -1,
        fingerprint: str = "",
        decision_record_id: str = "",
        remark: str = "",
        validation_message: str = "",
    ) -> Optional[OrderRecord]:
        code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        record = OrderRecord(
            request_id=request_id,
            broker_order_id=broker_order_id,
            fingerprint=fingerprint,
            stock_code=code,
            stock_name=stock_name,
            direction=direction,
            price=price,
            price_type=price_type,
            order_volume=order_volume,
            source=source,
            trigger=trigger,
            strategy_name=strategy_name,
            strategy_id=strategy_id,
            virtual_account_id=virtual_account_id,
            intent_id=intent_id,
            execution_mode=execution_mode,
            status=status,
            validation_message=validation_message,
            decision_record_id=decision_record_id,
            remark=remark,
        )
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO order_records (
                    request_id, broker_order_id, fingerprint, stock_code, stock_name,
                    direction, price, price_type, order_volume, source, trigger,
                    strategy_name, strategy_id, virtual_account_id, intent_id,
                    execution_mode, status, validation_message,
                    order_status_code, order_status_text, executed_price, executed_volume,
                    linked_trade_record_id, decision_record_id, remark, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    record.request_id, record.broker_order_id, record.fingerprint, record.stock_code,
                    record.stock_name, record.direction, record.price, record.price_type,
                    record.order_volume, record.source, record.trigger, record.strategy_name,
                    record.strategy_id, record.virtual_account_id, record.intent_id,
                    record.execution_mode, record.status, record.validation_message,
                    record.order_status_code, record.order_status_text, record.executed_price,
                    record.executed_volume, record.linked_trade_record_id, record.decision_record_id,
                    record.remark, record.created_at, record.updated_at,
                ),
            )
            record.id = cursor.lastrowid
            conn.commit()
            conn.close()
            return record
        except sqlite3.IntegrityError:
            logger.warning("委托记录 request_id 已存在: %s", request_id)
            return self.get_order_record_by_request_id(request_id)
        except Exception as e:
            logger.error("保存委托记录失败: %s", e)
            return None

    def update_order_record(self, request_id: str, **fields) -> bool:
        if not request_id:
            return False
        if not fields:
            return False
        allowed = {
            "broker_order_id", "fingerprint", "stock_code", "stock_name", "direction",
            "price", "price_type", "order_volume", "source", "trigger", "strategy_name",
            "strategy_id", "virtual_account_id", "intent_id",
            "execution_mode", "status", "validation_message", "order_status_code",
            "order_status_text", "executed_price", "executed_volume", "linked_trade_record_id",
            "decision_record_id", "remark",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = ", ".join(f"{key} = ?" for key in updates.keys())
        params = list(updates.values()) + [request_id]
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"UPDATE order_records SET {sql} WHERE request_id = ?", params)
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed
        except Exception as e:
            logger.error("更新委托记录失败: %s", e)
            return False

    def get_order_record_by_request_id(self, request_id: str) -> Optional[OrderRecord]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM order_records WHERE request_id = ? LIMIT 1", (request_id,))
        row = cursor.fetchone()
        conn.close()
        return OrderRecord.from_dict(dict(row)) if row else None

    def find_recent_order_record(self, fingerprint: str, within_seconds: int = 30) -> Optional[OrderRecord]:
        if not fingerprint:
            return None
        cutoff = (datetime.now() - timedelta(seconds=max(int(within_seconds), 1))).strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM order_records
            WHERE fingerprint = ? AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (fingerprint, cutoff),
        )
        row = cursor.fetchone()
        conn.close()
        return OrderRecord.from_dict(dict(row)) if row else None

    def get_order_records(
        self,
        *,
        start_time: str = "",
        end_time: str = "",
        status: str = "",
        source: str = "",
        limit: int = 1000,
    ) -> List[OrderRecord]:
        conn = self._get_connection()
        cursor = conn.cursor()
        conditions = []
        params = []
        if start_time:
            conditions.append("created_at >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("created_at <= ?")
            params.append(end_time)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if source:
            conditions.append("source = ?")
            params.append(source)
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        cursor.execute(
            f"SELECT * FROM order_records WHERE {where_clause} ORDER BY created_at DESC, id DESC LIMIT ?",
            [*params, limit],
        )
        rows = cursor.fetchall()
        conn.close()
        return [OrderRecord.from_dict(dict(row)) for row in rows]

    def _infer_strategy_identity(
        self,
        stock_code: str,
        *,
        strategy_id: str = "",
        virtual_account_id: str = "",
        intent_id: str = "",
    ) -> tuple[str, str, str]:
        if strategy_id:
            return strategy_id, virtual_account_id, intent_id
        try:
            from .strategy_registry_service import get_strategy_registry_service

            owner = get_strategy_registry_service().get_owner(stock_code)
        except Exception:
            owner = None
        if owner is None:
            return "", "", intent_id
        return owner.strategy_id, owner.virtual_account_id, intent_id

    def sync_order_records_from_orders(self, orders: list) -> int:
        updated = 0
        if not orders:
            return updated
        for order in orders:
            try:
                order_id = int(getattr(order, "order_id", 0) or 0)
                if order_id <= 0:
                    continue
                status_code = int(getattr(order, "order_status", 0) or 0)
                traded_volume = int(getattr(order, "traded_volume", 0) or 0)
                traded_price = float(getattr(order, "traded_price", 0) or 0)
                status_text = {
                    48: "未报",
                    49: "待报",
                    50: "已报",
                    51: "已报待撤",
                    52: "部成待撤",
                    53: "部撤",
                    54: "已撤",
                    55: "部成",
                    56: "已成",
                    57: "废单",
                }.get(status_code, str(status_code))
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT request_id FROM order_records WHERE broker_order_id = ? ORDER BY id DESC LIMIT 1",
                    (order_id,),
                )
                row = cursor.fetchone()
                conn.close()
                if not row:
                    continue
                request_id = str(row["request_id"])
                ok = self.update_order_record(
                    request_id,
                    status=_BROKER_ORDER_STATUS_MAP.get(status_code, "submitted"),
                    order_status_code=status_code,
                    order_status_text=status_text,
                    executed_price=traded_price,
                    executed_volume=traded_volume,
                    validation_message=getattr(order, "status_msg", "") or status_text,
                )
                if ok:
                    updated += 1
            except Exception as exc:
                logger.debug("同步委托记录状态失败: %s", exc)
        return updated
    
    def is_trade_exists(self, traded_id, trade_date: str = None) -> bool:
        """
        检查成交记录是否已存在（基于券商成交ID）
        
        Args:
            traded_id: 券商成交ID（int或str）
            trade_date: 成交日期（可选，用于更精确匹配）
            
        Returns:
            是否已存在
        """
        # 转换为字符串进行匹配
        traded_id_str = str(traded_id) if traded_id else ""
        if not traded_id_str or traded_id_str == "0":
            return False
            
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 使用 remark 字段存储的 traded_id 来检查
        # remark 格式: "成交号:123456"
        if trade_date:
            cursor.execute(
                "SELECT 1 FROM trades WHERE remark LIKE ? AND trade_date = ? LIMIT 1",
                (f"成交号:{traded_id_str}%", trade_date)
            )
        else:
            cursor.execute(
                "SELECT 1 FROM trades WHERE remark LIKE ? LIMIT 1",
                (f"成交号:{traded_id_str}%",)
            )
        
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    
    def sync_broker_trades(
        self,
        broker_trades: list,
        source: str = "broker_sync",
        *,
        strategy_id: str = "",
        virtual_account_id: str = "",
        intent_id: str = "",
    ) -> int:
        """
        同步券商成交回报到本地数据库
        
        基于券商的 traded_id 进行去重，只新增不存在的记录。
        
        Args:
            broker_trades: 券商成交回报列表，每个元素应有以下属性：
                - traded_id: 成交编号
                - stock_code: 股票代码
                - stock_name: 股票名称（可选）
                - order_type: 委托类型（23=买入, 24=卖出）
                - traded_price: 成交价格
                - traded_volume: 成交数量
                - traded_amount: 成交金额
                - traded_time: 成交时间（可选）
            source: 来源标记
            
        Returns:
            新增的记录数量
        """
        added_count = 0
        today = datetime.now().strftime("%Y-%m-%d")
        
        for trade in broker_trades:
            try:
                # 获取成交ID（可能是字符串或整数）
                traded_id_raw = getattr(trade, 'traded_id', 0)
                try:
                    traded_id = int(traded_id_raw) if traded_id_raw else 0
                except (ValueError, TypeError):
                    traded_id = 0
                    
                stock_code_raw = getattr(trade, 'stock_code', '')
                
                if traded_id <= 0:
                    continue
                
                # 检查是否已存在
                trade_date = self._normalize_broker_time_to_date(
                    getattr(trade, 'traded_time', None),
                    today,
                )
                if self.is_trade_exists(traded_id, trade_date):
                    continue
                
                # 解析交易数据
                stock_code = str(stock_code_raw).split('.')[0]
                stock_name = getattr(trade, 'stock_name', '') or stock_code
                inferred_strategy_id, inferred_virtual_account_id, inferred_intent_id = self._infer_strategy_identity(
                    stock_code,
                    strategy_id=strategy_id,
                    virtual_account_id=virtual_account_id,
                    intent_id=intent_id,
                )
                order_type = getattr(trade, 'order_type', 0)
                direction = TradeDirection.BUY.value if order_type == 23 else TradeDirection.SELL.value
                price = float(getattr(trade, 'traded_price', 0))
                volume = int(getattr(trade, 'traded_volume', 0))
                amount = float(getattr(trade, 'traded_amount', 0)) or round(price * volume, 2)
                
                if price <= 0 or volume <= 0:
                    continue
                
                # 计算费用
                commission = max(amount * self.COMMISSION_RATE, self.MIN_COMMISSION)
                stamp_tax = amount * self.STAMP_TAX_RATE if direction == TradeDirection.SELL.value else 0
                transfer_fee = amount * self.TRANSFER_FEE_RATE if stock_code.startswith('6') else 0
                
                # 添加记录
                record = self.add_record(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    direction=direction,
                    price=price,
                    volume=volume,
                    broker_order_id=int(getattr(trade, 'order_id', 0) or 0),
                    trade_date=trade_date,
                    source=source,
                    strategy_id=inferred_strategy_id,
                    virtual_account_id=inferred_virtual_account_id,
                    intent_id=inferred_intent_id,
                    remark=f"成交号:{traded_id}",
                    commission=round(commission, 2),
                    stamp_tax=round(stamp_tax, 2),
                    transfer_fee=round(transfer_fee, 2)
                )
                
                if record:
                    added_count += 1
                    
            except Exception as e:
                logger.error(f"同步成交记录失败: {e}")
                continue
        
        if added_count > 0:
            self._log(f"同步成交记录完成，新增 {added_count} 条")
        
        return added_count
    
    def sync_from_orders(
        self,
        orders: list,
        source: str = "broker_sync",
        name_map: dict = None,
        *,
        strategy_id: str = "",
        virtual_account_id: str = "",
        intent_id: str = "",
    ) -> int:
        """
        从委托数据中同步已成交的记录
        
        筛选状态为"已成交(56)"或"部成(55)"的委托，同步到交易记录。
        使用 order_id 作为去重标识。
        
        Args:
            orders: 委托列表
            source: 来源标记
            name_map: 股票代码到名称的映射表
            
        Returns:
            新增的记录数量
        """
        added_count = 0
        today = datetime.now().strftime("%Y-%m-%d")
        name_map = name_map or {}
        
        # 已成交状态：55=部成, 56=已成
        filled_statuses = [55, 56]
        
        for order in orders:
            try:
                # 检查委托状态
                order_status = getattr(order, 'order_status', 0)
                if order_status not in filled_statuses:
                    continue
                
                # 获取委托ID
                order_id = getattr(order, 'order_id', 0)
                if not order_id:
                    continue
                
                # 检查是否已存在（使用委托号去重）
                trade_date = self._normalize_broker_time_to_date(
                    getattr(order, 'traded_time', None) or getattr(order, 'order_time', None),
                    today,
                )
                if self._is_order_synced(order_id, trade_date):
                    continue
                
                # 解析交易数据
                stock_code = str(getattr(order, 'stock_code', '')).split('.')[0]
                inferred_strategy_id, inferred_virtual_account_id, inferred_intent_id = self._infer_strategy_identity(
                    stock_code,
                    strategy_id=strategy_id,
                    virtual_account_id=virtual_account_id,
                    intent_id=intent_id,
                )
                
                # 获取股票名称：优先从name_map获取，其次从委托数据，最后用xtdata
                stock_name = name_map.get(stock_code, '')
                if not stock_name:
                    stock_name = getattr(order, 'stock_name', '') or ''
                if not stock_name:
                    # 尝试使用 xtdata.get_instrument_detail 获取
                    try:
                        from xtquant import xtdata
                        xt_code = f"{stock_code}.SH" if stock_code.startswith(('5', '6', '9')) else f"{stock_code}.SZ"
                        detail = xtdata.get_instrument_detail(xt_code)
                        stock_name = detail.get('InstrumentName', stock_code) if detail else stock_code
                    except:
                        stock_name = stock_code
                
                order_type = getattr(order, 'order_type', 0)
                direction = TradeDirection.BUY.value if order_type == 23 else TradeDirection.SELL.value
                
                # 成交价格和数量
                price = float(getattr(order, 'traded_price', 0))
                volume = int(getattr(order, 'traded_volume', 0))
                
                if price <= 0 or volume <= 0:
                    continue
                
                amount = round(price * volume, 2)
                
                # 计算费用
                commission = max(amount * self.COMMISSION_RATE, self.MIN_COMMISSION)
                stamp_tax = amount * self.STAMP_TAX_RATE if direction == TradeDirection.SELL.value else 0
                transfer_fee = amount * self.TRANSFER_FEE_RATE if stock_code.startswith('6') else 0
                
                # 添加记录
                record = self.add_record(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    direction=direction,
                    price=price,
                    volume=volume,
                    broker_order_id=int(order_id or 0),
                    trade_date=trade_date,
                    source=source,
                    strategy_id=inferred_strategy_id,
                    virtual_account_id=inferred_virtual_account_id,
                    intent_id=inferred_intent_id,
                    remark=f"委托号:{order_id}",
                    commission=round(commission, 2),
                    stamp_tax=round(stamp_tax, 2),
                    transfer_fee=round(transfer_fee, 2)
                )
                
                if record:
                    added_count += 1
                    
            except Exception as e:
                logger.error(f"从委托同步记录失败: {e}")
                continue
        
        if added_count > 0:
            self._log(f"同步成交记录完成，新增 {added_count} 条")
        
        return added_count

    def save_daily_position_snapshots(self, snapshot_date: str, positions: list) -> int:
        saved_count = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('DELETE FROM daily_position_snapshots WHERE snapshot_date = ?', (snapshot_date,))
            for pos in positions or []:
                volume = int(getattr(pos, "volume", 0) or 0)
                if volume <= 0:
                    continue
                stock_code = str(getattr(pos, "stock_code", "") or "").split(".")[0]
                if not stock_code:
                    continue
                cursor.execute(
                    '''
                    INSERT INTO daily_position_snapshots (
                        snapshot_date, stock_code, stock_name, volume, can_use_volume,
                        open_price, market_value, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        snapshot_date,
                        stock_code,
                        getattr(pos, "stock_name", "") or stock_code,
                        volume,
                        int(getattr(pos, "can_use_volume", 0) or 0),
                        round(float(getattr(pos, "open_price", 0) or 0), 4),
                        round(float(getattr(pos, "market_value", 0) or 0), 2),
                        now,
                    ),
                )
                saved_count += 1
            conn.commit()
        finally:
            conn.close()
        if saved_count > 0:
            self._log(f"保存日终持仓快照完成，共 {saved_count} 条")
        return saved_count

    def get_daily_position_snapshots(self, snapshot_date: str) -> List[DailyPositionSnapshot]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM daily_position_snapshots WHERE snapshot_date = ? ORDER BY stock_code ASC',
            (snapshot_date,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [DailyPositionSnapshot.from_dict(dict(row)) for row in rows]

    def get_daily_position_snapshot_count(self, snapshot_date: str) -> int:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COUNT(*) FROM daily_position_snapshots WHERE snapshot_date = ?',
            (snapshot_date,),
        )
        count = int(cursor.fetchone()[0] or 0)
        conn.close()
        return count

    def save_strategy_position_snapshots(
        self,
        snapshot_date: str,
        positions_by_strategy: Dict[str, Dict[str, Any]],
    ) -> int:
        saved_count = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('DELETE FROM strategy_position_snapshots WHERE snapshot_date = ?', (snapshot_date,))
            for strategy_id, payload in (positions_by_strategy or {}).items():
                strategy_name = str(payload.get("strategy_name", "") or "")
                virtual_account_id = str(payload.get("virtual_account_id", "") or "")
                for pos in payload.get("positions", []) or []:
                    volume = int(pos.get("volume", 0) or 0)
                    if volume <= 0:
                        continue
                    stock_code = str(pos.get("stock_code", "") or "").split(".")[0]
                    if not stock_code:
                        continue
                    cursor.execute(
                        '''
                        INSERT INTO strategy_position_snapshots (
                            snapshot_date, strategy_id, strategy_name, virtual_account_id,
                            stock_code, stock_name, volume, can_use_volume, open_price,
                            market_value, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            snapshot_date,
                            strategy_id,
                            strategy_name,
                            virtual_account_id,
                            stock_code,
                            pos.get("stock_name", "") or stock_code,
                            volume,
                            int(pos.get("can_use_volume", 0) or 0),
                            round(float(pos.get("open_price", 0) or 0), 4),
                            round(float(pos.get("market_value", 0) or 0), 2),
                            now,
                        ),
                    )
                    saved_count += 1
            conn.commit()
        finally:
            conn.close()
        return saved_count

    def save_strategy_daily_pnl_snapshot(
        self,
        *,
        snapshot_date: str,
        strategy_id: str,
        strategy_name: str,
        virtual_account_id: str,
        total_asset: float,
        cash: float,
        market_value: float,
        position_count: int = 0,
        remark: str = "",
    ) -> Optional[StrategyDailyPnlSnapshot]:
        snapshot = StrategyDailyPnlSnapshot(
            snapshot_date=snapshot_date,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            total_asset=round(total_asset, 2),
            cash=round(cash, 2),
            market_value=round(market_value, 2),
            position_count=int(position_count or 0),
            remark=remark,
        )
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO strategy_daily_pnl (
                    snapshot_date, strategy_id, strategy_name, virtual_account_id,
                    total_asset, cash, market_value, position_count, remark, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, strategy_id) DO UPDATE SET
                    strategy_name = excluded.strategy_name,
                    virtual_account_id = excluded.virtual_account_id,
                    total_asset = excluded.total_asset,
                    cash = excluded.cash,
                    market_value = excluded.market_value,
                    position_count = excluded.position_count,
                    remark = excluded.remark,
                    created_at = excluded.created_at
                ''',
                (
                    snapshot.snapshot_date,
                    snapshot.strategy_id,
                    snapshot.strategy_name,
                    snapshot.virtual_account_id,
                    snapshot.total_asset,
                    snapshot.cash,
                    snapshot.market_value,
                    snapshot.position_count,
                    snapshot.remark,
                    snapshot.created_at,
                ),
            )
            conn.commit()
            conn.close()
            return snapshot
        except Exception as exc:
            logger.error("保存策略日终权益快照失败: %s", exc)
            return None

    def save_strategy_daily_trade_summary(
        self,
        *,
        snapshot_date: str,
        strategy_id: str,
        strategy_name: str,
        virtual_account_id: str,
        trade_count: int,
        buy_count: int,
        sell_count: int,
        total_buy_amount: float,
        total_sell_amount: float,
        total_commission: float,
        remark: str = "",
    ) -> Optional[StrategyDailyTradeSummary]:
        summary = StrategyDailyTradeSummary(
            snapshot_date=snapshot_date,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            trade_count=int(trade_count or 0),
            buy_count=int(buy_count or 0),
            sell_count=int(sell_count or 0),
            total_buy_amount=round(total_buy_amount, 2),
            total_sell_amount=round(total_sell_amount, 2),
            total_commission=round(total_commission, 2),
            remark=remark,
        )
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO strategy_daily_trade_summary (
                    snapshot_date, strategy_id, strategy_name, virtual_account_id,
                    trade_count, buy_count, sell_count, total_buy_amount,
                    total_sell_amount, total_commission, remark, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, strategy_id) DO UPDATE SET
                    strategy_name = excluded.strategy_name,
                    virtual_account_id = excluded.virtual_account_id,
                    trade_count = excluded.trade_count,
                    buy_count = excluded.buy_count,
                    sell_count = excluded.sell_count,
                    total_buy_amount = excluded.total_buy_amount,
                    total_sell_amount = excluded.total_sell_amount,
                    total_commission = excluded.total_commission,
                    remark = excluded.remark,
                    created_at = excluded.created_at
                ''',
                (
                    summary.snapshot_date,
                    summary.strategy_id,
                    summary.strategy_name,
                    summary.virtual_account_id,
                    summary.trade_count,
                    summary.buy_count,
                    summary.sell_count,
                    summary.total_buy_amount,
                    summary.total_sell_amount,
                    summary.total_commission,
                    summary.remark,
                    summary.created_at,
                ),
            )
            conn.commit()
            conn.close()
            return summary
        except Exception as exc:
            logger.error("保存策略日成交汇总失败: %s", exc)
            return None

    def summarize_trades_by_strategy(self, snapshot_date: str) -> List[dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT
                strategy_id,
                MAX(COALESCE(strategy_id, '')) AS strategy_id_max,
                MAX(COALESCE(virtual_account_id, '')) AS virtual_account_id,
                COUNT(*) AS trade_count,
                SUM(CASE WHEN direction = 'buy' THEN 1 ELSE 0 END) AS buy_count,
                SUM(CASE WHEN direction = 'sell' THEN 1 ELSE 0 END) AS sell_count,
                SUM(CASE WHEN direction = 'buy' THEN amount ELSE 0 END) AS total_buy_amount,
                SUM(CASE WHEN direction = 'sell' THEN amount ELSE 0 END) AS total_sell_amount,
                SUM(commission + stamp_tax + transfer_fee) AS total_commission
            FROM trades
            WHERE trade_date = ? AND COALESCE(strategy_id, '') != ''
            GROUP BY strategy_id
            ORDER BY strategy_id ASC
            ''',
            (snapshot_date,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def count_order_records_by_broker_ids(self, broker_order_ids: List[int]) -> int:
        ids = [int(order_id) for order_id in broker_order_ids if int(order_id or 0) > 0]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT COUNT(DISTINCT broker_order_id) FROM order_records WHERE broker_order_id IN ({placeholders})',
            ids,
        )
        count = int(cursor.fetchone()[0] or 0)
        conn.close()
        return count

    def count_trade_records_by_trade_ids(self, traded_ids: List[int], trade_date: str) -> int:
        ids = [int(traded_id) for traded_id in traded_ids if int(traded_id or 0) > 0]
        if not ids:
            return 0
        conn = self._get_connection()
        cursor = conn.cursor()
        total = 0
        for traded_id in ids:
            cursor.execute(
                "SELECT 1 FROM trades WHERE remark LIKE ? AND trade_date = ? LIMIT 1",
                (f"成交号:{traded_id}%", trade_date),
            )
            if cursor.fetchone():
                total += 1
        conn.close()
        return total
    
    def _is_order_synced(self, order_id, trade_date: str = None) -> bool:
        """检查委托是否已同步（基于委托号）"""
        order_id_str = str(order_id) if order_id else ""
        if not order_id_str:
            return False
            
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 检查 remark 中是否已有该委托号
        if trade_date:
            cursor.execute(
                "SELECT 1 FROM trades WHERE remark LIKE ? AND trade_date = ? LIMIT 1",
                (f"委托号:{order_id_str}%", trade_date)
            )
        else:
            cursor.execute(
                "SELECT 1 FROM trades WHERE remark LIKE ? LIMIT 1",
                (f"委托号:{order_id_str}%",)
            )
        
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    
    def get_record_by_id(self, record_id: int) -> Optional[TradeRecord]:
        """根据ID获取记录"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM trades WHERE id = ?', (record_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return TradeRecord.from_dict(dict(row))
        return None

    def get_records_by_broker_order_id(
        self,
        broker_order_id: int,
        *,
        source: str = None,
        stock_code: str = None,
    ) -> List[TradeRecord]:
        """根据券商委托单号查询交易记录。"""
        if broker_order_id is None or int(broker_order_id) <= 0:
            return []

        conn = self._get_connection()
        cursor = conn.cursor()

        conditions = ["broker_order_id = ?"]
        params = [int(broker_order_id)]

        if source:
            conditions.append("source = ?")
            params.append(source)
        if stock_code:
            code = stock_code.split('.')[0] if '.' in stock_code else stock_code
            conditions.append("stock_code = ?")
            params.append(code)

        where_clause = " AND ".join(conditions)
        cursor.execute(
            f'''
            SELECT * FROM trades
            WHERE {where_clause}
            ORDER BY trade_date ASC, id ASC
            '''
            ,
            params,
        )
        rows = cursor.fetchall()
        conn.close()
        return [TradeRecord.from_dict(dict(row)) for row in rows]

    def get_latest_record_by_broker_order_id(
        self,
        broker_order_id: int,
        *,
        source: str = None,
        stock_code: str = None,
    ) -> Optional[TradeRecord]:
        """根据券商委托单号获取最新一条交易记录。"""
        records = self.get_records_by_broker_order_id(
            broker_order_id,
            source=source,
            stock_code=stock_code,
        )
        return records[-1] if records else None
    
    def get_records(self,
                    start_date: str = None,
                    end_date: str = None,
                    stock_code: str = None,
                    direction: str = None,
                    source: str = None,
                    limit: int = 1000,
                    offset: int = 0) -> List[TradeRecord]:
        """
        查询交易记录
        
        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            stock_code: 股票代码
            direction: 交易方向
            source: 来源
            limit: 最大返回数量
            offset: 偏移量
            
        Returns:
            交易记录列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 构建查询条件
        conditions = []
        params = []
        
        if start_date:
            conditions.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("trade_date <= ?")
            params.append(end_date)
        if stock_code:
            code = stock_code.split('.')[0] if '.' in stock_code else stock_code
            conditions.append("stock_code = ?")
            params.append(code)
        if direction:
            conditions.append("direction = ?")
            params.append(direction)
        if source:
            conditions.append("source = ?")
            params.append(source)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        query = f'''
            SELECT * FROM trades 
            WHERE {where_clause}
            ORDER BY trade_date DESC, id DESC
            LIMIT ? OFFSET ?
        '''
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [TradeRecord.from_dict(dict(row)) for row in rows]
    
    def get_records_count(self,
                         start_date: str = None,
                         end_date: str = None,
                         stock_code: str = None,
                         direction: str = None,
                         source: str = None) -> int:
        """获取符合条件的记录总数"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        conditions = []
        params = []
        
        if start_date:
            conditions.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("trade_date <= ?")
            params.append(end_date)
        if stock_code:
            code = stock_code.split('.')[0] if '.' in stock_code else stock_code
            conditions.append("stock_code = ?")
            params.append(code)
        if direction:
            conditions.append("direction = ?")
            params.append(direction)
        if source:
            conditions.append("source = ?")
            params.append(source)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        cursor.execute(f'SELECT COUNT(*) FROM trades WHERE {where_clause}', params)
        count = cursor.fetchone()[0]
        conn.close()
        
        return count
    
    def get_today_records(self) -> List[TradeRecord]:
        """获取今日交易记录"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.get_records(start_date=today, end_date=today)
    
    def get_stock_records(self, stock_code: str, limit: int = 100) -> List[TradeRecord]:
        """获取指定股票的交易记录"""
        return self.get_records(stock_code=stock_code, limit=limit)
    
    def get_daily_summary(self, days: int = 30) -> List[dict]:
        """
        获取每日交易汇总
        
        Args:
            days: 最近天数
            
        Returns:
            每日汇总列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        cursor.execute('''
            SELECT * FROM daily_summary 
            WHERE trade_date >= ?
            ORDER BY trade_date DESC
        ''', (start_date,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_statistics(self, 
                      start_date: str = None,
                      end_date: str = None,
                      stock_code: str = None) -> TradeSummary:
        """
        获取交易统计
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            stock_code: 股票代码（可选）
            
        Returns:
            交易统计摘要
        """
        records = self.get_records(
            start_date=start_date,
            end_date=end_date,
            stock_code=stock_code,
            limit=100000
        )
        
        summary = TradeSummary()
        summary.total_trades = len(records)
        
        # 按股票分组计算盈亏
        stock_trades: Dict[str, List[TradeRecord]] = {}
        
        for record in records:
            if record.direction == TradeDirection.BUY.value:
                summary.buy_count += 1
                summary.total_buy_amount += record.amount
            else:
                summary.sell_count += 1
                summary.total_sell_amount += record.amount
            
            summary.total_commission += record.total_fee
            
            # 分组
            if record.stock_code not in stock_trades:
                stock_trades[record.stock_code] = []
            stock_trades[record.stock_code].append(record)
        
        # 计算每只股票的盈亏
        profits = []
        for code, trades in stock_trades.items():
            # 按日期排序
            trades.sort(key=lambda x: (x.trade_date, x.id))
            
            # 简单计算：卖出金额 - 买入金额
            buy_amount = sum(t.amount for t in trades if t.direction == TradeDirection.BUY.value)
            sell_amount = sum(t.amount for t in trades if t.direction == TradeDirection.SELL.value)
            
            if sell_amount > 0:  # 有卖出才计算盈亏
                profit = sell_amount - buy_amount
                profits.append(profit)
                
                if profit > 0:
                    summary.win_count += 1
                elif profit < 0:
                    summary.loss_count += 1
        
        # 计算统计指标
        if profits:
            summary.total_profit = sum(profits)
            summary.avg_profit = summary.total_profit / len(profits)
            summary.max_profit = max(profits) if profits else 0
            summary.max_loss = min(profits) if profits else 0
        
        if summary.win_count + summary.loss_count > 0:
            summary.win_rate = summary.win_count / (summary.win_count + summary.loss_count) * 100
        
        return summary
    
    def get_stock_holding_cost(self, stock_code: str) -> Tuple[float, int]:
        """
        计算指定股票的持仓成本
        
        Args:
            stock_code: 股票代码
            
        Returns:
            (平均成本价, 当前持仓数量)
        """
        records = self.get_stock_records(stock_code, limit=10000)
        records.sort(key=lambda x: (x.trade_date, x.id))
        
        total_cost = 0.0
        total_volume = 0
        
        for record in records:
            if record.direction == TradeDirection.BUY.value:
                total_cost += record.amount + record.total_fee
                total_volume += record.volume
            else:  # SELL
                if total_volume > 0:
                    avg_cost = total_cost / total_volume
                    sell_volume = min(record.volume, total_volume)
                    total_cost -= avg_cost * sell_volume
                    total_volume -= sell_volume
        
        avg_price = total_cost / total_volume if total_volume > 0 else 0
        return round(avg_price, 3), total_volume
    
    def delete_record(self, record_id: int) -> bool:
        """删除交易记录"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM trades WHERE id = ?', (record_id,))
            affected = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            if affected > 0:
                self._log(f"删除交易记录: ID={record_id}")
                self.records_changed.emit()
                return True
            return False
            
        except Exception as e:
            logger.error(f"删除交易记录失败: {e}")
            return False
    
    def export_to_csv(self, file_path: str,
                     start_date: str = None,
                     end_date: str = None) -> bool:
        """
        导出交易记录到 CSV 文件
        
        Args:
            file_path: 导出文件路径
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            是否成功
        """
        try:
            import csv
            
            records = self.get_records(start_date=start_date, end_date=end_date, limit=100000)
            
            if not records:
                logger.warning("没有可导出的记录")
                return False
            
            headers = [
                '交易日期', '股票代码', '股票名称', '方向',
                '价格', '数量', '金额', '手续费', '印花税', '过户费', '来源', '备注'
            ]
            
            with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                
                for r in records:
                    writer.writerow([
                        r.trade_date, r.stock_code, r.stock_name,
                        r.direction_display, f"{r.price:.4f}", r.volume,
                        f"{r.amount:.2f}", f"{r.commission:.2f}",
                        f"{r.stamp_tax:.2f}", f"{r.transfer_fee:.2f}",
                        r.source_display, r.remark
                    ])
            
            self._log(f"导出 {len(records)} 条记录到 {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"导出CSV失败: {e}")
            return False
    
    # ==================== Daily PnL Snapshot Methods ====================
    
    def _init_pnl_table(self):
        """Initialize daily_pnl snapshot table"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_pnl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT UNIQUE NOT NULL,
                total_asset REAL DEFAULT 0,
                cash REAL DEFAULT 0,
                market_value REAL DEFAULT 0,
                total_profit REAL DEFAULT 0,
                total_profit_pct REAL DEFAULT 0,
                cumulative_return REAL DEFAULT 0,
                position_count INTEGER DEFAULT 0,
                remark TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_pnl_date ON daily_pnl(snapshot_date)')
        
        conn.commit()
        conn.close()
        logger.info("daily_pnl table initialized")

    def _init_position_snapshot_table(self):
        """Initialize daily position snapshot table"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_position_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                volume INTEGER DEFAULT 0,
                can_use_volume INTEGER DEFAULT 0,
                open_price REAL DEFAULT 0,
                market_value REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_date, stock_code)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_position_snapshot_date ON daily_position_snapshots(snapshot_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_position_snapshot_code ON daily_position_snapshots(stock_code)')

        conn.commit()
        conn.close()
        logger.info("daily_position_snapshots table initialized")

    def _init_strategy_snapshot_tables(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS strategy_daily_pnl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_name TEXT DEFAULT '',
                virtual_account_id TEXT DEFAULT '',
                total_asset REAL DEFAULT 0,
                cash REAL DEFAULT 0,
                market_value REAL DEFAULT 0,
                position_count INTEGER DEFAULT 0,
                remark TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_date, strategy_id)
            )
            '''
        )
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS strategy_position_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_name TEXT DEFAULT '',
                virtual_account_id TEXT DEFAULT '',
                stock_code TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                volume INTEGER DEFAULT 0,
                can_use_volume INTEGER DEFAULT 0,
                open_price REAL DEFAULT 0,
                market_value REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_date, strategy_id, stock_code)
            )
            '''
        )
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS strategy_daily_trade_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_name TEXT DEFAULT '',
                virtual_account_id TEXT DEFAULT '',
                trade_count INTEGER DEFAULT 0,
                buy_count INTEGER DEFAULT 0,
                sell_count INTEGER DEFAULT 0,
                total_buy_amount REAL DEFAULT 0,
                total_sell_amount REAL DEFAULT 0,
                total_commission REAL DEFAULT 0,
                remark TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_date, strategy_id)
            )
            '''
        )
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_strategy_pnl_date ON strategy_daily_pnl(snapshot_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_strategy_pnl_id ON strategy_daily_pnl(strategy_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_strategy_position_date ON strategy_position_snapshots(snapshot_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_strategy_position_id ON strategy_position_snapshots(strategy_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_strategy_trade_summary_date ON strategy_daily_trade_summary(snapshot_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_strategy_trade_summary_id ON strategy_daily_trade_summary(strategy_id)')
        conn.commit()
        conn.close()
        logger.info("strategy snapshot tables initialized")

    @staticmethod
    def _normalize_broker_time_to_date(raw_value, fallback_date: str) -> str:
        if raw_value in (None, "", 0, "0"):
            return fallback_date
        value = str(raw_value).strip()
        digits = "".join(ch for ch in value if ch.isdigit())
        patterns = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y%m%d%H%M%S",
            "%Y%m%d%H%M",
            "%Y%m%d",
            "%Y-%m-%d",
        ]
        candidates = [value]
        if digits and digits != value:
            candidates.append(digits)
        for candidate in candidates:
            for pattern in patterns:
                try:
                    return datetime.strptime(candidate, pattern).strftime("%Y-%m-%d")
                except ValueError:
                    continue
        if len(digits) >= 8:
            try:
                return datetime.strptime(digits[:8], "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                pass
        return fallback_date
    
    def save_daily_pnl(self, snapshot_date: str, total_asset: float, cash: float,
                       market_value: float, position_count: int = 0,
                       remark: str = "") -> Optional[DailyPnlSnapshot]:
        """
        Save or update daily PnL snapshot.
        
        Automatically computes daily P&L and cumulative return based on
        the previous snapshot.
        
        Args:
            snapshot_date: Date string YYYY-MM-DD
            total_asset: Total account asset value
            cash: Available cash
            market_value: Market value of positions
            position_count: Number of positions held
            remark: Optional note
            
        Returns:
            DailyPnlSnapshot or None on failure
        """
        # Get previous snapshot to calculate daily PnL
        prev = self.get_previous_pnl_snapshot(snapshot_date)
        
        total_profit = 0.0
        total_profit_pct = 0.0
        cumulative_return = 0.0
        
        if prev and prev.total_asset > 0:
            total_profit = total_asset - prev.total_asset
            total_profit_pct = (total_profit / prev.total_asset) * 100
            # Cumulative return: chain from previous
            cumulative_return = (1 + prev.cumulative_return / 100) * (1 + total_profit_pct / 100) - 1
            cumulative_return *= 100
        elif prev is None:
            # First snapshot ever, no PnL to calculate
            total_profit = 0.0
            total_profit_pct = 0.0
            cumulative_return = 0.0
        
        snapshot = DailyPnlSnapshot(
            snapshot_date=snapshot_date,
            total_asset=round(total_asset, 2),
            cash=round(cash, 2),
            market_value=round(market_value, 2),
            total_profit=round(total_profit, 2),
            total_profit_pct=round(total_profit_pct, 4),
            cumulative_return=round(cumulative_return, 4),
            position_count=position_count,
            remark=remark,
        )
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # UPSERT: insert or replace if same date exists
            cursor.execute('''
                INSERT INTO daily_pnl (
                    snapshot_date, total_asset, cash, market_value,
                    total_profit, total_profit_pct, cumulative_return,
                    position_count, remark, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date) DO UPDATE SET
                    total_asset=excluded.total_asset,
                    cash=excluded.cash,
                    market_value=excluded.market_value,
                    total_profit=excluded.total_profit,
                    total_profit_pct=excluded.total_profit_pct,
                    cumulative_return=excluded.cumulative_return,
                    position_count=excluded.position_count,
                    remark=excluded.remark,
                    created_at=excluded.created_at
            ''', (
                snapshot.snapshot_date, snapshot.total_asset, snapshot.cash,
                snapshot.market_value, snapshot.total_profit, snapshot.total_profit_pct,
                snapshot.cumulative_return, snapshot.position_count,
                snapshot.remark, snapshot.created_at
            ))
            
            snapshot.id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            self._log(f"Daily PnL saved: {snapshot_date} total={total_asset:,.2f} "
                     f"pnl={total_profit:+,.2f} ({total_profit_pct:+.2f}%)")
            
            self.pnl_snapshot_saved.emit(snapshot)
            return snapshot
            
        except Exception as e:
            logger.error(f"Failed to save daily PnL: {e}")
            return None
    
    def get_previous_pnl_snapshot(self, before_date: str) -> Optional[DailyPnlSnapshot]:
        """Get the most recent snapshot before the given date"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT * FROM daily_pnl WHERE snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 1',
            (before_date,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return DailyPnlSnapshot.from_dict(dict(row))
        return None
    
    def get_pnl_snapshot(self, snapshot_date: str) -> Optional[DailyPnlSnapshot]:
        """Get snapshot for a specific date"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM daily_pnl WHERE snapshot_date = ?', (snapshot_date,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return DailyPnlSnapshot.from_dict(dict(row))
        return None
    
    def get_pnl_snapshots(self, start_date: str = None, end_date: str = None,
                          limit: int = 365) -> List[DailyPnlSnapshot]:
        """
        Get PnL snapshots within a date range.
        
        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            limit: Max number of records
            
        Returns:
            List of DailyPnlSnapshot, ordered by date ascending
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        conditions = []
        params = []
        
        if start_date:
            conditions.append("snapshot_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("snapshot_date <= ?")
            params.append(end_date)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        cursor.execute(
            f'SELECT * FROM daily_pnl WHERE {where_clause} ORDER BY snapshot_date ASC LIMIT ?',
            params + [limit]
        )
        rows = cursor.fetchall()
        conn.close()
        
        return [DailyPnlSnapshot.from_dict(dict(row)) for row in rows]
    
    def get_pnl_snapshots_count(self) -> int:
        """Get total count of PnL snapshots"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM daily_pnl')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def delete_pnl_snapshot(self, snapshot_date: str) -> bool:
        """Delete a PnL snapshot by date"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM daily_pnl WHERE snapshot_date = ?', (snapshot_date,))
            affected = cursor.rowcount
            conn.commit()
            conn.close()
            if affected > 0:
                self._log(f"Deleted PnL snapshot: {snapshot_date}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete PnL snapshot: {e}")
            return False
    
    def calculate_max_drawdown(self, snapshots: List[DailyPnlSnapshot]) -> Tuple[float, str, str]:
        """
        Calculate maximum drawdown from a series of snapshots.
        
        Returns:
            (max_drawdown_pct, peak_date, trough_date)
        """
        if not snapshots:
            return 0.0, "", ""
        
        peak_asset = snapshots[0].total_asset
        peak_date = snapshots[0].snapshot_date
        max_drawdown = 0.0
        max_dd_peak_date = ""
        max_dd_trough_date = ""
        
        for s in snapshots:
            if s.total_asset > peak_asset:
                peak_asset = s.total_asset
                peak_date = s.snapshot_date
            
            if peak_asset > 0:
                drawdown = (peak_asset - s.total_asset) / peak_asset * 100
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
                    max_dd_peak_date = peak_date
                    max_dd_trough_date = s.snapshot_date
        
        return round(max_drawdown, 2), max_dd_peak_date, max_dd_trough_date
    
    def calculate_sharpe_ratio(self, snapshots: List[DailyPnlSnapshot],
                               risk_free_rate: float = 0.015) -> float:
        """
        Calculate annualized Sharpe ratio.
        
        Args:
            snapshots: List of snapshots
            risk_free_rate: Annual risk-free rate (default 1.5%)
            
        Returns:
            Sharpe ratio
        """
        if len(snapshots) < 2:
            return 0.0
        
        daily_returns = []
        for i in range(1, len(snapshots)):
            prev_asset = snapshots[i - 1].total_asset
            if prev_asset > 0:
                ret = (snapshots[i].total_asset - prev_asset) / prev_asset
                daily_returns.append(ret)
        
        if not daily_returns:
            return 0.0
        
        import math
        avg_return = sum(daily_returns) / len(daily_returns)
        variance = sum((r - avg_return) ** 2 for r in daily_returns) / len(daily_returns)
        std_return = math.sqrt(variance) if variance > 0 else 0
        
        if std_return == 0:
            return 0.0
        
        # Annualize
        daily_rf = risk_free_rate / 252
        sharpe = (avg_return - daily_rf) / std_return * math.sqrt(252)
        return round(sharpe, 2)
    
    def export_pnl_to_csv(self, file_path: str, start_date: str = None,
                          end_date: str = None) -> bool:
        """Export PnL snapshots to CSV"""
        try:
            import csv
            
            snapshots = self.get_pnl_snapshots(start_date, end_date, limit=100000)
            if not snapshots:
                logger.warning("No PnL snapshots to export")
                return False
            
            headers = [
                'Date', 'Total Asset', 'Cash', 'Market Value',
                'Daily P&L', 'Daily Return %', 'Cumulative Return %',
                'Positions', 'Remark'
            ]
            
            with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for s in snapshots:
                    writer.writerow([
                        s.snapshot_date, f"{s.total_asset:.2f}", f"{s.cash:.2f}",
                        f"{s.market_value:.2f}", f"{s.total_profit:.2f}",
                        f"{s.total_profit_pct:.4f}", f"{s.cumulative_return:.4f}",
                        s.position_count, s.remark
                    ])
            
            self._log(f"Exported {len(snapshots)} PnL snapshots to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to export PnL snapshots: {e}")
            return False

    def get_all_stocks(self) -> List[Tuple[str, str]]:
        """获取所有交易过的股票列表"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT DISTINCT stock_code, stock_name 
            FROM trades 
            ORDER BY stock_code
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        return [(row['stock_code'], row['stock_name']) for row in rows]


# 全局单例
_trade_record_service: Optional[TradeRecordService] = None


def get_trade_record_service() -> TradeRecordService:
    """
    获取全局交易记录服务实例（单例模式）
    
    Returns:
        TradeRecordService 实例
    """
    global _trade_record_service
    if _trade_record_service is None:
        _trade_record_service = TradeRecordService()
    return _trade_record_service

