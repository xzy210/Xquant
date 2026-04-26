from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .strategy_constants import (
    AI_STOCK_STRATEGY_ID,
    AI_STOCK_STRATEGY_NAME,
    AI_STOCK_VIRTUAL_ACCOUNT_ID,
    OWNER_TYPE_AI,
    OWNER_TYPE_ETF_ROTATION,
    OWNER_TYPE_OTHER,
    OWNER_TYPE_UNMANAGED,
    UNMANAGED_STRATEGY_ID,
    UNMANAGED_STRATEGY_NAME,
    UNMANAGED_VIRTUAL_ACCOUNT_ID,
    load_default_etf_rotation_profile,
    normalize_symbol_code,
)


@dataclass(frozen=True)
class StrategySpec:
    """Unified strategy metadata shared by live plugins, budget ledger, and ownership registry."""

    strategy_id: str
    strategy_name: str = ""
    virtual_account_id: str = ""
    owner_type: str = OWNER_TYPE_OTHER
    capital_limit: float = 0.0
    enabled: bool = True
    is_test: bool = False
    hidden: bool = False
    is_unmanaged: bool = False
    asset_class: str = ""
    frequency: str = ""
    universe: List[str] = field(default_factory=list)
    plugin_id: str = ""
    plugin_name: str = ""
    plugin_tab_key: str = ""
    plugin_tab_title: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""

    def __post_init__(self) -> None:
        strategy_id = str(self.strategy_id or "").strip()
        strategy_name = str(self.strategy_name or strategy_id).strip()
        virtual_account_id = str(self.virtual_account_id or f"va_{strategy_id}").strip() if strategy_id else ""
        owner_type = str(self.owner_type or OWNER_TYPE_OTHER).strip() or OWNER_TYPE_OTHER
        plugin_id = str(self.plugin_id or strategy_id).strip()
        plugin_name = str(self.plugin_name or strategy_name or plugin_id).strip()
        universe = [normalize_symbol_code(code) for code in list(self.universe or []) if normalize_symbol_code(code)]
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "strategy_name", strategy_name)
        object.__setattr__(self, "virtual_account_id", virtual_account_id)
        object.__setattr__(self, "owner_type", owner_type)
        object.__setattr__(self, "capital_limit", float(self.capital_limit or 0.0))
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(self, "is_test", bool(self.is_test))
        object.__setattr__(self, "hidden", bool(self.hidden))
        object.__setattr__(self, "is_unmanaged", bool(self.is_unmanaged))
        object.__setattr__(self, "asset_class", str(self.asset_class or "").strip())
        object.__setattr__(self, "frequency", str(self.frequency or "").strip())
        object.__setattr__(self, "universe", universe)
        object.__setattr__(self, "plugin_id", plugin_id)
        object.__setattr__(self, "plugin_name", plugin_name)
        object.__setattr__(self, "plugin_tab_key", str(self.plugin_tab_key or "").strip())
        object.__setattr__(self, "plugin_tab_title", str(self.plugin_tab_title or "").strip())
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        object.__setattr__(self, "updated_at", str(self.updated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        if not strategy_id:
            raise ValueError("strategy_id is required")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_budget_kwargs(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "virtual_account_id": self.virtual_account_id,
            "capital_limit": self.capital_limit,
            "enabled": self.enabled,
            "is_test": self.is_test,
            "hidden": self.hidden,
            "is_unmanaged": self.is_unmanaged,
        }

    def to_ownership_kwargs(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "virtual_account_id": self.virtual_account_id,
            "owner_type": self.owner_type,
        }

    def to_plugin_metadata(self) -> dict:
        return {
            "plugin_id": self.plugin_id,
            "plugin_name": self.plugin_name,
            "tab_key": self.plugin_tab_key,
            "tab_title": self.plugin_tab_title,
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "virtual_account_id": self.virtual_account_id,
            "asset_class": self.asset_class,
            "frequency": self.frequency,
            "metadata": dict(self.metadata or {}),
        }


class StrategySpecService:
    """Single source of built-in strategy identity and metadata."""

    def __init__(self) -> None:
        self._extra_specs: Dict[str, StrategySpec] = {}

    def register(self, spec: StrategySpec, *, override: bool = True) -> StrategySpec:
        if not override and spec.strategy_id in self._extra_specs:
            raise ValueError(f"duplicate strategy spec: {spec.strategy_id}")
        self._extra_specs[spec.strategy_id] = spec
        return spec

    def get(self, strategy_id: str, *, fallback_name: str = "", virtual_account_id: str = "") -> StrategySpec:
        normalized = str(strategy_id or "").strip()
        for spec in self.list_specs(include_hidden=True, include_test=True):
            if spec.strategy_id == normalized:
                return spec
        if not normalized:
            raise ValueError("strategy_id is required")
        return StrategySpec(
            strategy_id=normalized,
            strategy_name=fallback_name or normalized,
            virtual_account_id=virtual_account_id or f"va_{normalized}",
            owner_type=OWNER_TYPE_OTHER,
            plugin_id=normalized,
            plugin_name=fallback_name or normalized,
        )

    def ai_stock(self) -> StrategySpec:
        return StrategySpec(
            strategy_id=AI_STOCK_STRATEGY_ID,
            strategy_name=AI_STOCK_STRATEGY_NAME,
            virtual_account_id=AI_STOCK_VIRTUAL_ACCOUNT_ID,
            owner_type=OWNER_TYPE_AI,
            asset_class="stock",
            frequency="daily",
            plugin_id=AI_STOCK_STRATEGY_ID,
            plugin_name=AI_STOCK_STRATEGY_NAME,
            plugin_tab_key="ai",
            plugin_tab_title="AI策略",
            metadata={"source": "builtin", "strategy_family": "ai_stock"},
        )

    def etf_rotation(self) -> StrategySpec:
        strategy_id, strategy_name, virtual_account_id, symbols, capital_limit = load_default_etf_rotation_profile()
        return StrategySpec(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            owner_type=OWNER_TYPE_ETF_ROTATION,
            capital_limit=capital_limit,
            asset_class="etf",
            frequency="daily",
            universe=symbols,
            plugin_id=strategy_id,
            plugin_name=strategy_name,
            plugin_tab_key="etf",
            plugin_tab_title="ETF轮动",
            metadata={"source": "builtin", "strategy_family": "etf_rotation"},
        )

    def unmanaged(self) -> StrategySpec:
        return StrategySpec(
            strategy_id=UNMANAGED_STRATEGY_ID,
            strategy_name=UNMANAGED_STRATEGY_NAME,
            virtual_account_id=UNMANAGED_VIRTUAL_ACCOUNT_ID,
            owner_type=OWNER_TYPE_UNMANAGED,
            capital_limit=0.0,
            enabled=False,
            is_unmanaged=True,
            asset_class="mixed",
            plugin_id=UNMANAGED_STRATEGY_ID,
            plugin_name="未管理持仓",
            plugin_tab_key="unmanaged",
            plugin_tab_title="未管理持仓",
            metadata={"source": "builtin", "strategy_family": "system_unmanaged"},
        )

    def builtin_specs(self) -> List[StrategySpec]:
        return [self.ai_stock(), self.etf_rotation(), self.unmanaged()]

    def list_specs(self, *, include_hidden: bool = True, include_test: bool = True) -> List[StrategySpec]:
        merged: Dict[str, StrategySpec] = {spec.strategy_id: spec for spec in self.builtin_specs()}
        merged.update(self._extra_specs)
        specs = list(merged.values())
        if not include_hidden:
            specs = [spec for spec in specs if not spec.hidden]
        if not include_test:
            specs = [spec for spec in specs if not spec.is_test]
        return sorted(specs, key=lambda item: (item.plugin_name or item.strategy_name, item.strategy_id))


_strategy_spec_service: Optional[StrategySpecService] = None


def get_strategy_spec_service() -> StrategySpecService:
    global _strategy_spec_service
    if _strategy_spec_service is None:
        _strategy_spec_service = StrategySpecService()
    return _strategy_spec_service
