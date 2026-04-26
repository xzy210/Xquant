from __future__ import annotations

from typing import Dict, List, Optional

from common.strategy_spec import StrategySpec as _StrategySpec

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
)



class StrategySpecService:
    """Single source of built-in strategy identity and metadata."""

    def __init__(self) -> None:
        self._extra_specs: Dict[str, _StrategySpec] = {}

    def register(self, spec: _StrategySpec, *, override: bool = True) -> _StrategySpec:
        if not override and spec.strategy_id in self._extra_specs:
            raise ValueError(f"duplicate strategy spec: {spec.strategy_id}")
        self._extra_specs[spec.strategy_id] = spec
        return spec

    def get(self, strategy_id: str, *, fallback_name: str = "", virtual_account_id: str = "") -> _StrategySpec:
        normalized = str(strategy_id or "").strip()
        for spec in self.list_specs(include_hidden=True, include_test=True):
            if spec.strategy_id == normalized:
                return spec
        if not normalized:
            raise ValueError("strategy_id is required")
        return _StrategySpec(
            strategy_id=normalized,
            strategy_name=fallback_name or normalized,
            virtual_account_id=virtual_account_id or f"va_{normalized}",
            owner_type=OWNER_TYPE_OTHER,
            plugin_id=normalized,
            plugin_name=fallback_name or normalized,
        )

    def ai_stock(self) -> _StrategySpec:
        return _StrategySpec(
            strategy_id=AI_STOCK_STRATEGY_ID,
            strategy_name=AI_STOCK_STRATEGY_NAME,
            virtual_account_id=AI_STOCK_VIRTUAL_ACCOUNT_ID,
            owner_type=OWNER_TYPE_AI,
            asset_class="stock",
            frequency="daily",
            plugin_id=AI_STOCK_STRATEGY_ID,
            plugin_name=AI_STOCK_STRATEGY_NAME,
            plugin_tab_key="ai",
            plugin_tab_title="AI实盘决策",
            metadata={"source": "builtin", "strategy_family": "ai_stock"},
        )

    def etf_rotation(self) -> _StrategySpec:
        strategy_id, strategy_name, virtual_account_id, symbols, capital_limit = load_default_etf_rotation_profile()
        return _StrategySpec(
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
            plugin_tab_title="ETF轮动实盘",
            metadata={"source": "builtin", "strategy_family": "etf_rotation"},
        )

    def unmanaged(self) -> _StrategySpec:
        return _StrategySpec(
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

    def builtin_specs(self) -> List[_StrategySpec]:
        return [self.ai_stock(), self.etf_rotation(), self.unmanaged()]

    def list_specs(self, *, include_hidden: bool = True, include_test: bool = True) -> List[_StrategySpec]:
        merged: Dict[str, _StrategySpec] = {spec.strategy_id: spec for spec in self.builtin_specs()}
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
