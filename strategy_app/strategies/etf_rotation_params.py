from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Tuple

from pydantic import BaseModel, Field, validator


class ETFRotationParams(BaseModel):
    """Shared ETF rotation strategy, decision, and guard parameters."""

    etf_pool: List[str] = Field(
        default_factory=lambda: ["510880", "159949", "513100", "518880"],
        title="ETF Pool",
        description="ETF symbols eligible for rotation.",
        min_items=1,
    )
    factor_config: List[Tuple[str, float]] = Field(
        default_factory=lambda: [
            ("bias_momentum_fast", 0.3),
            ("slope_momentum_fast", 0.3),
            ("efficiency_momentum_fast", 0.4),
        ],
        title="Factor Config",
        description="Momentum factor names and weights used to build the composite score.",
        min_items=1,
    )
    rebalance_threshold: float = Field(
        1.5,
        ge=0.0,
        le=100.0,
        title="Rebalance Threshold",
        description="Switch when the top ETF score is greater than the current holding score times this threshold.",
    )
    momentum_window: int = Field(
        25,
        ge=2,
        le=500,
        title="Momentum Window",
        description="Lookback window used by ETF momentum factors.",
    )
    zscore_window: int = Field(
        60,
        ge=5,
        le=1000,
        title="Z-Score Window",
        description="Lookback window used to normalize factor values into z-scores.",
    )
    empty_threshold: float = Field(
        -0.5,
        ge=-100.0,
        le=100.0,
        title="Empty Position Threshold",
        description="If every ETF score is below this value, the strategy stays empty or sells all holdings.",
    )
    enable_empty_position: bool = Field(
        True,
        title="Enable Empty Position",
        description="Allow the strategy to hold cash when all ETF scores are weak.",
    )
    rebalance_period: int = Field(
        1,
        ge=1,
        le=250,
        title="Rebalance Period",
        description="Minimum rebalance interval measured in trading days.",
    )
    enable_trailing_stop: bool = Field(
        True,
        title="Enable Trailing Stop",
        description="Enable per-holding trailing stop guard.",
    )
    trailing_stop_pct: float = Field(
        0.08,
        ge=0.0,
        le=1.0,
        title="Trailing Stop %",
        description="Sell when the current holding drops this ratio from its highest observed price.",
    )
    enable_drawdown_protection: bool = Field(
        True,
        title="Enable Drawdown Protection",
        description="Enable account-level drawdown guard.",
    )
    max_drawdown_pct: float = Field(
        0.15,
        ge=0.0,
        le=1.0,
        title="Max Drawdown %",
        description="Maximum account drawdown ratio before the strategy exits and enters cooldown.",
    )
    drawdown_cooldown_days: int = Field(
        10,
        ge=0,
        le=250,
        title="Drawdown Cooldown Days",
        description="Trading-day cooldown after account drawdown protection is triggered.",
    )

    _LEGACY_WEIGHT_KEYS: ClassVar[Dict[str, str]] = {
        "bias_weight": "bias_momentum_fast",
        "slope_weight": "slope_momentum_fast",
        "efficiency_weight": "efficiency_momentum_fast",
    }

    @validator("etf_pool")
    def _normalize_etf_pool(cls, value: List[str]) -> List[str]:
        normalized = [str(item).strip() for item in value or [] if str(item).strip()]
        if not normalized:
            raise ValueError("etf_pool must contain at least one symbol")
        return normalized

    @validator("factor_config", pre=True)
    def _normalize_factor_config(cls, value: Any) -> List[Tuple[str, float]]:
        items = list(value or [])
        normalized: List[Tuple[str, float]] = []
        for item in items:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("factor") or "").strip()
                weight = item.get("weight", 0.0)
            else:
                try:
                    name, weight = item
                except (TypeError, ValueError):
                    raise ValueError("factor_config items must be (name, weight) pairs") from None
                name = str(name).strip()
            if not name:
                raise ValueError("factor name cannot be empty")
            normalized.append((name, float(weight)))
        if not normalized:
            raise ValueError("factor_config must contain at least one factor")
        return normalized

    @classmethod
    def field_names(cls) -> set[str]:
        return set(getattr(cls, "model_fields", None) or getattr(cls, "__fields__", {}))

    @classmethod
    def from_mapping(cls, values: dict | None = None, **overrides: Any) -> "ETFRotationParams":
        payload = dict(values or {})
        legacy_factors = []
        for key, factor_name in cls._LEGACY_WEIGHT_KEYS.items():
            if key in payload:
                legacy_factors.append((factor_name, payload.pop(key)))
        if legacy_factors and "factor_config" not in payload:
            payload["factor_config"] = legacy_factors
        payload.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**{key: value for key, value in payload.items() if key in cls.field_names()})

    def to_dict(self) -> dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump()
        return self.dict()


__all__ = ["ETFRotationParams"]
