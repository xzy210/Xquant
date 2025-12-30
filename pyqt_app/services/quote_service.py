# quote_service.py - 实时行情推送服务
"""
基于 xtquant 的实时行情服务

功能：
- 订阅/取消订阅股票实时行情
- 定时轮询获取最新行情快照
- 支持批量订阅
- PyQt 信号推送

采用轮询模式（推荐）：
- 使用 QTimer 定期调用 get_full_tick() 获取快照
- 更稳定，与 PyQt GUI 更好集成
- 适合 UI 显示场景（1-3秒刷新足够）

参考文档: https://dict.thinktrader.net/nativeApi/xtdata.html
"""
import logging
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime
import threading

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

# 设置日志
logger = logging.getLogger(__name__)

# 检查 xtquant 是否可用
try:
    from xtquant import xtdata
    HAS_XTQUANT = True
except ImportError:
    HAS_XTQUANT = False
    xtdata = None
    logger.warning("xtquant 未安装，实时行情功能不可用")


@dataclass
class QuoteData:
    """实时行情数据结构"""
    code: str                    # 股票代码 (如 "000001.SZ")
    name: str = ""               # 股票名称
    last_price: float = 0.0      # 最新价
    open_price: float = 0.0      # 开盘价
    high_price: float = 0.0      # 最高价
    low_price: float = 0.0       # 最低价
    prev_close: float = 0.0      # 昨收价
    volume: int = 0              # 成交量（股）
    amount: float = 0.0          # 成交额
    bid_prices: List[float] = field(default_factory=lambda: [0.0] * 5)   # 买1-买5价格
    bid_volumes: List[int] = field(default_factory=lambda: [0] * 5)     # 买1-买5量
    ask_prices: List[float] = field(default_factory=lambda: [0.0] * 5)   # 卖1-卖5价格
    ask_volumes: List[int] = field(default_factory=lambda: [0] * 5)     # 卖1-卖5量
    timestamp: datetime = field(default_factory=datetime.now)           # 时间戳
    
    @property
    def change(self) -> float:
        """涨跌额"""
        if self.prev_close > 0:
            return self.last_price - self.prev_close
        return 0.0
    
    @property
    def change_pct(self) -> float:
        """涨跌幅（百分比）"""
        if self.prev_close > 0:
            return (self.last_price - self.prev_close) / self.prev_close * 100
        return 0.0
    
    @property
    def is_up(self) -> bool:
        """是否上涨"""
        return self.change > 0
    
    @property
    def simple_code(self) -> str:
        """返回6位简单代码"""
        return self.code.split('.')[0] if '.' in self.code else self.code
    
    def to_dict(self) -> dict:
        """转换为字典格式（兼容旧接口）"""
        return {
            'lastPrice': self.last_price,
            'open': self.open_price,
            'high': self.high_price,
            'low': self.low_price,
            'lastClose': self.prev_close,
            'volume': self.volume,
            'amount': self.amount,
            'bidPrice': self.bid_prices,
            'bidVol': self.bid_volumes,
            'askPrice': self.ask_prices,
            'askVol': self.ask_volumes,
        }


def to_xt_code(code: str) -> str:
    """
    将6位股票代码转换为 xtquant 格式
    
    Args:
        code: 6位股票代码或已带后缀的代码
    
    Returns:
        xtquant 格式代码，如 "000001.SZ"
    """
    if '.' in code:
        return code
    
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


def from_xt_code(xt_code: str) -> str:
    """
    将 xtquant 格式代码转换为6位代码
    
    Args:
        xt_code: xtquant 格式代码，如 "000001.SZ"
    
    Returns:
        6位股票代码
    """
    return xt_code.split('.')[0] if '.' in xt_code else xt_code


