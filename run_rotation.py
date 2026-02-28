#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF轮动实盘 - 启动入口

用法:
    python run_rotation.py
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from live_rotation.window import ETFRotationLiveWindow, LIGHT_THEME


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ETF轮动实盘")
    app.setStyle('Fusion')
    app.setStyleSheet(LIGHT_THEME)

    win = ETFRotationLiveWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
