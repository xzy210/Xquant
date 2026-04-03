#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""独立启动实盘策略中心。

运行方式:
    python run_live_strategy_center.py
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

TRADING_APP_DIR = os.path.join(ROOT_DIR, "trading_app")
if TRADING_APP_DIR not in sys.path:
    sys.path.insert(0, TRADING_APP_DIR)

from trading_app.services.ai_trade_runtime_support import AITradeRuntimeSupport
from trading_app.styles import DARK_THEME
from trading_app.widgets.live_strategy_hub_widget import LiveStrategyHubWidget, LiveStrategyHubWindow


def main(initial_tab: str = LiveStrategyHubWidget.TAB_AI) -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName("来财 - 实盘策略中心")
    app.setOrganizationName("StockTradebyZ")
    app.setApplicationVersion("1.0.0")
    app.setFont(QFont("Microsoft YaHei", 9))
    app.setStyleSheet(DARK_THEME)

    runtime_support = AITradeRuntimeSupport(project_root=ROOT_DIR)
    app.aboutToQuit.connect(runtime_support.shutdown)

    hub_window = LiveStrategyHubWindow(
        context_provider=runtime_support.build_agent_runtime_context,
        symbol_name_resolver=runtime_support.lookup_symbol_name,
        name_map=runtime_support.name_map,
        etf_name_map=runtime_support.etf_name_map,
        initial_tab=initial_tab,
    )
    hub_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    hub_window.destroyed.connect(app.quit)
    hub_window.show()
    hub_window.activateWindow()
    hub_window.raise_()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
