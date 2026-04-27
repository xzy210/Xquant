# -*- coding: utf-8 -*-
"""Legacy ETF rotation live widget embedded as a tab."""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from live_rotation.rotation_engine import RotationEngine
from live_rotation.widget import ETFRotationLiveWidget


def create_legacy_rotation_tab(parent: QWidget | None = None) -> ETFRotationLiveWidget:
    """Return the legacy ETF rotation live UI as an embeddable QWidget."""
    engine = RotationEngine()
    widget = ETFRotationLiveWidget(engine=engine, parent=parent)
    widget.setObjectName("legacy_rotation_tab")
    widget._legacy_engine = engine  # type: ignore[attr-defined]
    return widget


__all__ = ["create_legacy_rotation_tab"]
