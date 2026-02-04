# auto_stop_loss_service.py - 自动止损服务
"""
自动止损服务

功能：
- 管理自动止损配置
- 在买入成交后自动创建止损条件单
- 支持配置默认止损比例、价格类型等
- 支持智能合并同一股票的止损单

工作流程：
1. 监听交易记录服务的 record_added 信号
2. 检查是否为买入成交
3. 如果开启自动止损，自动创建止损条件单
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass, asdict

from PyQt6.QtCore import QObject, pyqtSignal

# 设置日志
logger = logging.getLogger(__name__)


@dataclass
class AutoStopLossConfig:
    """自动止损配置"""
    enabled: bool = False              # 是否启用自动止损
    stop_loss_pct: float = 5.0         # 默认止损百分比（%）
    price_type: str = "market"         # 委托类型：market(市价)/limit(限价)
    limit_offset_pct: float = 0.5      # 限价偏移百分比（相对止损价再低N%）
    expire_days: int = 30              # 有效期（天），0表示永不过期
    merge_same_stock: bool = True      # 是否合并同一股票的止损单
    notify_on_create: bool = True      # 创建止损单时是否通知
    exempt_etf: bool = False           # 是否豁免ETF（不自动创建止损单）
    exempt_codes: List[str] = None     # 豁免股票代码列表
    
    def __post_init__(self):
        if self.exempt_codes is None:
            self.exempt_codes = []
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AutoStopLossConfig':
        """从字典创建"""
        # Handle exempt_codes which may be None in saved config
        if 'exempt_codes' in data and data['exempt_codes'] is None:
            data['exempt_codes'] = []
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class AutoStopLossService(QObject):
    """
    自动止损服务
    
    负责在买入成交后自动创建止损条件单
    
    信号：
        config_changed: 配置变更信号
        stop_loss_created: 止损单创建信号 (stock_code, stock_name, stop_price, volume)
        log_message: 日志消息信号
    """
    
    config_changed = pyqtSignal()
    stop_loss_created = pyqtSignal(str, str, float, int)  # stock_code, stock_name, stop_price, volume
    log_message = pyqtSignal(str)
    
    CONFIG_FILE = "auto_stop_loss_config.json"
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 配置目录
        self.config_dir = Path(__file__).parent.parent / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / self.CONFIG_FILE
        
        # 配置
        self._config = AutoStopLossConfig()
        
        # 条件单服务（由外部注入）
        self._conditional_order_service = None
        
        # 加载配置
        self._load_config()
        
        logger.info(f"自动止损服务初始化完成，启用状态: {self._config.enabled}")
    
    @property
    def config(self) -> AutoStopLossConfig:
        """获取配置"""
        return self._config
    
    @property
    def is_enabled(self) -> bool:
        """是否启用自动止损"""
        return self._config.enabled
    
    def _load_config(self):
        """加载配置"""
        if not self.config_path.exists():
            self._save_config()
            return
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._config = AutoStopLossConfig.from_dict(data)
            logger.info(f"自动止损配置已加载: 启用={self._config.enabled}, 止损比例={self._config.stop_loss_pct}%")
        except Exception as e:
            logger.error(f"加载自动止损配置失败: {e}")
    
    def _save_config(self):
        """保存配置"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self._config.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info("自动止损配置已保存")
        except Exception as e:
            logger.error(f"保存自动止损配置失败: {e}")
    
    def _log(self, message: str):
        """发送日志"""
        logger.info(message)
        self.log_message.emit(f"[自动止损] {message}")
    
    def set_conditional_order_service(self, service):
        """
        设置条件单服务
        
        Args:
            service: ConditionalOrderService 实例
        """
        self._conditional_order_service = service
        logger.info("条件单服务已连接到自动止损服务")
    
    def update_config(self, **kwargs) -> bool:
        """
        更新配置
        
        Args:
            **kwargs: 配置项键值对
            
        Returns:
            是否成功
        """
        try:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
            
            self._save_config()
            self.config_changed.emit()
            
            self._log(f"配置已更新: {kwargs}")
            return True
        except Exception as e:
            logger.error(f"更新配置失败: {e}")
            return False
    
    def set_enabled(self, enabled: bool):
        """启用/禁用自动止损"""
        self.update_config(enabled=enabled)
        self._log(f"自动止损已{'启用' if enabled else '禁用'}")
    
    def is_stock_exempt(self, stock_code: str, stock_name: str = "") -> bool:
        """
        检查股票是否豁免
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            
        Returns:
            是否豁免
        """
        code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        
        # 检查是否在豁免列表中
        if code in self._config.exempt_codes:
            return True
        
        # 检查是否豁免ETF
        if self._config.exempt_etf:
            # ETF一般以 51/52/15/16/18 开头
            if code.startswith(('51', '52', '15', '16', '18')):
                return True
            # 或者名称包含 "ETF"
            if stock_name and 'ETF' in stock_name.upper():
                return True
        
        return False
    
    def add_exempt_code(self, stock_code: str):
        """添加豁免股票代码"""
        code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        if code not in self._config.exempt_codes:
            self._config.exempt_codes.append(code)
            self._save_config()
            self._log(f"已添加豁免股票: {code}")
    
    def remove_exempt_code(self, stock_code: str):
        """移除豁免股票代码"""
        code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        if code in self._config.exempt_codes:
            self._config.exempt_codes.remove(code)
            self._save_config()
            self._log(f"已移除豁免股票: {code}")
    
    def calculate_stop_price(self, cost_price: float) -> float:
        """
        计算止损价格
        
        Args:
            cost_price: 成本价
            
        Returns:
            止损价格
        """
        return round(cost_price * (1 - self._config.stop_loss_pct / 100), 3)
    
    def calculate_expire_date(self) -> str:
        """
        计算过期日期
        
        Returns:
            过期日期字符串，格式 YYYY-MM-DD，空字符串表示永不过期
        """
        if self._config.expire_days <= 0:
            return ""
        expire = datetime.now() + timedelta(days=self._config.expire_days)
        return expire.strftime("%Y-%m-%d")
    
    def on_buy_trade_added(self, stock_code: str, stock_name: str, 
                          price: float, volume: int, source: str = "") -> Optional[str]:
        """
        买入成交后处理（自动创建止损单）
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            price: 成交价格（成本价）
            volume: 成交数量
            source: 交易来源
            
        Returns:
            创建的止损单ID，如果未创建则返回None
        """
        # 检查是否启用
        if not self._config.enabled:
            return None
        
        # 检查条件单服务
        if not self._conditional_order_service:
            logger.warning("条件单服务未设置，无法创建自动止损单")
            return None
        
        # 检查是否豁免
        code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        if self.is_stock_exempt(code, stock_name):
            self._log(f"股票 {stock_name}({code}) 已豁免，跳过创建止损单")
            return None
        
        # 检查是否需要合并现有止损单
        if self._config.merge_same_stock:
            existing_orders = self._conditional_order_service.get_orders_by_stock(code)
            pending_stop_loss = [
                o for o in existing_orders 
                if o.status == "pending" and o.condition_type == "stop_loss"
            ]
            
            if pending_stop_loss:
                # 已有止损单，更新数量（累加）
                existing = pending_stop_loss[0]
                new_volume = existing.order_volume + volume
                
                # 重新计算止损价（使用加权平均成本）
                # 简化处理：使用新的买入价作为成本基准
                new_stop_price = self.calculate_stop_price(price)
                
                # 更新现有止损单（通过删除旧的创建新的）
                self._conditional_order_service.remove_order(existing.id)
                
                self._log(f"合并止损单: {stock_name}({code}) 原数量{existing.order_volume}+新增{volume}={new_volume}股")
                
                # 创建新的合并后的止损单
                return self._create_stop_loss_order(
                    code, stock_name, new_stop_price, new_volume, price
                )
        
        # 创建新止损单
        stop_price = self.calculate_stop_price(price)
        return self._create_stop_loss_order(code, stock_name, stop_price, volume, price)
    
    def _create_stop_loss_order(self, stock_code: str, stock_name: str,
                                stop_price: float, volume: int, 
                                cost_price: float) -> Optional[str]:
        """
        创建止损条件单
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            stop_price: 止损价格
            volume: 委托数量
            cost_price: 成本价
            
        Returns:
            条件单ID
        """
        try:
            # 确定委托价格
            order_price = 0.0
            if self._config.price_type == "limit":
                # 限价单，止损价再下浮一定比例确保成交
                order_price = round(stop_price * (1 - self._config.limit_offset_pct / 100), 3)
            
            # 计算过期日期
            expire_date = self.calculate_expire_date()
            
            # 备注
            remark = f"自动止损 成本:{cost_price:.3f} 止损:-{self._config.stop_loss_pct}%"
            
            # 创建条件单
            order = self._conditional_order_service.add_order(
                stock_code=stock_code,
                stock_name=stock_name,
                condition_type="stop_loss",
                trigger_price=stop_price,
                order_volume=volume,
                order_price_type=self._config.price_type,
                order_price=order_price,
                expire_date=expire_date,
                remark=remark
            )
            
            self._log(f"✅ 自动创建止损单: {stock_name}({stock_code}) "
                     f"成本{cost_price:.3f} → 止损{stop_price:.3f}({-self._config.stop_loss_pct}%) "
                     f"数量{volume}股")
            
            # 发送信号
            self.stop_loss_created.emit(stock_code, stock_name, stop_price, volume)
            
            return order.id if order else None
            
        except Exception as e:
            logger.error(f"创建自动止损单失败: {e}")
            self._log(f"❌ 创建止损单失败: {stock_name}({stock_code}) - {e}")
            return None
    
    def create_stop_loss_for_position(self, stock_code: str, stock_name: str,
                                      cost_price: float, volume: int,
                                      stop_loss_pct: float = None) -> Optional[str]:
        """
        为现有持仓创建止损单（手动触发）
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            cost_price: 成本价
            volume: 持仓数量
            stop_loss_pct: 止损比例（不传则使用默认配置）
            
        Returns:
            条件单ID
        """
        if not self._conditional_order_service:
            logger.warning("条件单服务未设置")
            return None
        
        pct = stop_loss_pct if stop_loss_pct is not None else self._config.stop_loss_pct
        stop_price = round(cost_price * (1 - pct / 100), 3)
        
        return self._create_stop_loss_order(
            stock_code, stock_name, stop_price, volume, cost_price
        )
    
    def batch_create_stop_loss(self, positions: List[Dict]) -> int:
        """
        批量为持仓创建止损单
        
        Args:
            positions: 持仓列表，每个元素包含:
                - stock_code: 股票代码
                - stock_name: 股票名称
                - cost_price: 成本价
                - volume: 可用数量
                
        Returns:
            创建的止损单数量
        """
        created_count = 0
        
        for pos in positions:
            code = pos.get('stock_code', '')
            name = pos.get('stock_name', code)
            cost = pos.get('cost_price', 0)
            volume = pos.get('volume', 0)
            
            if not code or cost <= 0 or volume <= 0:
                continue
            
            # 检查是否豁免
            if self.is_stock_exempt(code, name):
                continue
            
            # 检查是否已有止损单
            existing = self._conditional_order_service.get_orders_by_stock(code)
            if any(o.status == "pending" and o.condition_type == "stop_loss" for o in existing):
                continue
            
            # 创建止损单
            if self.create_stop_loss_for_position(code, name, cost, volume):
                created_count += 1
        
        self._log(f"批量创建止损单完成，共创建 {created_count} 个")
        return created_count


# 全局单例
_auto_stop_loss_service: Optional[AutoStopLossService] = None


def get_auto_stop_loss_service() -> AutoStopLossService:
    """
    获取全局自动止损服务实例（单例模式）
    
    Returns:
        AutoStopLossService 实例
    """
    global _auto_stop_loss_service
    if _auto_stop_loss_service is None:
        _auto_stop_loss_service = AutoStopLossService()
    return _auto_stop_loss_service
