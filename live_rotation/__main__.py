"""允许 python -m live_rotation 直接启动"""
import sys
from PyQt6.QtWidgets import QApplication

from .window import ETFRotationLiveWindow, DARK_THEME

app = QApplication(sys.argv)
app.setApplicationName("ETF轮动实盘")
app.setStyle('Fusion')
app.setStyleSheet(DARK_THEME)

win = ETFRotationLiveWindow()
win.show()

sys.exit(app.exec())
