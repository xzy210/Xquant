# etf_grid_widget.py - ETF Grid Trading Widget
"""
ETF Grid Trading Strategy UI Widget

Features:
- Parameter configuration panel
- Grid visualization
- Real-time signal monitoring  
- Backtest with performance charts
- Trade history display
- Candlestick chart with trade signals visualization
"""

import sys
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QGroupBox, QFormLayout, QSplitter, QFrame, QProgressBar,
    QCheckBox, QScrollArea, QSizePolicy, QGridLayout,
    QTextEdit, QSlider, QDateEdit, QLineEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QDate
from PyQt6.QtGui import QColor, QBrush, QFont, QPainter, QPen, QPicture

# Import pyqtgraph for charting
import pyqtgraph as pg
from pyqtgraph import PlotWidget, InfiniteLine, GraphicsObject

# Import strategy
sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies.etf_grid_strategy import (
    ETFGridStrategy, GridConfig, GridType, SignalType,
    create_default_etf_config
)
from data_loader import load_etf_data, load_etf_name_map, get_etf_list

# Import xtquant for fetching real-time minute data
try:
    from xtquant import xtdata
    HAS_XTQUANT = True
except ImportError:
    HAS_XTQUANT = False
    xtdata = None

# Import fetch_etf_kline from fetch_kline_xtquant
try:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from fetch_kline_xtquant import fetch_etf_kline, check_connection
except ImportError:
    fetch_etf_kline = None
    check_connection = None


class CandlestickItem(GraphicsObject):
    """Custom candlestick chart item for pyqtgraph"""
    
    def __init__(self, data):
        GraphicsObject.__init__(self)
        self.data = data  # List of (index, open, high, low, close)
        self.picture = None
        self.generatePicture()
    
    def generatePicture(self):
        """Generate the picture for painting"""
        self.picture = QPicture()
        painter = QPainter(self.picture)
        painter.setPen(pg.mkPen('w'))
        
        w = 0.3  # Width of candle body
        
        for item in self.data:
            idx, open_price, high, low, close = item
            
            # Determine color - Chinese market: red for up, green for down
            if close >= open_price:
                color = QColor('#ec0000')  # Red for up
            else:
                color = QColor('#00da3c')  # Green for down
            
            painter.setPen(pg.mkPen(color))
            painter.setBrush(QBrush(color))
            
            # Draw the wick (high-low line)
            painter.drawLine(
                pg.QtCore.QPointF(idx, low),
                pg.QtCore.QPointF(idx, high)
            )
            
            # Draw the body
            if close >= open_price:
                # Bullish - draw hollow or filled
                painter.drawRect(
                    pg.QtCore.QRectF(idx - w, open_price, w * 2, close - open_price)
                )
            else:
                # Bearish
                painter.drawRect(
                    pg.QtCore.QRectF(idx - w, close, w * 2, open_price - close)
                )
        
        painter.end()
    
    def paint(self, painter, *args):
        if self.picture:
            self.picture.play(painter)
    
    def boundingRect(self):
        if self.picture:
            return pg.QtCore.QRectF(self.picture.boundingRect())
        return pg.QtCore.QRectF()
    
    def setData(self, data):
        """Update data and redraw"""
        self.data = data
        self.generatePicture()
        self.informViewBoundsChanged()
        self.update()


