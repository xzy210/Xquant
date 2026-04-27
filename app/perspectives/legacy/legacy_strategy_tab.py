# -*- coding: utf-8 -*-
"""Legacy StrategyMainWindow embedded as a tab."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STRATEGY_APP_DIR = _PROJECT_ROOT / "strategy_app"


def _ensure_legacy_import_path() -> None:
    strategy_path = str(_STRATEGY_APP_DIR)
    root_path = str(_PROJECT_ROOT)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)
    if strategy_path not in sys.path:
        sys.path.insert(0, strategy_path)


def create_legacy_strategy_tab(parent: QWidget | None = None) -> QWidget:
    """Return the legacy strategy research window as an embeddable QWidget."""
    _ensure_legacy_import_path()

    from strategy_app.main_window import StrategyMainWindow

    legacy_window = StrategyMainWindow()
    legacy_window.setParent(parent)
    legacy_window.setWindowFlags(Qt.WindowType.Widget)
    legacy_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    legacy_window.setObjectName("legacy_strategy_tab")
    return legacy_window


__all__ = ["create_legacy_strategy_tab"]
