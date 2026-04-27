"""
ETF轮动实盘 - 独立窗口

可以从 trading_app 菜单打开，也可以独立运行。
"""
import sys
from pathlib import Path

from PyQt6.QtWidgets import QMainWindow, QApplication, QMessageBox
from PyQt6.QtCore import Qt

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from .widget import ETFRotationLiveWidget
from .rotation_engine import RotationEngine
from common.ui.themes import DARK_THEME_QSS as DARK_THEME, LIGHT_THEME


class ETFRotationLiveWindow(QMainWindow):
    """ETF轮动实盘独立窗口"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ETF轮动实盘")
        self.resize(1100, 700)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.engine = RotationEngine()
        self.widget = ETFRotationLiveWidget(engine=self.engine, parent=self)
        self.setCentralWidget(self.widget)

        self.setStyleSheet(LIGHT_THEME)

        self._setup_menubar()

    def _setup_menubar(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")

        from PyQt6.QtGui import QAction
        close_action = QAction("关闭(&X)", self)
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

    def inject_broker(self, xt_trader, acc):
        """注入券商连接（供 trading_app 调用）"""
        self.widget.inject_broker(xt_trader, acc)


if __name__ == "__main__":
    # 直接运行请使用项目根目录的启动脚本:
    #   python run_rotation.py
    # 或者以模块方式运行:
    #   python -m live_rotation.window
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(LIGHT_THEME)
    win = ETFRotationLiveWindow()
    win.show()
    sys.exit(app.exec())
