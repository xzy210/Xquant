from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, validator


class AIStockStrategyParams(BaseModel):
    """Shared AI stock strategy parameters for live and research flows."""

    strategy_id: str = Field("ai_stock", title="策略ID")
    model_name: str = Field("", title="模型名称")
    model_base_url: str = Field("", title="模型Base URL")
    system_prompt: str = Field("", title="系统提示词")
    prompt_template_version: str = Field("ai_stock_v1", title="提示词模板版本")

    candidate_pool_enabled: bool = Field(True, title="启用候选池")
    candidate_pool_name: str = Field("中证500趋势精选池", title="候选池名称")
    candidate_universe_file: str = Field("中证500成分股_股票列表.csv", title="候选池股票列表")
    benchmark_index_code: str = Field("000905", title="基准指数")
    max_candidates: int = Field(30, ge=1, le=500, title="候选池最大数量")
    ai_review_limit: int = Field(10, ge=1, le=100, title="AI复核数量")

    min_confidence: float = Field(0.6, ge=0.0, le=1.0, title="最低置信度")
    max_single_position_pct: float = Field(0.30, ge=0.0, le=1.0, title="单票最大仓位")
    max_total_position_pct: float = Field(0.90, ge=0.0, le=1.0, title="总仓位上限")
    max_stop_loss_pct: float = Field(0.10, ge=0.0, le=1.0, title="最大止损幅度")
    max_risk_score_for_buy: float = Field(0.80, ge=0.0, le=1.0, title="买入最大风险评分")
    block_st_stocks: bool = Field(False, title="禁止ST")
    warn_st_stocks: bool = Field(True, title="提示ST风险")
    block_limit_up_buy: bool = Field(True, title="禁止涨停买入")
    block_limit_down_sell: bool = Field(True, title="禁止跌停卖出")

    execution_sequence: str = Field("sell_first", title="执行顺序")
    buy_sizing_mode: str = Field("equal_slots", title="买入 sizing")
    buy_value_per_order: float = Field(5000.0, ge=0.0, title="单笔买入金额")
    buy_position_pct: float = Field(0.10, ge=0.0, le=1.0, title="单笔买入比例")
    sell_sizing_mode: str = Field("signal_driven", title="卖出 sizing")
    max_buy_orders_per_day: int = Field(2, ge=0, le=100, title="每日最多买单")
    max_sell_orders_per_day: int = Field(6, ge=0, le=100, title="每日最多卖单")
    max_new_positions_per_day: int = Field(2, ge=0, le=100, title="每日最多新仓")
    allow_open_new_position: bool = Field(True, title="允许新开仓")
    allow_add_to_existing: bool = Field(True, title="允许加仓")
    reserve_cash_pct: float = Field(0.20, ge=0.0, le=0.95, title="现金保留比例")
    max_daily_loss_pct: float = Field(0.02, ge=0.0, le=1.0, title="日亏损上限")

    schedule_enabled: bool = Field(False, title="AI调度启用")
    schedule_time: str = Field("14:35", title="AI调度时间")
    schedule_notify_on_complete: bool = Field(True, title="调度通知")
    schedule_auto_execute: bool = Field(True, title="调度自动执行")
    unmanaged_schedule_enabled: bool = Field(False, title="未管理巡检启用")
    unmanaged_schedule_time: str = Field("14:40", title="未管理巡检时间")

    @validator("model_name", "model_base_url", "system_prompt", "prompt_template_version", pre=True)
    def _clean_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @validator("execution_sequence")
    def _validate_execution_sequence(cls, value: str) -> str:
        allowed = {"sell_first", "buy_first", "sell_only", "buy_only"}
        text = str(value or "sell_first").strip().lower()
        return text if text in allowed else "sell_first"

    @validator("buy_sizing_mode")
    def _validate_buy_sizing_mode(cls, value: str) -> str:
        allowed = {"equal_slots", "fixed_amount", "fixed_pct"}
        text = str(value or "equal_slots").strip().lower()
        return text if text in allowed else "equal_slots"

    @validator("sell_sizing_mode")
    def _validate_sell_sizing_mode(cls, value: str) -> str:
        allowed = {"signal_driven", "full_exit", "half_exit"}
        text = str(value or "signal_driven").strip().lower()
        return text if text in allowed else "signal_driven"

    @validator("schedule_time", "unmanaged_schedule_time")
    def _validate_time_text(cls, value: str) -> str:
        text = str(value or "").strip()
        parts = text.split(":")
        if len(parts) != 2:
            return "14:35"
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError:
            return "14:35"
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return "14:35"
        return f"{hour:02d}:{minute:02d}"

    @classmethod
    def field_names(cls) -> set[str]:
        return set(getattr(cls, "model_fields", None) or getattr(cls, "__fields__", {}))

    @classmethod
    def from_mapping(cls, values: dict | None = None, **overrides: Any) -> "AIStockStrategyParams":
        payload = dict(values or {})
        payload.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**{key: value for key, value in payload.items() if key in cls.field_names()})

    def to_dict(self) -> dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump()
        return self.dict()

    def params_hash(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


__all__ = ["AIStockStrategyParams"]
