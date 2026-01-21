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
        
        logger.info(f"交易记录服务初始化完成，数据库路径: {self.db_path}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
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
                remark TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        ''')
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_date ON trades(trade_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_code ON trades(stock_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_direction ON trades(direction)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_source ON trades(source)')
        
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
                    trade_date, source, remark, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                record.trade_id, record.broker_order_id, record.stock_code,
                record.stock_name, record.direction, record.price, record.volume,
                record.amount, record.commission, record.stamp_tax, record.transfer_fee,
                record.trade_date, record.source, record.remark, record.created_at
            ))
            
            record.id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            self._log(f"新增交易记录: {stock_name}({code}) {record.direction_display} "
                     f"{volume}股 @ {price:.3f}")
            
            self.record_added.emit(record)
            self.records_changed.emit()
            
            return record
            
        except sqlite3.IntegrityError as e:
            logger.warning(f"交易记录已存在: {record.trade_id}")
            return None
        except Exception as e:
            logger.error(f"保存交易记录失败: {e}")
            return None
    
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