class TimeLineChartWidget(QWidget):
    """Time-line chart widget with trade signals and grid visualization (using line chart instead of candlestick)"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.price_data = []  # List of (time_str, close_price)
        self.trade_history = []
        self.grid_prices = []
        self.base_price = 0
        self.current_trade_index = -1  # Current trade index for playback
        self.time_to_index = {}  # Map time string to index
        
        # Items that need to be updated during playback
        self.buy_scatter = None
        self.sell_scatter = None
        self.grid_line_items = []  # Grid line items
        self.base_line_item = None
        self.current_pos_line = None  # Vertical line for current position
        self.price_line = None  # Price line item
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Create plot widget
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#1e1e1e')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('left', '价格')
        self.plot_widget.setLabel('bottom', '时间')
        
        # Enable mouse interaction
        self.plot_widget.setMouseEnabled(x=True, y=True)
        self.plot_widget.enableAutoRange()
        
        # Add crosshair
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#666666', width=1))
        self.hLine = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('#666666', width=1))
        self.plot_widget.addItem(self.vLine, ignoreBounds=True)
        self.plot_widget.addItem(self.hLine, ignoreBounds=True)
        
        # Connect mouse movement
        self.plot_widget.scene().sigMouseMoved.connect(self.mouseMoved)
        
        layout.addWidget(self.plot_widget)
        
        # Info label for crosshair
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                background-color: #2d2d2d;
                padding: 5px;
                font-family: Consolas, monospace;
                font-size: 11px;
            }
        """)
        layout.addWidget(self.info_label)
        
        # Connect view range change for auto Y-axis adjustment
        self.plot_widget.sigXRangeChanged.connect(self.on_x_range_changed)
    
    def mouseMoved(self, pos):
        """Handle mouse movement for crosshair"""
        if self.plot_widget.sceneBoundingRect().contains(pos):
            mousePoint = self.plot_widget.plotItem.vb.mapSceneToView(pos)
            self.vLine.setPos(mousePoint.x())
            self.hLine.setPos(mousePoint.y())
            
            # Update info label
            idx = int(round(mousePoint.x()))
            if 0 <= idx < len(self.price_data):
                time_str, close = self.price_data[idx]
                self.info_label.setText(
                    f"时间: {time_str}  |  价格: {close:.3f}  |  当前: {mousePoint.y():.3f}"
                )
    
    def set_data(self, ohlc_data: pd.DataFrame, trade_history: List[Dict], 
                 grid_prices: List[float], base_price: float):
        """
        Set chart data - draws time-line chart
        
        Args:
            ohlc_data: DataFrame with columns [time/date, open, high, low, close]
            trade_history: List of trade records
            grid_prices: List of grid price levels
            base_price: Base/center price of grid
        """
        # Filter only buy/sell trades
        self.trade_history = [t for t in trade_history if t.get('type') in ['buy', 'sell']]
        self.grid_prices = sorted(grid_prices)
        self.base_price = base_price
        self.current_trade_index = -1
        
        # Convert OHLC data to price list format
        self.price_data = []
        self.time_to_index = {}
        
        # Determine time column (could be 'time' for minute data or 'date' for daily)
        time_col = 'time' if 'time' in ohlc_data.columns else 'date'
        
        for i, row in ohlc_data.iterrows():
            time_val = row[time_col]
            if hasattr(time_val, 'strftime'):
                if time_col == 'time':
                    time_str = time_val.strftime('%Y-%m-%d %H:%M')
                else:
                    time_str = time_val.strftime('%Y-%m-%d')
            else:
                time_str = str(time_val)
            
            self.time_to_index[time_str] = len(self.price_data)
            self.price_data.append((
                time_str,
                float(row['close'])
            ))
        
        # Draw time-line chart
        self.draw_time_line()
        
        # Initialize view at the first trade position
        if self.trade_history:
            self.show_trade_at_index(0)
    
    def draw_time_line(self):
        """Draw time-line chart (called once when data is set)"""
        # Clear everything
        self.plot_widget.clear()
        
        # Re-add crosshair lines
        self.plot_widget.addItem(self.vLine, ignoreBounds=True)
        self.plot_widget.addItem(self.hLine, ignoreBounds=True)
        
        if not self.price_data:
            return
        
        # Prepare price data
        x_vals = list(range(len(self.price_data)))
        y_vals = [p[1] for p in self.price_data]
        
        # Draw price line (blue line)
        self.price_line = self.plot_widget.plot(
            x_vals, y_vals,
            pen=pg.mkPen(color='#00bfff', width=1.5),
            name='价格'
        )
        
        # Set initial X range to show last 120 bars for minute data
        total_bars = len(self.price_data)
        self.plot_widget.setXRange(max(0, total_bars - 120), total_bars + 5)
    
    def on_x_range_changed(self, view_box):
        """Auto adjust Y-axis to fit visible data range"""
        if not self.price_data:
            return
        
        # Get current visible X range
        x_range = self.plot_widget.viewRange()[0]
        x_min = max(0, int(x_range[0]))
        x_max = min(len(self.price_data), int(x_range[1]) + 1)
        
        if x_min >= x_max:
            return
        
        # Find min/max prices in visible range
        visible_prices = [self.price_data[i][1] for i in range(x_min, x_max)]
        
        if not visible_prices:
            return
        
        visible_low = min(visible_prices)
        visible_high = max(visible_prices)
        
        # Add margin
        price_range = visible_high - visible_low
        margin = price_range * 0.1 if price_range > 0 else 0.01
        
        # Update Y range
        self.plot_widget.setYRange(visible_low - margin, visible_high + margin, padding=0)
    
    def update_signals_and_grid(self, up_to_trade_index: int):
        """Update only buy/sell signals and grid lines (called during playback)"""
        # Remove old signal items
        if self.buy_scatter is not None:
            self.plot_widget.removeItem(self.buy_scatter)
            self.buy_scatter = None
        if self.sell_scatter is not None:
            self.plot_widget.removeItem(self.sell_scatter)
            self.sell_scatter = None
        
        # Remove old grid lines
        for line in self.grid_line_items:
            self.plot_widget.removeItem(line)
        self.grid_line_items.clear()
        
        # Remove old base line
        if self.base_line_item is not None:
            self.plot_widget.removeItem(self.base_line_item)
            self.base_line_item = None
        
        # Remove old position line
        if self.current_pos_line is not None:
            self.plot_widget.removeItem(self.current_pos_line)
            self.current_pos_line = None
        
        # Get grid prices and base price from current trade record
        current_grid_prices = self.grid_prices
        current_base_price = self.base_price
        
        if 0 <= up_to_trade_index < len(self.trade_history):
            trade = self.trade_history[up_to_trade_index]
            # Use grid data from trade record if available
            if 'grid_prices' in trade:
                current_grid_prices = trade['grid_prices']
            if 'base_price' in trade:
                current_base_price = trade['base_price']
        
        # Get current visible Y range for grid line filtering
        y_range = self.plot_widget.viewRange()[1]
        y_min, y_max = y_range[0], y_range[1]
        
        # Draw grid lines within visible range
        for price in current_grid_prices:
            if y_min <= price <= y_max:
                if price > current_base_price:
                    color = '#00da3c80'  # Green with alpha for sell levels
                elif price < current_base_price:
                    color = '#ec000080'  # Red with alpha for buy levels
                else:
                    color = '#ffcc00'  # Yellow for base level
                
                line = pg.InfiniteLine(
                    pos=price, 
                    angle=0, 
                    pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine)
                )
                self.plot_widget.addItem(line)
                self.grid_line_items.append(line)
        
        # Draw base price line
        if current_base_price > 0 and y_min <= current_base_price <= y_max:
            self.base_line_item = pg.InfiniteLine(
                pos=current_base_price,
                angle=0,
                pen=pg.mkPen('#ffcc00', width=2)
            )
            self.plot_widget.addItem(self.base_line_item)
        
        # Collect trade signals up to current trade index
        buy_points = []
        sell_points = []
        
        for i, trade in enumerate(self.trade_history):
            if i > up_to_trade_index:
                break
            
            trade_date = trade.get('date', '')
            if trade_date in self.time_to_index:
                trade_idx = self.time_to_index[trade_date]
                trade_type = trade.get('type', '')
                trade_price = trade.get('price', 0)
                
                if trade_type == 'buy':
                    buy_points.append((trade_idx, trade_price))
                elif trade_type == 'sell':
                    sell_points.append((trade_idx, trade_price))
        
        # Draw buy signals (red up arrows)
        if buy_points:
            buy_x = [p[0] for p in buy_points]
            buy_y = [p[1] for p in buy_points]
            self.buy_scatter = pg.ScatterPlotItem(
                x=buy_x, y=buy_y,
                symbol='t1',  # Up triangle
                size=15,
                pen=pg.mkPen('#ec0000', width=2),
                brush=pg.mkBrush('#ec0000')
            )
            self.plot_widget.addItem(self.buy_scatter)
        
        # Draw sell signals (green down arrows)
        if sell_points:
            sell_x = [p[0] for p in sell_points]
            sell_y = [p[1] for p in sell_points]
            self.sell_scatter = pg.ScatterPlotItem(
                x=sell_x, y=sell_y,
                symbol='t',  # Down triangle
                size=15,
                pen=pg.mkPen('#00da3c', width=2),
                brush=pg.mkBrush('#00da3c')
            )
            self.plot_widget.addItem(self.sell_scatter)
        
        # Draw vertical line at current trade position
        if 0 <= up_to_trade_index < len(self.trade_history):
            current_trade = self.trade_history[up_to_trade_index]
            trade_date = current_trade.get('date', '')
            if trade_date in self.time_to_index:
                pos_x = self.time_to_index[trade_date]
                self.current_pos_line = pg.InfiniteLine(
                    pos=pos_x,
                    angle=90,
                    pen=pg.mkPen('#ffaa00', width=2, style=Qt.PenStyle.DashLine)
                )
                self.plot_widget.addItem(self.current_pos_line)
    
    def show_trade_at_index(self, trade_index: int):
        """Show chart state at a specific trade index - only updates signals and grid"""
        if trade_index < 0 or trade_index >= len(self.trade_history):
            return
        
        self.current_trade_index = trade_index
        
        # Update only signals and grid lines, keep K-line chart view unchanged
        self.update_signals_and_grid(trade_index)


