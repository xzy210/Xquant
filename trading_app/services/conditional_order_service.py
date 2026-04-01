# conditional_order_service.py - 条件单服务
"""
条件单（止盈止损）自动执行服务

功能：
- 管理条件单列表（止盈/止损）
- 实时监控行情
- 自动触发下单执行
- 条件单持久化存储

条件类型：
- 止盈：当最新价 >= 目标价时触发卖出
- 止损：当最新价 <= 目标价时触发卖出
- 突破买入：当最新价 >= 目标价时触发买入
- 跌破买入：当最新价 <= 目标价时触发买入
"""
import json
import logging
import uuid
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
import math

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

# 设置日志
logger = logging.getLogger(__name__)


class OrderConditionType(Enum):
    """条件类型枚举"""
    TAKE_PROFIT = "take_profit"      # 止盈：价格 >= 目标价触发卖出
    STOP_LOSS = "stop_loss"          # 止损：价格 <= 目标价触发卖出
    TRAILING_STOP = "trailing_stop"  # 移动止损：价格从最高点回撤超过一定比例触发卖出
    BREAKOUT_BUY = "breakout_buy"    # 突破买入：价格 >= 目标价触发买入
    PULLBACK_BUY = "pullback_buy"    # 回调买入：价格 <= 目标价触发买入


class OrderStatus(Enum):
    """条件单状态枚举"""
    PENDING = "pending"              # 待触发
    TRIGGERED = "triggered"          # 已触发（等待执行）
    EXECUTED = "executed"            # 已执行
    SIMULATED = "simulated"          # 影子执行
    CANCELLED = "cancelled"          # 已撤销
    FAILED = "failed"                # 执行失败
    EXPIRED = "expired"              # 已过期


