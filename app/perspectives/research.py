# -*- coding: utf-8 -*-
"""Native research perspective factories for the application shell."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QWidget

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STRATEGY_APP_DIR = _PROJECT_ROOT / "strategy_app"
_DATA_DIR = _PROJECT_ROOT / "data"
_STOCKLIST_PATH = _PROJECT_ROOT / "stocklist" / "stocklist.csv"


def _ensure_strategy_import_path() -> None:
    root_path = str(_PROJECT_ROOT)
    strategy_path = str(_STRATEGY_APP_DIR)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)
    if strategy_path not in sys.path:
        sys.path.insert(0, strategy_path)


def create_cross_sectional_backtest_tab(parent: QWidget | None = None) -> QWidget:
    _ensure_strategy_import_path()
    from strategy_app.widgets.cross_sectional_backtest_widget import CrossSectionalBacktestWidget

    widget = CrossSectionalBacktestWidget(str(_DATA_DIR))
    widget.setParent(parent)
    return widget


def create_factor_library_tab(parent: QWidget | None = None) -> QWidget:
    _ensure_strategy_import_path()
    from strategy_app.widgets.factor_library_widget import FactorLibraryWidget

    widget = FactorLibraryWidget(str(_DATA_DIR), str(_STOCKLIST_PATH))
    widget.setParent(parent)
    return widget


def create_ai_training_tab(parent: QWidget | None = None) -> QWidget:
    _ensure_strategy_import_path()
    from strategy_app.widgets.ai_trading_widget import AITradingWidget

    widget = AITradingWidget(str(_DATA_DIR), str(_STOCKLIST_PATH), parent=parent)
    return widget


__all__ = [
    "create_ai_training_tab",
    "create_cross_sectional_backtest_tab",
    "create_factor_library_tab",
]
