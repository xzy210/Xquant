from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .models import RiskPolicyDecision, StrategyRiskContext

if TYPE_CHECKING:  # pragma: no cover
    from ..trade_execution_service import ExecutionRequest

logger = logging.getLogger(__name__)


@runtime_checkable
class StrategyRiskPolicy(Protocol):
    """Strategy-specific risk policy invoked by the unified trade gateway.

    Implementations must be thread-safe and side-effect-free: they may read
    external state (broker, budget, strategy internal state) but MUST NOT
    mutate order state or submit orders. The registry aggregates decisions
    and the gateway is responsible for applying them.
    """

    strategy_id: str

    def evaluate(
        self,
        request: "ExecutionRequest",
        context: StrategyRiskContext,
    ) -> RiskPolicyDecision: ...


class NoopStrategyRiskPolicy:
    """Default no-op policy used when no strategy-specific rules are registered.

    Returns an approval decision unconditionally. Useful as a placeholder and
    for tests that want to bypass strategy risk evaluation.
    """

    def __init__(self, strategy_id: str = "__noop__") -> None:
        self.strategy_id = strategy_id

    def evaluate(
        self,
        request: "ExecutionRequest",
        context: StrategyRiskContext,
    ) -> RiskPolicyDecision:
        return RiskPolicyDecision.approve(reason="无策略风控规则")
