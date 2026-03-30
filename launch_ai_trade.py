#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""独立启动 AI 交易决策中心。

运行方式:
    python launch_ai_trade.py
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "trading_app"))

from trading_app.main_window import MainWindow
from trading_app.styles import DARK_THEME


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName("来财 - AI交易决策中心")
    app.setOrganizationName("StockTradebyZ")
    app.setApplicationVersion("1.0.0")
    app.setFont(QFont("Microsoft YaHei", 9))
    app.setStyleSheet(DARK_THEME)

    # 初始化主程序能力，但不显示主窗口。
    main_window = MainWindow()
    main_window.hide()
    app.lastWindowClosed.connect(main_window.close)
    app.lastWindowClosed.connect(app.quit)

    # 延迟到事件循环启动后再打开独立窗口，避免初始化顺序问题。
    def open_ai_center():
        main_window.open_ai_trade_decision_center()
        ai_window = getattr(main_window, "_ai_trade_window", None)
        if ai_window is None:
            app.quit()
            return
        ai_window.activateWindow()
        ai_window.raise_()

    QTimer.singleShot(0, open_ai_center)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