class TradePlaybackWidget(QWidget):
    """Widget for playing back trades on K-line chart"""
    
    trade_index_changed = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.trade_history = []
        self.current_trade_index = 0
        self.is_playing = False
        self.play_timer = QTimer()
        self.play_timer.timeout.connect(self.next_trade)
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Playback controls
        controls_layout = QHBoxLayout()
        
        self.first_btn = QPushButton("⏮")
        self.first_btn.setFixedWidth(40)
        self.first_btn.clicked.connect(self.go_first)
        self.first_btn.setToolTip("跳到第一笔交易")
        controls_layout.addWidget(self.first_btn)
        
        self.prev_btn = QPushButton("◀")
        self.prev_btn.setFixedWidth(40)
        self.prev_btn.clicked.connect(self.prev_trade)
        self.prev_btn.setToolTip("上一笔交易")
        controls_layout.addWidget(self.prev_btn)
        
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(40)
        self.play_btn.clicked.connect(self.toggle_play)
        self.play_btn.setToolTip("播放/暂停")
        controls_layout.addWidget(self.play_btn)
        
        self.next_btn = QPushButton("▶")
        self.next_btn.setFixedWidth(40)
        self.next_btn.clicked.connect(self.next_trade)
        self.next_btn.setToolTip("下一笔交易")
        controls_layout.addWidget(self.next_btn)
        
        self.last_btn = QPushButton("⏭")
        self.last_btn.setFixedWidth(40)
        self.last_btn.clicked.connect(self.go_last)
        self.last_btn.setToolTip("跳到最后一笔交易")
        controls_layout.addWidget(self.last_btn)
        
        controls_layout.addSpacing(20)
        
        # Speed control
        speed_label = QLabel("播放速度:")
        speed_label.setStyleSheet("color: #888888;")
        controls_layout.addWidget(speed_label)
        
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "1x", "2x", "4x"])
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.currentIndexChanged.connect(self.update_speed)
        controls_layout.addWidget(self.speed_combo)
        
        controls_layout.addStretch()
        
        # Trade counter
        self.trade_counter = QLabel("交易: 0/0")
        self.trade_counter.setStyleSheet("color: #ffffff; font-weight: bold;")
        controls_layout.addWidget(self.trade_counter)
        
        layout.addLayout(controls_layout)
        
        # Slider for seeking
        slider_layout = QHBoxLayout()
        
        self.trade_slider = QSlider(Qt.Orientation.Horizontal)
        self.trade_slider.setMinimum(0)
        self.trade_slider.setMaximum(0)
        self.trade_slider.valueChanged.connect(self.on_slider_changed)
        slider_layout.addWidget(self.trade_slider)
        
        layout.addLayout(slider_layout)
        
        # Current trade info
        self.trade_info = QLabel("")
        self.trade_info.setStyleSheet("""
            QLabel {
                color: #ffffff;
                background-color: #2d2d2d;
                padding: 8px;
                border-radius: 4px;
                font-family: Consolas, monospace;
            }
        """)
        self.trade_info.setWordWrap(True)
        layout.addWidget(self.trade_info)
        
        # Apply button styles
        btn_style = """
            QPushButton {
                background-color: #3c3c3c;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #4c4c4c;
            }
            QPushButton:pressed {
                background-color: #2c2c2c;
            }
        """
        for btn in [self.first_btn, self.prev_btn, self.play_btn, self.next_btn, self.last_btn]:
            btn.setStyleSheet(btn_style)
    
    def set_trade_history(self, trade_history: List[Dict]):
        """Set trade history for playback"""
        # Filter only buy/sell trades
        self.trade_history = [t for t in trade_history if t.get('type') in ['buy', 'sell']]
        self.current_trade_index = 0
        
        self.trade_slider.setMaximum(max(0, len(self.trade_history) - 1))
        self.trade_slider.setValue(0)
        
        self.update_trade_info()
        self.update_counter()
    
    def update_counter(self):
        """Update trade counter display"""
        total = len(self.trade_history)
        current = self.current_trade_index + 1 if total > 0 else 0
        self.trade_counter.setText(f"交易: {current}/{total}")
    
    def update_trade_info(self):
        """Update trade info display"""
        if not self.trade_history or self.current_trade_index < 0:
            self.trade_info.setText("无交易记录")
            return
        
        if self.current_trade_index >= len(self.trade_history):
            self.current_trade_index = len(self.trade_history) - 1
        
        trade = self.trade_history[self.current_trade_index]
        
        trade_type = trade.get('type', '')
        type_color = '#ec0000' if trade_type == 'buy' else '#00da3c'
        type_text = '买入' if trade_type == 'buy' else '卖出'
        
        info = f"""
<span style="color: {type_color}; font-weight: bold; font-size: 14px;">【{type_text}】</span><br>
<b>日期:</b> {trade.get('date', 'N/A')}<br>
<b>价格:</b> {trade.get('price', 0):.3f}<br>
<b>数量:</b> {trade.get('quantity', 0)} 股<br>
<b>金额:</b> ¥{trade.get('amount', 0):,.2f}<br>
<b>网格:</b> Level {trade.get('grid_level', 0)}<br>
<b>原因:</b> {trade.get('reason', 'N/A')}<br>
<b>持仓:</b> {trade.get('position_after', 0)} 股 | <b>现金:</b> ¥{trade.get('cash_after', 0):,.2f}
"""
        self.trade_info.setText(info)
    
    def go_first(self):
        """Go to first trade"""
        if self.trade_history:
            self.current_trade_index = 0
            self.trade_slider.setValue(0)
            self.update_trade_info()
            self.update_counter()
            self.trade_index_changed.emit(self.current_trade_index)
    
    def go_last(self):
        """Go to last trade"""
        if self.trade_history:
            self.current_trade_index = len(self.trade_history) - 1
            self.trade_slider.setValue(self.current_trade_index)
            self.update_trade_info()
            self.update_counter()
            self.trade_index_changed.emit(self.current_trade_index)
    
    def prev_trade(self):
        """Go to previous trade"""
        if self.trade_history and self.current_trade_index > 0:
            self.current_trade_index -= 1
            self.trade_slider.setValue(self.current_trade_index)
            self.update_trade_info()
            self.update_counter()
            self.trade_index_changed.emit(self.current_trade_index)
    
    def next_trade(self):
        """Go to next trade"""
        if self.trade_history and self.current_trade_index < len(self.trade_history) - 1:
            self.current_trade_index += 1
            self.trade_slider.setValue(self.current_trade_index)
            self.update_trade_info()
            self.update_counter()
            self.trade_index_changed.emit(self.current_trade_index)
        else:
            # Stop playing when reaching the end
            if self.is_playing:
                self.toggle_play()
    
    def toggle_play(self):
        """Toggle auto-play"""
        self.is_playing = not self.is_playing
        
        if self.is_playing:
            self.play_btn.setText("⏸")
            self.update_speed()
            self.play_timer.start()
        else:
            self.play_btn.setText("▶")
            self.play_timer.stop()
    
    def update_speed(self):
        """Update playback speed"""
        speed_map = {0: 2000, 1: 1000, 2: 500, 3: 250}  # ms
        interval = speed_map.get(self.speed_combo.currentIndex(), 1000)
        self.play_timer.setInterval(interval)
    
    def on_slider_changed(self, value):
        """Handle slider value change"""
        if value != self.current_trade_index and self.trade_history:
            self.current_trade_index = value
            self.update_trade_info()
            self.update_counter()
            self.trade_index_changed.emit(self.current_trade_index)


