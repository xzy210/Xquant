# order_book_widget.py - 五档盘口组件
"""
显示股票的五档买卖盘数据
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

class OrderBookWidget(QWidget):
    """五档盘口组件"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUI()
        
    def setupUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)
        
        # 整体背景样式
        self.setStyleSheet("""
            QWidget {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 13px;
            }
            QLabel#title {
                color: #888;
                font-weight: bold;
                border-bottom: 1px solid #333;
                padding-bottom: 2px;
            }
        """)
        
        # 卖盘容器
        self.sell_labels = [] # List of (price_label, vol_label)
        for i in range(5, 0, -1):
            row = QHBoxLayout()
            label = QLabel(f"卖{i}")
            label.setFixedWidth(30)
            price = QLabel("--")
            price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            vol = QLabel("--")
            vol.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            vol.setFixedWidth(60)
            
            row.addWidget(label)
            row.addWidget(price)
            row.addWidget(vol)
            layout.addLayout(row)
            self.sell_labels.append((price, vol))
            
        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setStyleSheet("background-color: #333;")
        layout.addWidget(line)
        
        # 买盘容器
        self.buy_labels = [] # List of (price_label, vol_label)
        for i in range(1, 6):
            row = QHBoxLayout()
            label = QLabel(f"买{i}")
            label.setFixedWidth(30)
            price = QLabel("--")
            price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            vol = QLabel("--")
            vol.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            vol.setFixedWidth(60)
            
            row.addWidget(label)
            row.addWidget(price)
            row.addWidget(vol)
            layout.addLayout(row)
            self.buy_labels.append((price, vol))
            
        layout.addStretch()

    def update_data(self, tick_data: dict, prev_close: float = 0):
        """
        更新盘口数据
        tick_data: xtquant 返回的 tick 字典
        """
        if not tick_data:
            self.clear_data()
            return
            
        ask_prices = tick_data.get('askPrice', [0]*5)
        ask_vols = tick_data.get('askVol', [0]*5)
        bid_prices = tick_data.get('bidPrice', [0]*5)
        bid_vols = tick_data.get('bidVol', [0]*5)
        
        # 颜色配置
        up_color = "#ff4d4d"
        down_color = "#00b800"
        equal_color = "#ffffff"
        
        def get_color(price):
            if not prev_close or price == 0: return equal_color
            if price > prev_close + 0.001: return up_color
            if price < prev_close - 0.001: return down_color
            return equal_color

        # 更新卖盘 (卖5到卖1)
        for i in range(5):
            idx = 4 - i # 卖5是列表第4个，卖1是第0个
            price = ask_prices[idx]
            vol = ask_vols[idx]
            
            p_label, v_label = self.sell_labels[i]
            
            if price > 0:
                p_label.setText(f"{price:.2f}")
                p_label.setStyleSheet(f"color: {get_color(price)};")
                v_label.setText(f"{int(vol)}")
                v_label.setStyleSheet("color: #ffdd00;") # 卖盘成交量通常用黄色或白色
            else:
                p_label.setText("--")
                v_label.setText("--")

        # 更新买盘 (买1到买5)
        for i in range(5):
            price = bid_prices[i]
            vol = bid_vols[i]
            
            p_label, v_label = self.buy_labels[i]
            
            if price > 0:
                p_label.setText(f"{price:.2f}")
                p_label.setStyleSheet(f"color: {get_color(price)};")
                v_label.setText(f"{int(vol)}")
                v_label.setStyleSheet("color: #ffdd00;")
            else:
                p_label.setText("--")
                v_label.setText("--")

    def clear_data(self):
        """清除数据"""
        for p, v in self.sell_labels + self.buy_labels:
            p.setText("--")
            p.setStyleSheet("color: #d4d4d4;")
            v.setText("--")
            v.setStyleSheet("color: #d4d4d4;")

