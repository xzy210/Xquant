from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class StrategyPanelContext:
    """Shared strategy identity used by live strategy panels."""

    strategy_id: str
    strategy_name: str
    virtual_account_id: str
    owner_type: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.strategy_name or self.strategy_id
