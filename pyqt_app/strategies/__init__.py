from .base_strategy import BaseStrategy
from .rebound_strategy import ContinuousDropReboundStrategy

# 策略注册表
STRATEGIES = {
    "continuous_drop_rebound": ContinuousDropReboundStrategy
}

def get_strategy(name: str) -> BaseStrategy:
    """工厂方法获取策略实例"""
    if name in STRATEGIES:
        return STRATEGIES[name]()
    return None

def get_all_strategies() -> dict:
    """获取所有可用策略 {id: name}"""
    return {k: v().name for k, v in STRATEGIES.items()}
