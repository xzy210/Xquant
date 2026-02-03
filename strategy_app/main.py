#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略研究应用入口

独立于主行情应用的策略研究与回测平台
"""
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from main_window import StrategyMainWindow
from styles import DARK_THEME_QSS


def main():
    """主函数"""
    # 启用高DPI支持
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    app.setApplicationName("策略研究")
    app.setApplicationVersion("1.0.0")
    
    # 设置应用样式
    app.setStyle('Fusion')
    app.setStyleSheet(DARK_THEME_QSS)
    
    # 创建主窗口
    window = StrategyMainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