@dataclass
class ConditionalOrder:
    """条件单数据结构"""
    id: str                                    # 唯一ID
    stock_code: str                            # 股票代码（6位）
    stock_name: str                            # 股票名称
    condition_type: str                        # 条件类型
    trigger_price: float                       # 触发价格
    order_volume: int                          # 委托数量
    order_price_type: str = "market"           # 委托价格类型：market(市价)/limit(限价)
    order_price: float = 0.0                   # 限价委托价格（市价时为0）
    status: str = "pending"                    # 状态
    created_at: str = ""                       # 创建时间
    triggered_at: str = ""                     # 触发时间
    executed_at: str = ""                      # 执行时间
    expire_date: str = ""                      # 过期日期（格式：YYYY-MM-DD，空表示永不过期）
    remark: str = ""                           # 备注
    trigger_count: int = 0                     # 触发次数（用于防止重复触发）
    last_price: float = 0.0                    # 最后一次检查的价格
    broker_order_id: int = -1                  # 券商委托单号
    error_message: str = ""                    # 错误信息
    drawdown_pct: float = 0.0                  # 回撤比例（仅用于移动止损）
    highest_price: float = 0.0                 # 监控期间最高价（仅用于移动止损）
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 如果是移动止损且未设置最高价，初始化为触发价反推或当前价
        # 这里暂时无法获取当前价，逻辑在check_trigger中处理
    
    @property
    def is_sell_order(self) -> bool:
        """是否为卖出条件单"""
        return self.condition_type in [
            OrderConditionType.TAKE_PROFIT.value,
            OrderConditionType.STOP_LOSS.value,
            OrderConditionType.TRAILING_STOP.value
        ]
    
    @property
    def is_buy_order(self) -> bool:
        """是否为买入条件单"""
        return self.condition_type in [
            OrderConditionType.BREAKOUT_BUY.value,
            OrderConditionType.PULLBACK_BUY.value
        ]
    
    @property
    def condition_display(self) -> str:
        """条件显示文本"""
        type_map = {
            OrderConditionType.TAKE_PROFIT.value: "止盈",
            OrderConditionType.STOP_LOSS.value: "止损",
            OrderConditionType.TRAILING_STOP.value: f"移动止损(回撤{self.drawdown_pct}%)",
            OrderConditionType.BREAKOUT_BUY.value: "突破买入",
            OrderConditionType.PULLBACK_BUY.value: "回调买入",
        }
        return type_map.get(self.condition_type, self.condition_type)
    
    @property
    def status_display(self) -> str:
        """状态显示文本"""
        status_map = {
            OrderStatus.PENDING.value: "待触发",
            OrderStatus.TRIGGERED.value: "已触发",
            OrderStatus.EXECUTED.value: "已执行",
            OrderStatus.SIMULATED.value: "影子执行",
            OrderStatus.CANCELLED.value: "已撤销",
            OrderStatus.FAILED.value: "执行失败",
            OrderStatus.EXPIRED.value: "已过期",
        }
        return status_map.get(self.status, self.status)
    
    @property 
    def direction_display(self) -> str:
        """交易方向显示"""
        return "卖出" if self.is_sell_order else "买入"
    
    def check_trigger(self, current_price: float) -> bool:
        """
        检查是否满足触发条件
        
        Args:
            current_price: 当前价格
            
        Returns:
            是否触发
        """
        if self.status != OrderStatus.PENDING.value:
            return False
        
        if current_price <= 0:
            return False
        
        # 检查过期
        if self.expire_date:
            today = datetime.now().strftime("%Y-%m-%d")
            if today > self.expire_date:
                self.status = OrderStatus.EXPIRED.value
                return False
        
        triggered = False
        
        # 更新最后价格
        self.last_price = current_price
        
        if self.condition_type == OrderConditionType.TAKE_PROFIT.value:
            # 止盈：当前价 >= 触发价
            triggered = current_price >= self.trigger_price
            
        elif self.condition_type == OrderConditionType.STOP_LOSS.value:
            # 止损：当前价 <= 触发价
            triggered = current_price <= self.trigger_price
            
        elif self.condition_type == OrderConditionType.TRAILING_STOP.value:
            # 移动止损逻辑
            
            # 1. 初始化最高价（如果是第一次检查）
            if self.highest_price <= 0:
                # 假设初始最高价为当前价，或者根据触发价反推
                # 如果 trigger_price = highest * (1 - pct/100)
                # 则 highest = trigger_price / (1 - pct/100)
                if self.drawdown_pct > 0 and self.drawdown_pct < 100:
                     implied_highest = self.trigger_price / (1 - self.drawdown_pct / 100)
                     self.highest_price = max(current_price, implied_highest)
                else:
                    self.highest_price = current_price
            
            # 2. 检查是否创新高
            if current_price > self.highest_price:
                self.highest_price = current_price
                # 提升触发价（止损线）
                if self.drawdown_pct > 0:
                    new_trigger = round(self.highest_price * (1 - self.drawdown_pct / 100), 3)
                    if new_trigger > self.trigger_price:
                        self.trigger_price = new_trigger
                        # 注意：这里修改了 trigger_price，调用者可能需要保存状态
            
            # 3. 检查是否跌破触发价
            triggered = current_price <= self.trigger_price
            
        elif self.condition_type == OrderConditionType.BREAKOUT_BUY.value:
            # 突破买入：当前价 >= 触发价
            triggered = current_price >= self.trigger_price
            
        elif self.condition_type == OrderConditionType.PULLBACK_BUY.value:
            # 回调买入：当前价 <= 触发价
            triggered = current_price <= self.trigger_price
        
        return triggered
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ConditionalOrder':
        """从字典创建"""
        return cls(**data)


