"""
ETF轮动实盘 - 配置管理

管理策略参数、ETF池、交易参数等所有可配置项。
"""
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RotationConfig:
    """ETF轮动实盘配置"""

    # --- ETF标的池 ---
    etf_pool: List[str] = field(default_factory=lambda: [
        '510880', '159949', '513100', '518880'
    ])

    # --- 策略参数（与回测保持一致）---
    factor_config: List[tuple] = field(default_factory=lambda: [
        ('bias_momentum_fast', 0.3),
        ('slope_momentum_fast', 0.3),
        ('efficiency_momentum_fast', 0.4),
    ])
    rebalance_threshold: float = 1.5
    momentum_window: int = 25
    zscore_window: int = 60
    empty_threshold: float = -0.5
    enable_empty_position: bool = True
    rebalance_period: int = 1            # 调仓周期（交易日）: 1=每日, 5=每周, 20=每月

    # --- 专用资金（资金隔离，始终启用）---
    use_dedicated_capital: bool = True     # 内部标志，始终为 True
    dedicated_capital: float = 100000.0   # 划拨给本策略的启动资金（元）

    # --- 交易费用 ---
    buy_commission_rate: float = 0.0001   # 买入佣金率（万1）
    sell_commission_rate: float = 0.0001  # 卖出佣金率（万1）
    min_commission: float = 5.0           # 每笔最低佣金（元）

    # --- 交易参数 ---
    cash_ratio: float = 0.99            # 买入时使用的资金比例
    min_trade_amount: float = 1000.0    # 最小交易金额
    price_type: str = "market"          # market / limit
    limit_slip_pct: float = 0.1         # 限价单滑点百分比

    # --- 定时调度 ---
    auto_enabled: bool = False
    check_time: str = "14:50"           # 每日信号检查时间 (HH:MM)
    data_update_time: str = "14:30"     # 数据更新时间

    # --- 通知 ---
    notify_on_signal: bool = True       # 产生信号时通知
    notify_on_trade: bool = True        # 交易执行后通知
    notify_daily_report: bool = True    # 每日推送简报

    # --- 风控 ---
    max_trades_per_day: int = 2
    min_hold_days: int = 0              # 最少持有天数（0=不限制）
    max_single_loss_pct: float = 15.0   # 单笔最大亏损比例
    trading_start: str = "09:30"
    trading_end: str = "14:57"          # 留3分钟buffer
    enable_risk_check: bool = True

    # --- 风控（高级）---
    enable_trailing_stop: bool = True     # 移动止盈
    trailing_stop_pct: float = 0.08       # 持仓从最高价回撤此比例时触发止盈
    enable_drawdown_protection: bool = True  # 账户回撤保护
    max_drawdown_pct: float = 0.15        # 账户最大回撤比例
    drawdown_cooldown_days: int = 10      # 触发回撤保护后冷却天数

    def to_strategy_params(self) -> dict:
        """转换为策略引擎接受的参数字典"""
        return {
            'etf_pool': self.etf_pool,
            'factor_config': self.factor_config,
            'rebalance_threshold': self.rebalance_threshold,
            'momentum_window': self.momentum_window,
            'zscore_window': self.zscore_window,
            'empty_threshold': self.empty_threshold,
            'enable_empty_position': self.enable_empty_position,
            'rebalance_period': self.rebalance_period,
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'RotationConfig':
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if 'factor_config' in filtered and isinstance(filtered['factor_config'], list):
            filtered['factor_config'] = [tuple(item) for item in filtered['factor_config']]
        # 向后兼容：旧配置使用 bias_weight/slope_weight/efficiency_weight
        if 'factor_config' not in filtered:
            fc = []
            if 'bias_weight' in data:
                fc.append(('bias_momentum_fast', data['bias_weight']))
            if 'slope_weight' in data:
                fc.append(('slope_momentum_fast', data['slope_weight']))
            if 'efficiency_weight' in data:
                fc.append(('efficiency_momentum_fast', data['efficiency_weight']))
            if fc:
                filtered['factor_config'] = fc
            for k in ('bias_weight', 'slope_weight', 'efficiency_weight'):
                filtered.pop(k, None)
        return cls(**filtered)


class ConfigManager:
    """配置持久化管理"""

    CONFIG_FILE = "rotation_config.json"

    def __init__(self, config_dir: Optional[str] = None):
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path(__file__).parent / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / self.CONFIG_FILE

    def load(self) -> RotationConfig:
        if not self.config_path.exists():
            logger.info("配置文件不存在，使用默认配置")
            return RotationConfig()
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f"已加载配置: {self.config_path}")
            return RotationConfig.from_dict(data)
        except Exception as e:
            logger.error(f"加载配置失败: {e}，使用默认配置")
            return RotationConfig()

    def save(self, config: RotationConfig):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"配置已保存: {self.config_path}")
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
