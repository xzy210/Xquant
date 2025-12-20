#!/usr/bin/env python
# -*- coding: utf-8 -*-
# main.py - 应用程序入口
"""
来财 - PyQt6版本

运行方式:
    python main.py
"""
import sys
import os

# 将父目录添加到路径，以便访问数据文件
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from main_window import MainWindow
from styles import DARK_THEME


def main():
    """应用程序入口"""
    # 启用高DPI支持
    # Qt6 默认启用，无需额外设置
    
    app = QApplication(sys.argv)
    
    # 设置应用程序信息
    app.setApplicationName("来财")
    app.setOrganizationName("StockTradebyZ")
    app.setApplicationVersion("1.0.0")
    
    # 设置默认字体
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    
    # 应用深色主题
    app.setStyleSheet(DARK_THEME)
    
    # 创建并显示主窗口
    window = MainWindow()
    window.show()
    
    # 运行应用程序
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
