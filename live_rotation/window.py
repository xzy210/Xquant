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
from .trade_executor import XtQuantExecutor

DARK_THEME = """
QMainWindow, QWidget {
    background-color: #1e1e1e;
    color: #ffffff;
}
QGroupBox {
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}
QTabWidget::pane {
    border: 1px solid #3c3c3c;
    background-color: #1e1e1e;
}
QTabBar::tab {
    background-color: #2d2d2d;
    color: #b0b0b0;
    padding: 8px 20px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #1e1e1e;
    color: #ffffff;
    border-bottom: 2px solid #0078d4;
}
QTabBar::tab:hover {
    background-color: #3c3c3c;
    color: #ffffff;
}
QTableWidget {
    background-color: #1e1e1e;
    color: #ffffff;
    gridline-color: #3c3c3c;
    border: 1px solid #3c3c3c;
}
QHeaderView::section {
    background-color: #2d2d2d;
    color: #ffffff;
    padding: 5px;
    border: 1px solid #3c3c3c;
}
QTableWidget::item:selected {
    background-color: #0078d4;
}
QSplitter::handle {
    background-color: #3c3c3c;
}
QSplitter::handle:horizontal {
    width: 2px;
}
QSpinBox, QDoubleSpinBox {
    background-color: #2d2d2d;
    color: #ffffff;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    padding: 4px 8px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background-color: #3c3c3c;
    border: none;
    width: 16px;
}
QLineEdit {
    background-color: #2d2d2d;
    color: #ffffff;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    padding: 4px 8px;
}
QCheckBox {
    color: #ffffff;
    spacing: 5px;
}
QScrollBar:vertical {
    background: #1e1e1e;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: #3c3c3c;
    border-radius: 5px;
    min-height: 20px;
}
"""


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

        self.setStyleSheet(DARK_THEME)

        self._setup_menubar()

    def _setup_menubar(self):
        menubar = self.menuBar()
        menubar.setStyleSheet(
            "QMenuBar{background:#2d2d2d;color:#fff;padding:2px;}"
            "QMenuBar::item:selected{background:#3c3c3c;}"
            "QMenu{background:#2d2d2d;color:#fff;border:1px solid #3c3c3c;}"
            "QMenu::item:selected{background:#0078d4;}"
        )

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
    app.setStyleSheet(DARK_THEME)
    win = ETFRotationLiveWindow()
    win.show()
    sys.exit(app.exec())
