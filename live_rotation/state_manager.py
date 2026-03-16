"""
ETF轮动实盘 - 状态持久化

记录当前持仓状态、历史交易记录，程序重启后可恢复。
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict

from common.io_utils import atomic_write_json

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """交易记录"""
    date: str
    time: str
    action: str           # BUY / SELL / SELL_ALL
    code: str
    name: str
    price: float
    quantity: int
    amount: float
    reason: str = ""
    broker_order_id: int = -1
    success: bool = True
    error_msg: str = ""
    pnl: float = 0.0      # 本笔盈亏（仅 SELL 有效）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'TradeRecord':
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class CapitalLedgerEntry:
    """专用资金账本流水条目"""
    date: str
    time: str
    action: str            # 初始化 / 买入划出 / 卖出回收 / 手动重置
    code: str = ""
    name: str = ""
    amount: float = 0.0    # 变动金额（正=增加，负=减少）
    commission: float = 0.0
    balance: float = 0.0   # 操作后余额
    fee_source: str = ""   # [实际] / [估算]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'CapitalLedgerEntry':
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class OrderRecord:
    """委托 / 成交明细"""
    order_id: int
    date: str
    time: str
    action: str            # 买入 / 卖出
    code: str
    name: str
    ordered_qty: int
    ordered_price: float
    filled_qty: int = 0
    filled_price: float = 0.0
    commission: float = -1.0   # -1 = 未知/估算
    status: str = "pending_submit"
    reason: str = ""
    pnl: float = 0.0           # 本笔盈亏（仅卖出有效）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'OrderRecord':
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class RotationState:
    """轮动策略实盘状态"""
    current_holding: Optional[str] = None       # 当前持仓ETF代码
    current_holding_name: str = ""              # 持仓ETF名称
    current_score: float = 0.0                  # 当前持仓得分
    buy_price: float = 0.0                      # 买入价格
    buy_date: str = ""                          # 买入日期
    buy_quantity: int = 0                       # 买入数量
    last_check_date: str = ""                   # 最近一次信号检查日期
    last_check_time: str = ""                   # 最近一次信号检查时间
    last_signal: str = ""                       # 最近信号: HOLD / SWITCH / SELL_ALL / BUY
    trades_today: int = 0                       # 今日已交易次数
    trades_today_date: str = ""                 # trades_today 对应的日期
    total_invested: float = 0.0                 # 累计投入资金
    total_pnl: float = 0.0                      # 累计盈亏

    holding_high_price: float = 0.0             # 持仓期间最高价（移动止盈用）
    account_peak: float = 0.0                   # 账户资产峰值（回撤保护用）
    cooldown_remaining: int = 0                 # 回撤保护冷却剩余天数
    cooldown_last_decrement_date: str = ""      # 冷却天数最近一次递减日期
    check_count: int = 0                        # 信号检查计数（调仓周期用）

    # --- 专用资金账本（真实账户模式下的资金隔离）---
    dedicated_cash: float = 0.0                 # 策略可用现金（0=尚未初始化）

    last_scores: Dict[str, float] = field(default_factory=dict)
    trade_history: List[dict] = field(default_factory=list)

    # --- 扩展分析数据 ---
    capital_ledger: List[dict] = field(default_factory=list)   # 资金流水（最近500条）
    daily_equity: Dict[str, float] = field(default_factory=dict)  # 净值曲线 {日期: 净值}
    order_records: List[dict] = field(default_factory=list)    # 委托明细（最近200条）

    def get_trades_today(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.trades_today_date != today:
            return 0
        return self.trades_today

    def increment_trades_today(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self.trades_today_date != today:
            self.trades_today = 0
            self.trades_today_date = today
        self.trades_today += 1

    def add_trade(self, record: TradeRecord):
        self.trade_history.append(record.to_dict())
        self.increment_trades_today()
        # 保留最近200条
        if len(self.trade_history) > 200:
            self.trade_history = self.trade_history[-200:]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'RotationState':
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


class StateManager:
    """状态持久化管理器"""

    STATE_FILE = "rotation_state.json"

    def __init__(self, config_dir: Optional[str] = None):
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path(__file__).parent / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.config_dir / self.STATE_FILE
        self._state: Optional[RotationState] = None

    @property
    def state(self) -> RotationState:
        if self._state is None:
            self._state = self.load()
        return self._state

    def load(self) -> RotationState:
        if not self.state_path.exists():
            logger.info("状态文件不存在，初始化空状态")
            return RotationState()
        try:
            with open(self.state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f"已加载状态: 持仓={data.get('current_holding', '无')}")
            return RotationState.from_dict(data)
        except Exception as e:
            logger.error(f"加载状态失败: {e}")
            return RotationState()

    def save(self, state: Optional[RotationState] = None):
        if state is not None:
            self._state = state
        if self._state is None:
            return
        try:
            atomic_write_json(self.state_path, self._state.to_dict())
            logger.debug("状态已保存")
        except Exception as e:
            logger.error(f"保存状态失败: {e}")

    def update_holding(self, code: Optional[str], name: str, score: float,
                       price: float, quantity: int):
        """更新持仓信息"""
        s = self.state
        s.current_holding = code
        s.current_holding_name = name
        s.current_score = score
        s.buy_price = price
        s.buy_quantity = quantity
        s.buy_date = datetime.now().strftime("%Y-%m-%d")
        s.holding_high_price = price
        self.save()

    def clear_holding(self):
        """清空持仓"""
        s = self.state
        s.current_holding = None
        s.current_holding_name = ""
        s.current_score = 0.0
        s.buy_price = 0.0
        s.buy_quantity = 0
        s.buy_date = ""
        s.holding_high_price = 0.0
        self.save()

    def update_check_result(self, signal: str, scores: Dict[str, float]):
        """更新检查结果"""
        s = self.state
        now = datetime.now()
        s.last_check_date = now.strftime("%Y-%m-%d")
        s.last_check_time = now.strftime("%H:%M:%S")
        s.last_signal = signal
        s.last_scores = scores
        self.save()

    # ------------------------------------------------------------------
    #  扩展分析数据辅助方法
    # ------------------------------------------------------------------

    def add_capital_entry(self, entry: 'CapitalLedgerEntry'):
        """追加资金流水条目，最多保留 500 条"""
        self.state.capital_ledger.append(entry.to_dict())
        if len(self.state.capital_ledger) > 500:
            self.state.capital_ledger = self.state.capital_ledger[-500:]
        self.save()

    def add_order_record(self, record: 'OrderRecord'):
        """新增或更新委托记录（同一 order_id 会覆盖旧记录）"""
        self.state.order_records = [
            r for r in self.state.order_records
            if r.get('order_id') != record.order_id
        ]
        self.state.order_records.append(record.to_dict())
        if len(self.state.order_records) > 200:
            self.state.order_records = self.state.order_records[-200:]
        self.save()

    def update_order_record(self, order_id: int, **kwargs):
        """按字段更新指定 order_id 的委托记录"""
        for r in self.state.order_records:
            if r.get('order_id') == order_id:
                r.update(kwargs)
                break
        self.save()

    def record_daily_equity(self, equity: float):
        """记录当日净值快照，最多保留 730 天"""
        today = datetime.now().strftime("%Y-%m-%d")
        self.state.daily_equity[today] = round(equity, 2)
        if len(self.state.daily_equity) > 730:
            keys = sorted(self.state.daily_equity.keys())
            for k in keys[:-730]:
                del self.state.daily_equity[k]
        self.save()
