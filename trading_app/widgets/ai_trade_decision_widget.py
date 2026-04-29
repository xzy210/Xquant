# -*- coding: utf-8 -*-
"""Compatibility exports for AI live decision widgets.

The implementation lives in ``trading_app.widgets.ai_trade_decision`` submodules.
This module keeps the historical import path stable.
"""
from __future__ import annotations

from trading_app.widgets.ai_trade_decision.account_panel import AccountPanel
from trading_app.widgets.ai_trade_decision.decision_panel import DecisionPanel
from trading_app.widgets.ai_trade_decision.dialogs import AIStrategyConfigDialog, SchedulerSettingsDialog
from trading_app.widgets.ai_trade_decision.panels import (
    AITradeDecisionPanel,
    AITradeDecisionWindow,
    UnmanagedPositionPanel,
)

__all__ = [
    "AccountPanel",
    "DecisionPanel",
    "SchedulerSettingsDialog",
    "AIStrategyConfigDialog",
    "AITradeDecisionPanel",
    "UnmanagedPositionPanel",
    "AITradeDecisionWindow",
]
