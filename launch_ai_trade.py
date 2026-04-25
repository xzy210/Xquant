#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""独立启动 AI 交易决策中心。

运行方式:
    python launch_ai_trade.py
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
# 仅将项目根目录加入 sys.path，保持 trading_app / common 等顶层包单一身份。
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from trading_app.styles import DARK_THEME
from trading_app.services.ai_trade_runtime_support import AITradeRuntimeSupport
from trading_app.widgets.ai_trade_decision_widget import AITradeDecisionWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName("来财 - AI交易决策中心")
    app.setOrganizationName("StockTradebyZ")
    app.setApplicationVersion("1.0.0")
    app.setFont(QFont("Microsoft YaHei", 9))
    app.setStyleSheet(DARK_THEME)

    runtime_support = AITradeRuntimeSupport(project_root=ROOT_DIR)
    app.aboutToQuit.connect(runtime_support.shutdown)

    ai_window = AITradeDecisionWindow(
        context_provider=runtime_support.build_agent_runtime_context,
        symbol_name_resolver=runtime_support.lookup_symbol_name,
        name_map=runtime_support.name_map,
        etf_name_map=runtime_support.etf_name_map,
    )
    ai_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    ai_window.destroyed.connect(app.quit)
    ai_window.show()
    ai_window.activateWindow()
    ai_window.raise_()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