class QuoteService(QObject):
    """
    实时行情服务（轮询模式）
    
    使用 QTimer 定时调用 xtdata.get_full_tick() 获取行情快照，
    通过 PyQt 信号发送给订阅者。
    
    信号：
        quote_updated: 单股行情更新信号，参数为 QuoteData
        quotes_batch_updated: 批量行情更新信号，参数为 Dict[str, QuoteData]
        connection_status_changed: 连接状态变化信号，参数为 (bool, str)
    
    使用示例：
        service = QuoteService()
        service.quote_updated.connect(on_quote_update)
        service.subscribe(['000001', '600000'])
    """
    
    # PyQt 信号
    quote_updated = pyqtSignal(object)  # QuoteData
    quotes_batch_updated = pyqtSignal(dict)  # Dict[str, QuoteData]
    connection_status_changed = pyqtSignal(bool, str)  # connected, message
    
    def __init__(self, parent=None, poll_interval: int = 1000):
        """
        初始化行情服务
        
        Args:
            parent: 父对象
            poll_interval: 轮询间隔（毫秒），默认1000ms
        """
        super().__init__(parent)
        
        self._subscribed_codes: Set[str] = set()  # 已订阅的代码（xtquant格式）
        self._quote_cache: Dict[str, QuoteData] = {}  # 行情缓存
        self._is_running = False
        self._poll_interval = max(1000, poll_interval)
        
        # 轮询定时器
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_quotes)
        
        # 数据更新锁
        self._lock = threading.Lock()
        
    @property
    def is_available(self) -> bool:
        """检查服务是否可用"""
        return HAS_XTQUANT
    
    @property
    def is_running(self) -> bool:
        """检查服务是否正在运行"""
        return self._is_running
    
    @property
    def subscribed_count(self) -> int:
        """返回已订阅的股票数量"""
        return len(self._subscribed_codes)
    
    @property
    def poll_interval(self) -> int:
        """获取轮询间隔"""
        return self._poll_interval
    
    def set_poll_interval(self, interval_ms: int):
        """
        设置轮询间隔
        
        Args:
            interval_ms: 轮询间隔（毫秒），最小1000ms
        """
        self._poll_interval = max(1000, interval_ms)
        if self._poll_timer.isActive():
            self._poll_timer.setInterval(self._poll_interval)
        logger.info(f"轮询间隔已设置为 {self._poll_interval}ms")
    
    def start(self) -> bool:
        """
        启动行情服务
        
        Returns:
            是否成功启动
        """
        if not HAS_XTQUANT:
            logger.error("xtquant 未安装，无法启动行情服务")
            self.connection_status_changed.emit(False, "xtquant 未安装")
            return False
        
        if self._is_running:
            logger.debug("行情服务已在运行中")
            return True
        
        try:
            # 测试连接
            test_result = xtdata.get_full_tick(["000001.SZ"])
            if test_result is None:
                logger.warning("xtquant 返回空数据，请确认 miniQMT 已启动并连接")
            
            self._is_running = True
            self._poll_timer.start(self._poll_interval)
            
            logger.info(f"实时行情服务已启动 (轮询间隔: {self._poll_interval}ms)")
            self.connection_status_changed.emit(True, "行情服务已启动")
            return True
            
        except Exception as e:
            logger.error(f"启动行情服务失败: {e}")
            self.connection_status_changed.emit(False, f"启动失败: {e}")
            return False
    
    def stop(self):
        """停止行情服务"""
        if not self._is_running:
            return
        
        try:
            self._poll_timer.stop()
            
            with self._lock:
                self._subscribed_codes.clear()
                self._quote_cache.clear()
            
            self._is_running = False
            logger.info("实时行情服务已停止")
            self.connection_status_changed.emit(False, "行情服务已停止")
            
        except Exception as e:
            logger.error(f"停止行情服务失败: {e}")
    
    def subscribe(self, codes: List[str], start_service: bool = True) -> bool:
        """
        订阅股票行情
        
        Args:
            codes: 股票代码列表（支持6位代码或xtquant格式）
            start_service: 是否自动启动服务
        
        Returns:
            是否成功
        """
        if not HAS_XTQUANT:
            logger.error("xtquant 未安装，无法订阅")
            return False
        
        if start_service and not self._is_running:
            if not self.start():
                return False
        
        if not codes:
            return True
        
        # 转换为 xtquant 格式并添加到订阅集合
        xt_codes = [to_xt_code(c) for c in codes]
        
        with self._lock:
            new_codes = [c for c in xt_codes if c not in self._subscribed_codes]
            self._subscribed_codes.update(xt_codes)
        
        if new_codes:
            logger.info(f"订阅 {len(new_codes)} 只股票: {new_codes[:3]}{'...' if len(new_codes) > 3 else ''}")
            # 立即刷新一次
            self.refresh_quotes(new_codes)
        
        return True
    
    def unsubscribe(self, codes: List[str]) -> bool:
        """
        取消订阅
        
        Args:
            codes: 股票代码列表
        """
        if not codes:
            return True
        
        xt_codes = [to_xt_code(c) for c in codes]
        
        with self._lock:
            for c in xt_codes:
                self._subscribed_codes.discard(c)
                self._quote_cache.pop(c, None)
        
        logger.info(f"取消订阅 {len(xt_codes)} 只股票")
        return True
    
    def unsubscribe_all(self):
        """取消所有订阅"""
        with self._lock:
            count = len(self._subscribed_codes)
            self._subscribed_codes.clear()
            self._quote_cache.clear()
        
        if count > 0:
            logger.info(f"已取消所有订阅 ({count} 只)")
    
    def get_quote(self, code: str) -> Optional[QuoteData]:
        """
        获取指定股票的最新行情
        
        Args:
            code: 股票代码
        
        Returns:
            QuoteData 或 None
        """
        xt_code = to_xt_code(code)
        return self._quote_cache.get(xt_code)
    
    def get_all_quotes(self) -> Dict[str, QuoteData]:
        """获取所有已缓存的行情数据"""
        with self._lock:
            return dict(self._quote_cache)
    
    def refresh_quotes(self, codes: List[str] = None):
        """
        主动刷新行情数据
        
        Args:
            codes: 要刷新的代码列表，None 表示刷新所有已订阅的
        """
        if not HAS_XTQUANT:
            return
        
        try:
            if codes is None:
                with self._lock:
                    xt_codes = list(self._subscribed_codes)
            else:
                xt_codes = [to_xt_code(c) if '.' not in c else c for c in codes]
            
            if not xt_codes:
                return
            
            # 获取快照数据
            tick_data = xtdata.get_full_tick(xt_codes)
            
            if tick_data:
                for xt_code, tick in tick_data.items():
                    self._process_tick(xt_code, tick)
                
        except Exception as e:
            logger.error(f"刷新行情失败: {e}")
    
    def _poll_quotes(self):
        """定时轮询获取行情"""
        if not self._subscribed_codes:
            return
        
        try:
            with self._lock:
                xt_codes = list(self._subscribed_codes)
            
            if not xt_codes:
                return
            
            # 分批获取（避免一次请求过多）
            batch_size = 100
            for i in range(0, len(xt_codes), batch_size):
                batch = xt_codes[i:i + batch_size]
                tick_data = xtdata.get_full_tick(batch)
                
                if tick_data:
                    for xt_code, tick in tick_data.items():
                        self._process_tick(xt_code, tick)
            
            # 发送批量更新信号
            self._emit_batch_update()
                
        except Exception as e:
            logger.error(f"轮询行情失败: {e}")
    
    def _process_tick(self, xt_code: str, tick: dict):
        """处理单条 tick 数据"""
        if not tick:
            return
        
        try:
            # 安全获取列表数据
            def safe_list(data, default_len=5):
                if isinstance(data, (list, tuple)):
                    return list(data)[:default_len] + [0] * (default_len - len(data))
                return [0] * default_len
            
            quote = QuoteData(
                code=xt_code,
                last_price=float(tick.get('lastPrice') or 0),
                open_price=float(tick.get('open') or 0),
                high_price=float(tick.get('high') or 0),
                low_price=float(tick.get('low') or 0),
                prev_close=float(tick.get('lastClose') or 0),
                volume=int(tick.get('volume') or 0),
                amount=float(tick.get('amount') or 0),
                bid_prices=safe_list(tick.get('bidPrice')),
                bid_volumes=[int(v or 0) for v in safe_list(tick.get('bidVol'))],
                ask_prices=safe_list(tick.get('askPrice')),
                ask_volumes=[int(v or 0) for v in safe_list(tick.get('askVol'))],
                timestamp=datetime.now()
            )
            
            with self._lock:
                self._quote_cache[xt_code] = quote
            
            # 发送单条更新信号
            self.quote_updated.emit(quote)
            
        except Exception as e:
            logger.error(f"处理 tick 数据失败 {xt_code}: {e}")
    
    def _emit_batch_update(self):
        """发送批量行情更新信号"""
        with self._lock:
            if self._quote_cache:
                self.quotes_batch_updated.emit(dict(self._quote_cache))


# 全局单例
_quote_service_instance: Optional[QuoteService] = None


def get_quote_service() -> QuoteService:
    """
    获取全局行情服务实例（单例模式）
    
    Returns:
        QuoteService 实例
    """
    global _quote_service_instance
    if _quote_service_instance is None:
        _quote_service_instance = QuoteService()
    return _quote_service_instance
