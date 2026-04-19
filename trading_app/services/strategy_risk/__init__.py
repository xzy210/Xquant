"""Strategy-specific risk policy framework used by the unified trade gateway.

The unified :class:`TradeExecutionService` performs global, account-level risk
checks (trading time, duplicate detection, budget, broker constraints). This
package adds an extensible, strategy-scoped layer on top of that: each strategy
(AI stock, ETF rotation, future grid/pairs) can register its own
:class:`StrategyRiskPolicy` to enforce idiosyncratic rules (daily trade caps,
minimum holding period, per-position loss limits, etc.) without forking the
execution gateway.

Usage sketch::

    from trading_app.services.strategy_risk import (
        get_strategy_risk_registry,
        StrategyRiskPolicy,
        RiskPolicyDecision,
    )

    class MyPolicy:
        strategy_id = "etf_rotation"

        def evaluate(self, request, context):
            if some_rule_violated(request):
                return RiskPolicyDecision.block("超过每日交易次数上限")
            return RiskPolicyDecision.approve()

    get_strategy_risk_registry().register(MyPolicy())
"""

from .models import (
    RISK_LEVEL_BLOCK,
    RISK_LEVEL_INFO,
    RISK_LEVEL_WARN,
    RiskPolicyDecision,
    StrategyRiskContext,
)
from .policy import NoopStrategyRiskPolicy, StrategyRiskPolicy
from .registry import (
    StrategyRiskRegistry,
    get_strategy_risk_registry,
    reset_strategy_risk_registry,
)

__all__ = [
    "RISK_LEVEL_BLOCK",
    "RISK_LEVEL_INFO",
    "RISK_LEVEL_WARN",
    "RiskPolicyDecision",
    "StrategyRiskContext",
    "NoopStrategyRiskPolicy",
    "StrategyRiskPolicy",
    "StrategyRiskRegistry",
    "get_strategy_risk_registry",
    "reset_strategy_risk_registry",
]
