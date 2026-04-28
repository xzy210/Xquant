from __future__ import annotations

from typing import List, Tuple

AI_STOCK_STRATEGY_ID = "ai_trade_decision_center"
AI_STOCK_STRATEGY_NAME = "AI实盘决策"
AI_STOCK_VIRTUAL_ACCOUNT_ID = "va_ai_trade_decision_center"

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
