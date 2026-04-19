"""AI stock strategy risk policy for the unified trade gateway.

This policy wraps the existing :class:`RiskGuardService` so AI decisions get
evaluated through the same :class:`StrategyRiskPolicy` mechanism as ETF
rotation. Rule implementation is left untouched inside ``RiskGuardService``;
this module only adapts its ``RiskCheckResult`` to the gateway-friendly
``RiskPolicyDecision`` shape.

The policy is auto-registered by :class:`TradeExecutionService`, so any order
with ``strategy_id == AI_STOCK_STRATEGY_ID`` flows through it without the
caller having to opt in.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .agent_context_service import BrokerContext
from .risk_guard_service import RiskGuardService
from .strategy_constants import AI_STOCK_STRATEGY_ID
from .strategy_risk import (
    RiskPolicyDecision,
    StrategyRiskContext,
)

logger = logging.getLogger(__name__)


class AIStockRiskPolicy:
    """Strategy-level risk policy for AI-driven stock orders."""

    strategy_id: str = AI_STOCK_STRATEGY_ID

    def __init__(self, risk_guard: Optional[RiskGuardService] = None) -> None:
        self.risk_guard = risk_guard or RiskGuardService()

    def evaluate(
        self,
        request: Any,
        context: StrategyRiskContext,
    ) -> RiskPolicyDecision:
        decision = getattr(request, "decision", None)
        if decision is None:
            return RiskPolicyDecision.approve(
                "AI 订单未附带 TradeDecision，跳过 AI 规则检查"
            )

        pre_eval = getattr(request, "risk_result", None)
        if pre_eval is not None:
            if not pre_eval.passed:
                reason = "；".join(pre_eval.blocked_reasons) or "AI 风控未通过"
                return RiskPolicyDecision.block(
                    reason,
                    metadata={"rule": "pre_evaluated"},
                )
            if pre_eval.warnings:
                return RiskPolicyDecision.warn(
                    "；".join(pre_eval.warnings),
                    metadata={"rule": "pre_evaluated_warnings"},
                )
            return RiskPolicyDecision.approve("预评估风控已通过")

        broker_ctx = context.broker if isinstance(context.broker, BrokerContext) else None
        try:
            result = self.risk_guard.evaluate(decision, broker=broker_ctx)
        except Exception as exc:
            logger.exception("AIStockRiskPolicy 调用 RiskGuardService 失败")
            return RiskPolicyDecision.block(
                f"AI 风控评估异常: {exc}",
                metadata={"rule": "ai_stock_evaluate_error"},
            )

        if not result.passed:
            reason = "；".join(result.blocked_reasons) or "AI 风控未通过"
            return RiskPolicyDecision.block(
                reason,
                metadata={
                    "rule": "ai_stock_rules",
                    "risk_level": result.overall_risk_level,
                },
            )
        if result.warnings:
            return RiskPolicyDecision.warn(
                "；".join(result.warnings),
                metadata={
                    "rule": "ai_stock_warnings",
                    "risk_level": result.overall_risk_level,
                },
            )
        return RiskPolicyDecision.approve(
            "AI 风控通过",
            metadata={"risk_level": result.overall_risk_level},
        )
