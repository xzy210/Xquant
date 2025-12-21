import pandas as pd
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QGridLayout, QScrollArea, QComboBox, QPushButton,
    QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QFont

try:
    from data_loader import load_stock_data
    from .kline_widget import CandlestickItem
except ImportError:
    from ..data_loader import load_stock_data
    from .kline_widget import CandlestickItem

class MiniKLineWidget(pg.GraphicsLayoutWidget):
    """小型K线图组件，用于面板展示"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground('#1e1e1e')
        self.plot = self.addPlot()
        self.plot.hideAxis('left')
        self.plot.hideAxis('bottom')
        self.plot.vb.setMouseEnabled(x=False, y=False)
        self.plot.vb.setMenuEnabled(False)
        self.plot.setContentsMargins(0, 0, 0, 0)
        
    def set_data(self, df: pd.DataFrame):
        self.plot.clear()
        if df is None or df.empty:
            return
            
        # 只显示最近 40 天
        display_df = df.tail(40).copy()
        display_df.reset_index(drop=True, inplace=True)
        
        # 绘制K线
        candle_item = CandlestickItem(display_df)
        self.plot.addItem(candle_item)
        
        # 自动缩放 Y 轴
        low = display_df['low'].min()
        high = display_df['high'].max()
        padding = (high - low) * 0.1 if high != low else 0.1
        self.plot.setYRange(low - padding, high + padding, padding=0)
        self.plot.setXRange(0, len(display_df), padding=0.02)

class StockCard(QFrame):
    """单只股票的卡片显示"""
    clicked = pyqtSignal(str, str) # code, name

    def __init__(self, code, name, parent=None):
        super().__init__(parent)
        self.code = code
        self.name = name
        self.setupUI()
        
    def setupUI(self):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            StockCard {
                background-color: #252525;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
            }
            StockCard:hover {
                border-color: #0078d4;
                background-color: #2d2d2d;
            }
        """)
        self.setFixedSize(180, 130)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        
        # 第一行：名称 (左) 和 代码 (右)
        top_layout = QHBoxLayout()
        self.name_label = QLabel(self.name)
        self.name_label.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: bold;")
        self.code_label = QLabel(self.code)
        self.code_label.setStyleSheet("color: #777777; font-size: 10px;")
        top_layout.addWidget(self.name_label)
        top_layout.addStretch()
        top_layout.addWidget(self.code_label)
        layout.addLayout(top_layout)
        
        # 第二行：价格和涨跌幅 (紧凑排列)
        price_layout = QHBoxLayout()
        price_layout.setSpacing(8)
        self.price_label = QLabel("--")
        self.price_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #ec0000; font-family: Consolas;")
        self.change_label = QLabel("--%")
        self.change_label.setStyleSheet("font-size: 11px; color: #ec0000; font-weight: normal;")
        price_layout.addWidget(self.price_label)
        price_layout.addWidget(self.change_label)
        price_layout.addStretch()
        layout.addLayout(price_layout)
        
        # Mini K线
        self.mini_kline = MiniKLineWidget()
        # 增加 K 线高度比例
        self.mini_kline.setFixedHeight(75)
        layout.addWidget(self.mini_kline)
        
    def update_data(self, df: pd.DataFrame):
        if df is not None and not df.empty:
            last_row = df.iloc[-1]
            price = last_row['close']
            
            # 计算涨跌幅
            change_pct = 0
            if len(df) > 1:
                prev_close = df.iloc[-2]['close']
                change_pct = (price - prev_close) / prev_close * 100
                
            color = "#ff4d4f" if change_pct >= 0 else "#2ecc71" # 优化后的红绿色
            self.price_label.setText(f"{price:.2f}")
            self.price_label.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {color}; font-family: Consolas;")
            self.change_label.setText(f"{change_pct:+.2f}%")
            self.change_label.setStyleSheet(f"font-size: 11px; color: {color}; font-weight: normal;")
            
            self.mini_kline.set_data(df)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.code, self.name)

class WatchlistPanelWidget(QWidget):
    """面板组件 - 矩阵式展示 (跟随左侧列表，仅限分组模式)"""
    stockSelected = pyqtSignal(str, str) # code, name

    def __init__(self, watchlist_manager, name_map, data_dir, parent=None):
        super().__init__(parent)
        self.manager = watchlist_manager
        self.name_map = name_map
        self.data_dir = data_dir
        self.cards = []
        self.current_stocks = []
        self.is_group_mode = False
        self.setupUI()
        
    def setupUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 提示标签
        self.placeholder_label = QLabel("请在左侧选择一个自选分组以开启面板展示")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setStyleSheet("color: #888888; font-size: 16px; margin-top: 50px;")
        layout.addWidget(self.placeholder_label)
        self.placeholder_label.hide()

        # 滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.container = QWidget()
        self.grid_layout = QGridLayout(self.container)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        self.scroll_area.setWidget(self.container)
        layout.addWidget(self.scroll_area)
        
    def set_stocks(self, stocks: list):
        """设置要显示的股票列表，跟随左侧列表"""
        if not self.is_group_mode:
            self.current_stocks = []
            self.refresh_grid()
            self.scroll_area.hide()
            self.placeholder_label.show()
            return

        self.placeholder_label.hide()
        self.scroll_area.show()

        # 限制显示数量，避免性能问题
        MAX_DISPLAY = 100
        if len(stocks) > MAX_DISPLAY:
            self.current_stocks = stocks[:MAX_DISPLAY]
        else:
            self.current_stocks = stocks
            
        self.refresh_grid()

    def set_group_mode(self, enabled: bool):
        """设置是否为分组模式"""
        self.is_group_mode = enabled
        if not enabled:
            self.current_stocks = []
            self.refresh_grid()
            self.scroll_area.hide()
            self.placeholder_label.show()
        else:
            self.placeholder_label.hide()
            self.scroll_area.show()
        
    def refresh_grid(self):
        # 清除现有卡片
        for card in self.cards:
            self.grid_layout.removeWidget(card)
            card.deleteLater()
        self.cards = []
        
        if not self.current_stocks:
            return

        # 获取容器宽度计算列数
        container_width = self.scroll_area.width()
        if container_width < 100:
            container_width = 1000
            
        card_width = 180
        spacing = 10
        cols = max(1, (container_width - spacing) // (card_width + spacing))
        
        for i, code in enumerate(self.current_stocks):
            name = self.name_map.get(code, code)
            card = StockCard(code, name)
            card.clicked.connect(self.stockSelected)
            
            # 加载数据 (尽量从缓存获取)
            from data_loader import load_stock_data
            df = load_stock_data(code, self.data_dir)
            card.update_data(df)
            
            row = i // cols
            col = i % cols
            self.grid_layout.addWidget(card, row, col)
            self.cards.append(card)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self.reLayout)

    def reLayout(self):
        if not self.cards:
            return
        container_width = self.scroll_area.width()
        card_width = 180
        spacing = 10
        cols = max(1, (container_width - spacing) // (card_width + spacing))
        
        for i, card in enumerate(self.cards):
            self.grid_layout.removeWidget(card)
            row = i // cols
            col = i % cols
            self.grid_layout.addWidget(card, row, col)

    def update_name_map(self, name_map):
        self.name_map = name_map

    def refresh(self):
        self.refresh_grid()

    def update_groups(self):
        pass
