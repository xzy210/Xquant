# -*- coding: utf-8 -*-
"""Legacy application tabs embedded in the new shell."""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget


def create_legacy_strategy_tab(parent: QWidget | None = None) -> QWidget:
    from .legacy_strategy_tab import create_legacy_strategy_tab as factory

    return factory(parent)


__all__ = ["create_legacy_strategy_tab"]