class BacktestThread(QThread):
    """Thread for running backtest"""
    progress = pyqtSignal(int, int)  # current, total
    finished = pyqtSignal(dict)  # results
    error = pyqtSignal(str)  # error message
    
    def __init__(self, strategy: ETFGridStrategy, data: pd.DataFrame):
        super().__init__()
        self.strategy = strategy
        self.data = data
    
    def run(self):
        try:
            results = self.strategy.backtest(
                self.data,
                progress_callback=lambda c, t: self.progress.emit(c, t)
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class GridVisualizationWidget(QWidget):
    """Widget for visualizing grid levels"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.grids = []
        self.current_price = 0
        self.base_price = 0
        self.setMinimumHeight(200)
    
    def set_data(self, grids: List, current_price: float, base_price: float):
        """Set grid data for visualization"""
        self.grids = grids
        self.current_price = current_price
        self.base_price = base_price
        self.update()
    
    def paintEvent(self, event):
        """Custom paint for grid visualization"""
        if not self.grids:
            return
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        margin = 40
        
        # Calculate price range
        prices = [g.price for g in self.grids]
        if not prices:
            return
        
        min_price = min(prices)
        max_price = max(prices)
        price_range = max_price - min_price
        
        if price_range <= 0:
            return
        
        # Draw background
        painter.fillRect(0, 0, width, height, QColor('#1e1e1e'))
        
        # Draw grid lines and labels
        for grid in self.grids:
            y = margin + (max_price - grid.price) / price_range * (height - 2 * margin)
            
            # Grid line color based on level
            if grid.level > 0:
                color = QColor('#00da3c')  # Green for sell levels
            elif grid.level < 0:
                color = QColor('#ec0000')  # Red for buy levels
            else:
                color = QColor('#ffcc00')  # Yellow for base level
            
            # Draw dashed line
            pen = QPen(color)
            pen.setWidth(1)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(margin, int(y), width - margin, int(y))
            
            # Draw price label
            painter.setPen(QColor('#ffffff'))
            painter.drawText(5, int(y + 4), f"{grid.price:.3f}")
            
            # Draw level indicator
            if grid.is_triggered:
                painter.setBrush(QBrush(color))
                painter.drawEllipse(width - margin + 5, int(y - 4), 8, 8)
        
        # Draw current price line
        if min_price <= self.current_price <= max_price:
            y = margin + (max_price - self.current_price) / price_range * (height - 2 * margin)
            pen = QPen(QColor('#00bfff'))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(margin, int(y), width - margin, int(y))
            
            # Price label
            painter.setPen(QColor('#00bfff'))
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(width - margin + 5, int(y + 4), f"现价: {self.current_price:.3f}")


class ETFGridWidget(QWidget):
    """Main ETF Grid Trading Strategy Widget"""
    
    def __init__(self, data_dir: str = "../data", parent=None):
        super().__init__(parent)
        self.data_dir = data_dir
        self.strategy: Optional[ETFGridStrategy] = None
        self.current_data: Optional[pd.DataFrame] = None
        self.etf_name_map = {}
        self.backtest_thread: Optional[BacktestThread] = None
        self.backtest_results: Optional[Dict] = None  # Store backtest results
        
        self.setup_ui()
        self.load_etf_list()
    
    def setup_ui(self):
        """Initialize UI"""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # Left panel: Configuration and controls (with scroll support)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setFixedWidth(340)  # Slightly wider to accommodate scrollbar
        left_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background-color: transparent;
            }
        """)
        
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 5, 0)  # Right margin for scrollbar
        left_layout.setSpacing(10)
        
        # ETF Selection
        etf_group = QGroupBox("ETF选择")
        etf_layout = QFormLayout(etf_group)
        
        # Search input for filtering ETF list
        self.etf_search_input = QLineEdit()
        self.etf_search_input.setPlaceholderText("输入代码或名称搜索...")
        self.etf_search_input.textChanged.connect(self.filter_etf_list)
        etf_layout.addRow("搜索:", self.etf_search_input)
        
        self.etf_combo = QComboBox()
        self.etf_combo.setMinimumWidth(180)
        self.etf_combo.currentIndexChanged.connect(self.on_etf_changed)
        etf_layout.addRow("ETF:", self.etf_combo)
        
        self.etf_type_combo = QComboBox()
        self.etf_type_combo.addItems(["宽基指数", "行业主题", "商品", "债券"])
        self.etf_type_combo.currentIndexChanged.connect(self.on_etf_type_changed)
        etf_layout.addRow("类型:", self.etf_type_combo)
        
        left_layout.addWidget(etf_group)
        
        # Data Settings
        data_group = QGroupBox("数据设置")
        data_layout = QFormLayout(data_group)
        
        # Period selection (1m, 5m)
        self.period_combo = QComboBox()
        self.period_combo.addItems(["1分钟", "5分钟"])
        self.period_combo.setToolTip("分时数据周期")
        data_layout.addRow("分时周期:", self.period_combo)
        
        # Date range selection
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addDays(-30))
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        data_layout.addRow("开始日期:", self.start_date_edit)
        
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        data_layout.addRow("结束日期:", self.end_date_edit)
        
        # Load data button
        self.load_data_btn = QPushButton("加载分时数据")
        self.load_data_btn.clicked.connect(self.load_minute_data)
        self.load_data_btn.setStyleSheet("""
            QPushButton {
                background-color: #107c10;
                color: white;
                padding: 5px 10px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #128c12;
            }
            QPushButton:disabled {
                background-color: #555555;
            }
        """)
        data_layout.addRow(self.load_data_btn)
        
        # Data status label
        self.data_status_label = QLabel("未加载数据")
        self.data_status_label.setStyleSheet("color: #888888; font-size: 11px;")
        data_layout.addRow(self.data_status_label)
        
        left_layout.addWidget(data_group)
        
        # Strategy Parameters
        params_group = QGroupBox("策略参数")
        params_layout = QFormLayout(params_group)
        
        self.capital_spin = QDoubleSpinBox()
        self.capital_spin.setRange(10000, 10000000)
        self.capital_spin.setValue(100000)
        self.capital_spin.setSingleStep(10000)
        self.capital_spin.setPrefix("¥")
        params_layout.addRow("初始资金:", self.capital_spin)
        
        self.grid_count_spin = QSpinBox()
        self.grid_count_spin.setRange(3, 30)
        self.grid_count_spin.setValue(10)
        self.grid_count_spin.setToolTip("每侧网格数量")
        params_layout.addRow("网格数量:", self.grid_count_spin)
        
        self.grid_spacing_spin = QDoubleSpinBox()
        self.grid_spacing_spin.setRange(0.5, 10)
        self.grid_spacing_spin.setValue(2.0)
        self.grid_spacing_spin.setSingleStep(0.5)
        self.grid_spacing_spin.setSuffix("%")
        self.grid_spacing_spin.setToolTip("网格间距百分比")
        params_layout.addRow("网格间距:", self.grid_spacing_spin)
        
        self.grid_type_combo = QComboBox()
        self.grid_type_combo.addItems(["等比网格", "等差网格"])
        params_layout.addRow("网格类型:", self.grid_type_combo)
        
        self.position_ratio_spin = QDoubleSpinBox()
        self.position_ratio_spin.setRange(1, 30)
        self.position_ratio_spin.setValue(10)
        self.position_ratio_spin.setSuffix("%")
        self.position_ratio_spin.setToolTip("每格仓位比例")
        params_layout.addRow("每格仓位:", self.position_ratio_spin)
        
        self.max_position_spin = QDoubleSpinBox()
        self.max_position_spin.setRange(10, 100)
        self.max_position_spin.setValue(80)
        self.max_position_spin.setSuffix("%")
        self.max_position_spin.setToolTip("最大持仓比例")
        params_layout.addRow("最大仓位:", self.max_position_spin)
        
        left_layout.addWidget(params_group)
        
        # ATR Adaptive Settings
        atr_group = QGroupBox("ATR自适应")
        atr_layout = QFormLayout(atr_group)
        
        self.use_atr_check = QCheckBox("启用ATR自适应网格")
        self.use_atr_check.setChecked(True)
        self.use_atr_check.toggled.connect(self.on_atr_toggle)
        atr_layout.addRow(self.use_atr_check)
        
        self.atr_period_spin = QSpinBox()
        self.atr_period_spin.setRange(5, 50)
        self.atr_period_spin.setValue(14)
        atr_layout.addRow("ATR周期:", self.atr_period_spin)
        
        self.atr_mult_spin = QDoubleSpinBox()
        self.atr_mult_spin.setRange(0.5, 5.0)
        self.atr_mult_spin.setValue(1.5)
        self.atr_mult_spin.setSingleStep(0.1)
        atr_layout.addRow("ATR倍数:", self.atr_mult_spin)
        
        left_layout.addWidget(atr_group)
        
        # Risk Control
        risk_group = QGroupBox("风险控制")
        risk_layout = QFormLayout(risk_group)
        
        self.stop_loss_spin = QDoubleSpinBox()
        self.stop_loss_spin.setRange(1, 50)
        self.stop_loss_spin.setValue(15)
        self.stop_loss_spin.setSuffix("%")
        risk_layout.addRow("止损比例:", self.stop_loss_spin)
        
        self.take_profit_spin = QDoubleSpinBox()
        self.take_profit_spin.setRange(5, 100)
        self.take_profit_spin.setValue(30)
        self.take_profit_spin.setSuffix("%")
        risk_layout.addRow("止盈比例:", self.take_profit_spin)
        
        self.rebalance_spin = QDoubleSpinBox()
        self.rebalance_spin.setRange(5, 50)
        self.rebalance_spin.setValue(10)
        self.rebalance_spin.setSuffix("%")
        self.rebalance_spin.setToolTip("价格偏离基准超过此比例时重置网格")
        risk_layout.addRow("重置阈值:", self.rebalance_spin)
        
        left_layout.addWidget(risk_group)
        
        # Action Buttons
        btn_layout = QHBoxLayout()
        
        self.run_btn = QPushButton("运行回测")
        self.run_btn.clicked.connect(self.run_backtest)
        self.run_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1084d8;
            }
            QPushButton:disabled {
                background-color: #555555;
            }
        """)
        btn_layout.addWidget(self.run_btn)
        
        self.reset_btn = QPushButton("重置参数")
        self.reset_btn.clicked.connect(self.reset_params)
        btn_layout.addWidget(self.reset_btn)
        
        left_layout.addLayout(btn_layout)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)
        
        left_layout.addStretch()
        
        # Set left panel as scroll area content
        left_scroll.setWidget(left_panel)
        main_layout.addWidget(left_scroll)
        
        # Right panel: Results and visualization
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # Result tabs
        self.result_tabs = QTabWidget()
        
        # Tab 1: Summary
        summary_tab = QWidget()
        summary_layout = QVBoxLayout(summary_tab)
        
        # Stats display
        self.stats_widget = QWidget()
        stats_layout = QGridLayout(self.stats_widget)
        stats_layout.setSpacing(15)
        
        self.stat_labels = {}
        stat_items = [
            ('total_return', '总收益率', '%'),
            ('total_profit', '总盈亏', '¥'),
            ('realized_profit', '已实现盈亏', '¥'),
            ('unrealized_profit', '未实现盈亏', '¥'),
            ('max_drawdown', '最大回撤', '%'),
            ('win_rate', '胜率', '%'),
            ('total_trades', '交易次数', ''),
            ('position_ratio', '当前仓位', '%'),
        ]
        
        for i, (key, name, unit) in enumerate(stat_items):
            row, col = i // 4, (i % 4) * 2
            name_label = QLabel(f"{name}:")
            name_label.setStyleSheet("color: #888888; font-size: 12px;")
            stats_layout.addWidget(name_label, row, col)
            
            value_label = QLabel("--")
            value_label.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
            stats_layout.addWidget(value_label, row, col + 1)
            self.stat_labels[key] = (value_label, unit)
        
        summary_layout.addWidget(self.stats_widget)
        
        # Equity curve chart
        self.equity_chart = PlotWidget()
        self.equity_chart.setBackground('#1e1e1e')
        self.equity_chart.showGrid(x=True, y=True, alpha=0.3)
        self.equity_chart.setLabel('left', '净值')
        self.equity_chart.setLabel('bottom', '交易日')
        summary_layout.addWidget(self.equity_chart)
        
        self.result_tabs.addTab(summary_tab, "回测结果")
        
        # Tab 2: Grid Visualization
        grid_tab = QWidget()
        grid_layout = QVBoxLayout(grid_tab)
        
        self.grid_visual = GridVisualizationWidget()
        grid_layout.addWidget(self.grid_visual)
        
        # Grid info
        self.grid_info = QTextEdit()
        self.grid_info.setReadOnly(True)
        self.grid_info.setMaximumHeight(150)
        self.grid_info.setStyleSheet("""
            QTextEdit {
                background-color: #2d2d2d;
                color: #ffffff;
                border: 1px solid #3c3c3c;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
        """)
        grid_layout.addWidget(self.grid_info)
        
        self.result_tabs.addTab(grid_tab, "网格分布")
        
        # Tab 3: Trade History
        history_tab = QWidget()
        history_layout = QVBoxLayout(history_tab)
        
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(8)
        self.history_table.setHorizontalHeaderLabels([
            "日期", "类型", "价格", "数量", "金额", "手续费", "网格", "原因"
        ])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.setStyleSheet("""
            QTableWidget {
                background-color: #2d2d2d;
                color: #ffffff;
                gridline-color: #3c3c3c;
            }
            QHeaderView::section {
                background-color: #3c3c3c;
                color: #ffffff;
                padding: 5px;
                border: none;
            }
        """)
        history_layout.addWidget(self.history_table)
        
        self.result_tabs.addTab(history_tab, "交易记录")
        
        # Tab 4: Per-bar Stats (for minute data)
        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        
        self.daily_table = QTableWidget()
        self.daily_table.setColumnCount(7)
        self.daily_table.setHorizontalHeaderLabels([
            "时间", "价格", "持仓", "持仓市值", "总资产", "收益率%", "仓位%"
        ])
        self.daily_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.daily_table.setStyleSheet("""
            QTableWidget {
                background-color: #2d2d2d;
                color: #ffffff;
                gridline-color: #3c3c3c;
            }
            QHeaderView::section {
                background-color: #3c3c3c;
                color: #ffffff;
                padding: 5px;
                border: none;
            }
        """)
        stats_layout.addWidget(self.daily_table)
        
        self.result_tabs.addTab(stats_tab, "分时统计")
        
        # Tab 5: Time-line Chart with Trade Signals (Playback)
        playback_tab = QWidget()
        playback_layout = QVBoxLayout(playback_tab)
        playback_layout.setContentsMargins(5, 5, 5, 5)
        
        # Time-line chart widget (using line chart instead of candlestick)
        self.timeline_chart = TimeLineChartWidget()
        playback_layout.addWidget(self.timeline_chart, stretch=3)
        
        # Trade playback controls
        self.trade_playback = TradePlaybackWidget()
        self.trade_playback.trade_index_changed.connect(self.on_trade_playback_changed)
        playback_layout.addWidget(self.trade_playback, stretch=1)
        
        self.result_tabs.addTab(playback_tab, "回放")
        
        right_layout.addWidget(self.result_tabs)
        
        main_layout.addWidget(right_panel, stretch=1)
    
    def load_etf_list(self):
        """Load ETF list"""
        self.etf_name_map = load_etf_name_map()
        etf_codes = get_etf_list(self.data_dir)
        
        # Store full list for filtering
        self.full_etf_list = []
        for code in etf_codes:
            name = self.etf_name_map.get(code, code)
            self.full_etf_list.append((code, name, f"{code} {name}"))
        
        # Add some default ETFs if no data
        if not self.full_etf_list:
            default_etfs = [
                ("510300", "沪深300ETF"),
                ("510500", "中证500ETF"),
                ("159915", "创业板ETF"),
            ]
            for code, name in default_etfs:
                self.full_etf_list.append((code, name, f"{code} {name}"))
        
        # Populate combo box
        self.etf_combo.clear()
        for code, name, display in self.full_etf_list:
            self.etf_combo.addItem(display, code)
    
    def filter_etf_list(self, search_text):
        """Filter ETF list based on search text"""
        if not hasattr(self, 'full_etf_list'):
            return
        
        search_text = search_text.strip().lower()
        
        # Remember current selection
        current_code = self.etf_combo.currentData()
        
        # Block signals during update
        self.etf_combo.blockSignals(True)
        self.etf_combo.clear()
        
        # Filter and add matching items
        for code, name, display in self.full_etf_list:
            if not search_text or search_text in code.lower() or search_text in name.lower():
                self.etf_combo.addItem(display, code)
        
        # Try to restore previous selection
        if current_code:
            for i in range(self.etf_combo.count()):
                if self.etf_combo.itemData(i) == current_code:
                    self.etf_combo.setCurrentIndex(i)
                    break
        
        self.etf_combo.blockSignals(False)
    
    def on_etf_changed(self, index):
        """Handle ETF selection change"""
        code = self.etf_combo.currentData()
        if code:
            # Clear current data when ETF changes
            self.current_data = None
            self.data_status_label.setText(f"已选择 {code}，请点击'加载分时数据'")
            self.data_status_label.setStyleSheet("color: #ffaa00; font-size: 11px;")
    
    def load_minute_data(self):
        """Load minute data from xtquant"""
        code = self.etf_combo.currentData()
        if not code:
            QMessageBox.warning(self, "错误", "请先选择ETF")
            return
        
        # Check if xtquant is available
        if not HAS_XTQUANT:
            QMessageBox.warning(self, "错误", "xtquant未安装，请安装xtquant或使用miniQMT")
            return
        
        if fetch_etf_kline is None:
            QMessageBox.warning(self, "错误", "无法导入fetch_etf_kline函数")
            return
        
        # Check connection
        if check_connection:
            connected, msg = check_connection()
            if not connected:
                QMessageBox.warning(self, "连接错误", f"miniQMT连接失败: {msg}")
                return
        
        # Get date range and period
        start_date = self.start_date_edit.date().toString("yyyyMMdd")
        end_date = self.end_date_edit.date().toString("yyyyMMdd")
        period_idx = self.period_combo.currentIndex()
        period = "1m" if period_idx == 0 else "5m"
        period_text = "1分钟" if period_idx == 0 else "5分钟"
        
        self.load_data_btn.setEnabled(False)
        self.data_status_label.setText(f"正在加载{period_text}数据...")
        self.data_status_label.setStyleSheet("color: #00bfff; font-size: 11px;")
        
        # Use QApplication.processEvents to update UI
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        
        try:
            # Fetch minute data from xtquant
            df = fetch_etf_kline(code, start_date, end_date, period)
            
            if df is None or df.empty:
                self.data_status_label.setText("无数据或获取失败")
                self.data_status_label.setStyleSheet("color: #ff4444; font-size: 11px;")
                QMessageBox.warning(self, "数据获取失败", f"无法获取 {code} 的{period_text}数据")
                return
            
            self.current_data = df
            
            # Get date range info
            time_col = 'time' if 'time' in df.columns else 'date'
            if time_col in df.columns and not df[time_col].isna().all():
                min_time = df[time_col].min()
                max_time = df[time_col].max()
                if hasattr(min_time, 'strftime'):
                    time_range_str = f"{min_time.strftime('%Y-%m-%d %H:%M')} ~ {max_time.strftime('%Y-%m-%d %H:%M')}"
                else:
                    time_range_str = f"{min_time} ~ {max_time}"
            else:
                time_range_str = "未知时间范围"
            
            self.data_status_label.setText(f"已加载 {len(df)} 条{period_text}数据")
            self.data_status_label.setStyleSheet("color: #00da3c; font-size: 11px;")
            self.update_status(f"已加载 {code} {period_text}数据，共 {len(df)} 条记录\n时间范围: {time_range_str}")
            
        except Exception as e:
            self.data_status_label.setText(f"加载失败: {str(e)[:30]}")
            self.data_status_label.setStyleSheet("color: #ff4444; font-size: 11px;")
            QMessageBox.warning(self, "加载错误", f"加载数据时出错: {str(e)}")
        finally:
            self.load_data_btn.setEnabled(True)
    
    def on_etf_type_changed(self, index):
        """Handle ETF type change - load default params"""
        type_map = {
            0: 'broad_market',
            1: 'sector',
            2: 'commodity',
            3: 'bond',
        }
        etf_type = type_map.get(index, 'broad_market')
        config = create_default_etf_config(etf_type)
        self.load_config(config)
    
    def on_atr_toggle(self, checked):
        """Handle ATR adaptive toggle"""
        self.atr_period_spin.setEnabled(checked)
        self.atr_mult_spin.setEnabled(checked)
        if not checked:
            # When ATR is disabled, use manual grid spacing
            self.grid_spacing_spin.setEnabled(True)
    
    def load_config(self, config: GridConfig):
        """Load configuration into UI"""
        self.capital_spin.setValue(config.initial_capital)
        self.grid_count_spin.setValue(config.grid_count)
        self.grid_spacing_spin.setValue(config.grid_spacing * 100)
        self.grid_type_combo.setCurrentIndex(
            0 if config.grid_type == GridType.GEOMETRIC else 1
        )
        self.position_ratio_spin.setValue(config.position_per_grid * 100)
        self.max_position_spin.setValue(config.max_position_ratio * 100)
        self.use_atr_check.setChecked(config.use_atr_adaptive)
        self.atr_period_spin.setValue(config.atr_period)
        self.atr_mult_spin.setValue(config.atr_multiplier)
        self.stop_loss_spin.setValue(config.stop_loss_ratio * 100)
        self.take_profit_spin.setValue(config.take_profit_ratio * 100)
        self.rebalance_spin.setValue(config.rebalance_threshold * 100)
    
    def get_config(self) -> GridConfig:
        """Get configuration from UI"""
        return GridConfig(
            initial_capital=self.capital_spin.value(),
            grid_count=self.grid_count_spin.value(),
            grid_spacing=self.grid_spacing_spin.value() / 100,
            grid_type=GridType.GEOMETRIC if self.grid_type_combo.currentIndex() == 0 else GridType.ARITHMETIC,
            position_per_grid=self.position_ratio_spin.value() / 100,
            max_position_ratio=self.max_position_spin.value() / 100,
            use_atr_adaptive=self.use_atr_check.isChecked(),
            atr_period=self.atr_period_spin.value(),
            atr_multiplier=self.atr_mult_spin.value(),
            stop_loss_ratio=self.stop_loss_spin.value() / 100,
            take_profit_ratio=self.take_profit_spin.value() / 100,
            rebalance_threshold=self.rebalance_spin.value() / 100,
        )
    
    def reset_params(self):
        """Reset parameters to defaults"""
        self.on_etf_type_changed(self.etf_type_combo.currentIndex())
    
    def run_backtest(self):
        """Run backtest"""
        code = self.etf_combo.currentData()
        if not code:
            QMessageBox.warning(self, "错误", "请先选择ETF")
            return
        
        if self.current_data is None or self.current_data.empty:
            QMessageBox.warning(self, "错误", f"请先点击'加载分时数据'获取 {code} 的分时数据")
            return
        
        # Get config
        config = self.get_config()
        
        # Create strategy
        self.strategy = ETFGridStrategy(config)
        
        # Run backtest in thread
        self.run_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        self.backtest_thread = BacktestThread(self.strategy, self.current_data.copy())
        self.backtest_thread.progress.connect(self.on_backtest_progress)
        self.backtest_thread.finished.connect(self.on_backtest_finished)
        self.backtest_thread.error.connect(self.on_backtest_error)
        self.backtest_thread.start()
    
    def on_backtest_progress(self, current, total):
        """Handle backtest progress"""
        progress = int(current / total * 100)
        self.progress_bar.setValue(progress)
    
    def on_backtest_finished(self, results):
        """Handle backtest completion"""
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        
        if 'error' in results:
            QMessageBox.warning(self, "错误", results['error'])
            return
        
        # Store results for K-line visualization
        self.backtest_results = results
        
        # Display results
        self.display_results(results)
        
        # Update grid visualization
        if self.strategy:
            grids = self.strategy.get_grids()
            current_price = self.current_data.iloc[-1]['close'] if self.current_data is not None else 0
            self.grid_visual.set_data(grids, current_price, self.strategy.state.base_price)
            
            # Update grid info
            self.update_grid_info(grids)
            
            # Update time-line chart with trade data
            self.update_timeline_chart(results)
        
        self.update_status("回测完成")
    
    def on_backtest_error(self, error_msg):
        """Handle backtest error"""
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "回测错误", error_msg)
    
    def display_results(self, results: Dict):
        """Display backtest results"""
        summary = results.get('summary', {})
        trade_history = results.get('trade_history', [])
        daily_stats = results.get('daily_stats', [])
        
        # Update stat labels
        for key, (label, unit) in self.stat_labels.items():
            value = summary.get(key, 0)
            if unit == '%':
                text = f"{value:+.2f}%"
            elif unit == '¥':
                text = f"¥{value:,.2f}"
            else:
                text = str(value)
            
            label.setText(text)
            
            # Color coding for profit/loss
            if key in ['total_return', 'total_profit', 'realized_profit', 'unrealized_profit']:
                if value > 0:
                    label.setStyleSheet("color: #ec0000; font-size: 14px; font-weight: bold;")
                elif value < 0:
                    label.setStyleSheet("color: #00da3c; font-size: 14px; font-weight: bold;")
                else:
                    label.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        
        # Draw equity curve
        self.equity_chart.clear()
        if daily_stats:
            returns = [s['total_return'] for s in daily_stats]
            self.equity_chart.plot(
                returns,
                pen=pg.mkPen(color='#00bfff', width=2),
                name='收益率'
            )
            
            # Add zero line
            zero_line = InfiniteLine(pos=0, angle=0, pen=pg.mkPen(color='#666666', width=1, style=Qt.PenStyle.DashLine))
            self.equity_chart.addItem(zero_line)
        
        # Update trade history table
        self.history_table.setRowCount(0)
        for trade in trade_history:
            if trade['type'] in ['buy', 'sell', 'rebalance']:
                row = self.history_table.rowCount()
                self.history_table.insertRow(row)
                
                self.history_table.setItem(row, 0, QTableWidgetItem(trade['date']))
                
                type_item = QTableWidgetItem(trade['type'])
                if trade['type'] == 'buy':
                    type_item.setForeground(QBrush(QColor('#ec0000')))
                elif trade['type'] == 'sell':
                    type_item.setForeground(QBrush(QColor('#00da3c')))
                else:
                    type_item.setForeground(QBrush(QColor('#ffcc00')))
                self.history_table.setItem(row, 1, type_item)
                
                self.history_table.setItem(row, 2, QTableWidgetItem(f"{trade['price']:.3f}"))
                self.history_table.setItem(row, 3, QTableWidgetItem(str(trade['quantity'])))
                self.history_table.setItem(row, 4, QTableWidgetItem(f"{trade['amount']:.2f}"))
                self.history_table.setItem(row, 5, QTableWidgetItem(f"{trade['commission']:.2f}"))
                self.history_table.setItem(row, 6, QTableWidgetItem(str(trade['grid_level'])))
                self.history_table.setItem(row, 7, QTableWidgetItem(trade['reason'][:30] + '...' if len(trade['reason']) > 30 else trade['reason']))
        
        # Update daily stats table (show last 100 rows)
        self.daily_table.setRowCount(0)
        for stat in daily_stats[-100:]:
            row = self.daily_table.rowCount()
            self.daily_table.insertRow(row)
            
            self.daily_table.setItem(row, 0, QTableWidgetItem(stat['date']))
            self.daily_table.setItem(row, 1, QTableWidgetItem(f"{stat['price']:.3f}"))
            self.daily_table.setItem(row, 2, QTableWidgetItem(str(stat['current_position'])))
            self.daily_table.setItem(row, 3, QTableWidgetItem(f"{stat['position_value']:.2f}"))
            self.daily_table.setItem(row, 4, QTableWidgetItem(f"{stat['total_value']:.2f}"))
            
            return_item = QTableWidgetItem(f"{stat['total_return']:+.2f}")
            if stat['total_return'] > 0:
                return_item.setForeground(QBrush(QColor('#ec0000')))
            elif stat['total_return'] < 0:
                return_item.setForeground(QBrush(QColor('#00da3c')))
            self.daily_table.setItem(row, 5, return_item)
            
            self.daily_table.setItem(row, 6, QTableWidgetItem(f"{stat['position_ratio']:.1f}"))
    
    def update_grid_info(self, grids):
        """Update grid information display"""
        info_lines = []
        info_lines.append(f"基准价格: {self.strategy.state.base_price:.3f}")
        info_lines.append(f"网格数量: {len(grids)}")
        info_lines.append(f"网格间距: {self.strategy.config.grid_spacing * 100:.2f}%")
        info_lines.append("")
        info_lines.append("网格分布:")
        
        for grid in sorted(grids, key=lambda x: x.price, reverse=True)[:10]:
            status = "●" if grid.is_triggered else "○"
            level_type = "卖" if grid.level > 0 else ("买" if grid.level < 0 else "基")
            info_lines.append(f"  {status} Lv{grid.level:+3d} [{level_type}]: {grid.price:.3f}")
        
        if len(grids) > 10:
            info_lines.append(f"  ... (共 {len(grids)} 个网格)")
        
        self.grid_info.setText("\n".join(info_lines))
    
    def update_timeline_chart(self, results: Dict):
        """Update time-line chart with backtest results"""
        if self.current_data is None or self.current_data.empty:
            return
        
        trade_history = results.get('trade_history', [])
        
        # Get grid prices
        grid_prices = []
        if self.strategy:
            grid_prices = self.strategy.get_grid_prices()
            base_price = self.strategy.state.base_price
        else:
            base_price = 0
        
        # Set data to time-line chart
        self.timeline_chart.set_data(
            ohlc_data=self.current_data,
            trade_history=trade_history,
            grid_prices=grid_prices,
            base_price=base_price
        )
        
        # Set trade history to playback widget
        self.trade_playback.set_trade_history(trade_history)
    
    def on_trade_playback_changed(self, trade_index: int):
        """Handle trade playback position change"""
        self.timeline_chart.show_trade_at_index(trade_index)
    
    def update_status(self, message: str):
        """Update status message"""
        # Could emit a signal to main window status bar
        print(f"[ETFGrid] {message}")


# For standalone testing
if __name__ == '__main__':
    from PyQt6.QtWidgets import QApplication
    import sys
    
    app = QApplication(sys.argv)
    
    # Apply dark theme
    app.setStyle('Fusion')
    
    widget = ETFGridWidget(data_dir="../data")
    widget.setWindowTitle("ETF网格交易策略")
    widget.resize(1200, 800)
    widget.show()
    
    sys.exit(app.exec())
