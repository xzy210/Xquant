from __future__ import annotations

from typing import List, Tuple

AI_STOCK_STRATEGY_ID = "ai_trade_decision_center"
AI_STOCK_STRATEGY_NAME = "AI实盘决策"
AI_STOCK_VIRTUAL_ACCOUNT_ID = "va_ai_trade_decision_center"

# 未管理账户：承载券商里未被任何策略认领的现金和持仓。
# 所有策略使用的是虚拟账户（dedicated capital），而券商账户上可能仍存在
# 用户手动买入或历史遗留持仓，以及尚未分配给任何策略的闲置现金。
# 把这些纳入 unmanaged 虚拟账户后，"主账本聚合 ≡ 券商账户实况"成立，
# 账户总资产/余额完全由主账本推导，不再依赖重复查券商。
UNMANAGED_STRATEGY_ID = "unmanaged"
UNMANAGED_STRATEGY_NAME = "未管理账户"
UNMANAGED_VIRTUAL_ACCOUNT_ID = "va_unmanaged"

OWNER_TYPE_AI = "ai"
OWNER_TYPE_ETF_ROTATION = "etf_rotation"
OWNER_TYPE_UNMANAGED = "unmanaged"
OWNER_TYPE_OTHER = "other"


def normalize_symbol_code(symbol_code: str) -> str:
    value = (symbol_code or "").strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    return value


def load_default_etf_rotation_profile() -> Tuple[str, str, str, List[str], float]:
    try:
        from live_rotation.config import ConfigManager

        cfg = ConfigManager().load()
        strategy_id = (cfg.strategy_id or "etf_rotation").strip() or "etf_rotation"
        strategy_name = "ETF轮动实盘"
        virtual_account_id = f"va_{strategy_id}"
        symbols = [normalize_symbol_code(code) for code in (cfg.etf_pool or []) if normalize_symbol_code(code)]
        capital_limit = float(getattr(cfg, "dedicated_capital", 0.0) or 0.0)
        return strategy_id, strategy_name, virtual_account_id, symbols, capital_limit
    except Exception:
        return "etf_rotation", "ETF轮动实盘", "va_etf_rotation", [], 0.0
