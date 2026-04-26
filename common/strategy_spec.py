from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List


def normalize_strategy_symbol(symbol_code: str) -> str:
    value = str(symbol_code or "").strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    return value


@dataclass(frozen=True)
class StrategySpec:
    """Common strategy identity and metadata shared by research, backtest, and live code."""

    strategy_id: str
    strategy_name: str = ""
    virtual_account_id: str = ""
    owner_type: str = "other"
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
        if not strategy_id:
            raise ValueError("strategy_id is required")

        strategy_name = str(self.strategy_name or strategy_id).strip()
        virtual_account_id = str(self.virtual_account_id or f"va_{strategy_id}").strip()
        owner_type = str(self.owner_type or "other").strip() or "other"
        plugin_id = str(self.plugin_id or strategy_id).strip()
        plugin_name = str(self.plugin_name or strategy_name or plugin_id).strip()
        universe = [normalize_strategy_symbol(code) for code in list(self.universe or []) if normalize_strategy_symbol(code)]

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