class ConditionalOrderService(QObject):
    """
    条件单服务
    
    负责管理条件单的创建、监控、触发和执行
    
    信号：
        order_triggered: 条件单触发信号，参数为 ConditionalOrder
        order_executed: 条件单执行完成信号，参数为 (ConditionalOrder, success, message)
        order_updated: 条件单更新信号，参数为 ConditionalOrder
        orders_changed: 条件单列表变化信号
        log_message: 日志消息信号
    """
    
    order_triggered = pyqtSignal(object)  # ConditionalOrder
    order_executed = pyqtSignal(object, bool, str)  # ConditionalOrder, success, message
    order_updated = pyqtSignal(object)  # ConditionalOrder
    orders_changed = pyqtSignal()
    log_message = pyqtSignal(str)
    
    CONFIG_FILE = "conditional_orders.json"
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 配置目录
        self.config_dir = Path(__file__).parent.parent / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / self.CONFIG_FILE
        
        # 条件单列表
        self._orders: Dict[str, ConditionalOrder] = {}
        
        # 交易执行器（由外部注入）
        self._trade_executor: Optional[Callable] = None
        
        # 监控状态
        self._is_monitoring = False
        
        # 行情缓存（由外部行情服务更新）
        self._quote_cache: Dict[str, float] = {}  # code -> last_price
        
        # 加载保存的条件单
        self._load_orders()
        
        logger.info(f"条件单服务初始化完成，已加载 {len(self._orders)} 个条件单")
    
    @property
    def is_monitoring(self) -> bool:
        """是否正在监控"""
        return self._is_monitoring
    
    @property
    def order_count(self) -> int:
        """条件单总数"""
        return len(self._orders)
    
    @property
    def pending_count(self) -> int:
        """待触发条件单数量"""
        return sum(1 for o in self._orders.values() if o.status == OrderStatus.PENDING.value)
    
    def set_trade_executor(self, executor: Callable):
        """
        设置交易执行器
        
        Args:
            executor: 交易执行函数，签名为 (stock_code, order_type, volume, price_type, price) -> (success, message, order_id)
        """
        self._trade_executor = executor
        logger.info("交易执行器已设置")
    
    def start_monitoring(self):
        """启动监控"""
        self._is_monitoring = True
        self._log("条件单监控已启动")
    
    def stop_monitoring(self):
        """停止监控"""
        self._is_monitoring = False
        self._log("条件单监控已停止")
    
    def _log(self, message: str):
        """发送日志"""
        logger.info(message)
        self.log_message.emit(f"[条件单] {message}")

    @staticmethod
    def _normalize_price(price: float, tick_size: float = 0.01) -> float:
        """将价格规整到合法价位（向下取整到最小价位）"""
        if price <= 0 or tick_size <= 0:
            return price
        normalized = math.floor(price / tick_size) * tick_size
        return round(normalized + 1e-8, 2)
    
    def _load_orders(self):
        """从文件加载条件单"""
        if not self.config_path.exists():
            return
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for order_data in data.get("orders", []):
                try:
                    order = ConditionalOrder.from_dict(order_data)
                    self._orders[order.id] = order
                except Exception as e:
                    logger.error(f"加载条件单失败: {e}")
            
            logger.info(f"成功加载 {len(self._orders)} 个条件单")
        except Exception as e:
            logger.error(f"读取条件单配置文件失败: {e}")
    
    def _save_orders(self):
        """保存条件单到文件"""
        try:
            data = {
                "orders": [o.to_dict() for o in self._orders.values()],
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存条件单配置文件失败: {e}")
    
    def add_order(self, 
                  stock_code: str,
                  stock_name: str,
                  condition_type: str,
                  trigger_price: float,
                  order_volume: int,
                  order_price_type: str = "market",
                  order_price: float = 0.0,
                  expire_date: str = "",
                  remark: str = "",
                  drawdown_pct: float = 0.0) -> ConditionalOrder:
        """
        添加条件单
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            condition_type: 条件类型
            trigger_price: 触发价格
            order_volume: 委托数量
            order_price_type: 价格类型（market/limit）
            order_price: 限价价格
            expire_date: 过期日期
            remark: 备注
            drawdown_pct: 回撤比例（仅用于移动止损）
            
        Returns:
            创建的条件单对象
        """
        order_id = str(uuid.uuid4())[:8]

        if order_price_type == "limit" and order_price > 0:
            order_price = self._normalize_price(order_price, 0.01)
        elif order_price_type != "limit":
            order_price = 0.0
        
        order = ConditionalOrder(
            id=order_id,
            stock_code=stock_code,
            stock_name=stock_name,
            condition_type=condition_type,
            trigger_price=trigger_price,
            order_volume=order_volume,
            order_price_type=order_price_type,
            order_price=order_price,
            expire_date=expire_date,
            remark=remark,
            drawdown_pct=drawdown_pct
        )
        
        self._orders[order_id] = order
        self._save_orders()
        self.orders_changed.emit()
        
        direction = "卖出" if order.is_sell_order else "买入"
        self._log(f"新增条件单: {stock_name}({stock_code}) {order.condition_display} "
                 f"触发价{trigger_price:.3f} {direction}{order_volume}股")
        
        return order
    
    def remove_order(self, order_id: str) -> bool:
        """
        删除条件单
        
        Args:
            order_id: 条件单ID
            
        Returns:
            是否成功删除
        """
        if order_id not in self._orders:
            return False
        
        order = self._orders.pop(order_id)
        self._save_orders()
        self.orders_changed.emit()
        
        self._log(f"删除条件单: {order.stock_name}({order.stock_code}) {order.condition_display}")
        return True
    
    def cancel_order(self, order_id: str) -> bool:
        """
        撤销条件单
        
        Args:
            order_id: 条件单ID
            
        Returns:
            是否成功撤销
        """
        if order_id not in self._orders:
            return False
        
        order = self._orders[order_id]
        if order.status != OrderStatus.PENDING.value:
            return False
        
        order.status = OrderStatus.CANCELLED.value
        self._save_orders()
        self.order_updated.emit(order)
        self.orders_changed.emit()
        
        self._log(f"撤销条件单: {order.stock_name}({order.stock_code}) {order.condition_display}")
        return True
    
    def get_order(self, order_id: str) -> Optional[ConditionalOrder]:
        """获取条件单"""
        return self._orders.get(order_id)
    
    def get_all_orders(self) -> List[ConditionalOrder]:
        """获取所有条件单"""
        return list(self._orders.values())
    
    def get_pending_orders(self) -> List[ConditionalOrder]:
        """获取待触发的条件单"""
        return [o for o in self._orders.values() if o.status == OrderStatus.PENDING.value]
    
    def get_orders_by_stock(self, stock_code: str) -> List[ConditionalOrder]:
        """获取指定股票的条件单"""
        code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        return [o for o in self._orders.values() if o.stock_code == code]
    
    def update_quotes(self, quotes: Dict[str, float]):
        """
        更新行情并检查触发条件
        
        Args:
            quotes: 行情数据 {stock_code: last_price}
        """
        if not self._is_monitoring:
            return
        
        self._quote_cache.update(quotes)
        
        # 检查每个待触发的条件单
        for order in self.get_pending_orders():
            code = order.stock_code
            # 尝试不同格式的代码
            price = quotes.get(code) or quotes.get(f"{code}.SH") or quotes.get(f"{code}.SZ")
            
            if price and order.check_trigger(price):
                self._trigger_order(order, price)
    
    def check_single_quote(self, stock_code: str, last_price: float):
        """
        检查单个股票的行情
        
        Args:
            stock_code: 股票代码
            last_price: 最新价
        """
        if not self._is_monitoring or last_price <= 0:
            return
        
        code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        self._quote_cache[code] = last_price
        
        # 检查该股票相关的条件单
        for order in self.get_orders_by_stock(code):
            if order.status == OrderStatus.PENDING.value and order.check_trigger(last_price):
                self._trigger_order(order, last_price)
    
    def _trigger_order(self, order: ConditionalOrder, trigger_price: float):
        """
        触发条件单
        
        Args:
            order: 条件单
            trigger_price: 触发时的价格
        """
        order.status = OrderStatus.TRIGGERED.value
        order.triggered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        order.trigger_count += 1
        order.last_price = trigger_price
        
        self._save_orders()
        self.order_triggered.emit(order)
        
        self._log(f"⚡ 条件单触发: {order.stock_name}({order.stock_code}) "
                 f"{order.condition_display} 触发价{order.trigger_price:.3f} "
                 f"当前价{trigger_price:.3f}")
        
        # 自动执行交易
        self._execute_order(order)
    
    def _execute_order(self, order: ConditionalOrder):
        """
        执行条件单交易
        
        Args:
            order: 条件单
        """
        if not self._trade_executor:
            order.status = OrderStatus.FAILED.value
            order.error_message = "交易执行器未设置"
            self._save_orders()
            self.order_executed.emit(order, False, "交易执行器未设置，请确保已连接券商")
            self._log(f"❌ 条件单执行失败: 交易执行器未设置")
            return
        
        try:
            # 确定交易方向
            order_type = 24 if order.is_sell_order else 23  # 24=卖出, 23=买入
            
            # 确定价格类型
            price_type = 0 if order.order_price_type == "limit" else 1  # 0=限价, 1=市价
            price = order.order_price if price_type == 0 else 0
            
            # 格式化股票代码
            stock_code = order.stock_code
            if '.' not in stock_code:
                if stock_code.startswith(('5', '6', '9')):
                    stock_code = f"{stock_code}.SH"
                else:
                    stock_code = f"{stock_code}.SZ"
            
            # 执行交易
            result = self._trade_executor(
                stock_code, order_type, order.order_volume, price_type, price
            )
            if isinstance(result, tuple):
                success, message, broker_order_id = result
                shadow = False
            else:
                success = bool(getattr(result, "success", False))
                message = str(getattr(result, "message", "") or "")
                broker_order_id = int(getattr(result, "broker_order_id", -1) or -1)
                shadow = bool(getattr(result, "shadow", False))
            
            if success and shadow:
                order.status = OrderStatus.SIMULATED.value
                order.executed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                order.broker_order_id = broker_order_id
                self._log(f"◎ 条件单影子执行: {order.stock_name} {order.direction_display} "
                         f"{order.order_volume}股")
            elif success:
                order.status = OrderStatus.EXECUTED.value
                order.executed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                order.broker_order_id = broker_order_id
                self._log(f"✅ 条件单执行成功: {order.stock_name} {order.direction_display} "
                         f"{order.order_volume}股 委托号:{broker_order_id}")
            else:
                order.status = OrderStatus.FAILED.value
                order.error_message = message
                self._log(f"❌ 条件单执行失败: {order.stock_name} - {message}")
            
            self._save_orders()
            self.order_executed.emit(order, success, message)
            self.order_updated.emit(order)
            self.orders_changed.emit()
            
        except Exception as e:
            order.status = OrderStatus.FAILED.value
            order.error_message = str(e)
            self._save_orders()
            self.order_executed.emit(order, False, str(e))
            self._log(f"❌ 条件单执行异常: {e}")
    
    def clear_history(self, keep_pending: bool = True):
        """
        清理历史条件单
        
        Args:
            keep_pending: 是否保留待触发的条件单
        """
        if keep_pending:
            self._orders = {
                oid: order for oid, order in self._orders.items()
                if order.status == OrderStatus.PENDING.value
            }
        else:
            self._orders.clear()
        
        self._save_orders()
        self.orders_changed.emit()
        self._log("已清理历史条件单")


# 全局单例
_conditional_order_service: Optional[ConditionalOrderService] = None


def get_conditional_order_service() -> ConditionalOrderService:
    """
    获取全局条件单服务实例（单例模式）
    
    Returns:
        ConditionalOrderService 实例
    """
    global _conditional_order_service
    if _conditional_order_service is None:
        _conditional_order_service = ConditionalOrderService()
    return _conditional_order_service
