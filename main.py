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

# 获取当前脚本所在目录 (项目根目录)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# 将项目根目录添加到系统路径
sys.path.insert(0, ROOT_DIR)
# 将 pyqt_app 目录添加到系统路径，以便可以直接导入其中的模块
sys.path.insert(0, os.path.join(ROOT_DIR, "pyqt_app"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

# 现在可以直接导入 pyqt_app 下的模块
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
    exit_code = app.exec()
    
    # 强制退出所有线程，防止残留进程
    import os
    os._exit(exit_code)


if __name__ == "__main__":
    main()

