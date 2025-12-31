import pandas as pd
import numpy as np
import pyqtgraph as pg
from datetime import date
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QGridLayout, QScrollArea, QComboBox, QPushButton,
    QFrame, QSizePolicy, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QColor, QFont, QPen, QBrush

try:
    from data_loader import load_stock_data
    from .kline_widget import CandlestickItem
    from services.quote_service import get_quote_service, QuoteData, to_xt_code
except ImportError:
    from ..data_loader import load_stock_data
    from .kline_widget import CandlestickItem
    from ..services.quote_service import get_quote_service, QuoteData, to_xt_code


class MiniKLineWidget(pg.GraphicsLayoutWidget):
    """小型K线图组件，用于面板展示 - 支持实时更新当日K线"""
    
    # 颜色配置
    UP_COLOR = "#ec0000"
    DOWN_COLOR = "#00da3c"
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground('#1e1e1e')
        self.plot = self.addPlot()
        self.plot.hideAxis('left')
        self.plot.hideAxis('bottom')
        self.plot.vb.setMouseEnabled(x=False, y=False)
        self.plot.vb.setMenuEnabled(False)
        self.plot.setContentsMargins(0, 0, 0, 0)
        
        # 保存数据引用，用于实时更新
        self._display_df = None
        self._today_bar_item = None  # 当日K线图形项
        self._today_x_pos = None     # 当日K线的x坐标位置
        self._history_count = 0      # 历史K线数量（不含今天）
        
    def set_data(self, df: pd.DataFrame):
        """设置历史K线数据"""
        self.plot.clear()
        self._today_bar_item = None
        self._today_x_pos = None
        self._history_count = 0
        
        if df is None or df.empty:
            self._display_df = None
            return
            
        # 只显示最近 40 天
        self._display_df = df.tail(40).copy()
        self._display_df.reset_index(drop=True, inplace=True)
        
        # 检查最后一天是否是今天
        today = date.today()
        last_idx = len(self._display_df) - 1
        last_date = pd.Timestamp(self._display_df.iloc[last_idx]['date']).date()
        
        if last_date == today and len(self._display_df) > 1:
            # 最后一天是今天，分开绘制：历史K线（不含今天）+ 当日K线（单独绘制）
            history_df = self._display_df.iloc[:-1].copy()
            history_df.reset_index(drop=True, inplace=True)
            
            # 绘制历史K线（不含今天）
            candle_item = CandlestickItem(history_df)
            self.plot.addItem(candle_item)
            
            # 记录历史K线数量，当日K线的x坐标位置
            self._history_count = len(history_df)
            self._today_x_pos = self._history_count  # 今天的x位置 = 历史数量
            
            # 单独绘制今天的K线
            self._redraw_today_candle()
        else:
            # 最后一天不是今天，正常绘制所有历史K线
            candle_item = CandlestickItem(self._display_df)
            self.plot.addItem(candle_item)
            
            # 记录历史K线数量，当日K线的x坐标位置（在历史数据之后）
            self._history_count = len(self._display_df)
            self._today_x_pos = self._history_count  # 今天的x位置 = 历史数量
        
        # 自动缩放
        self._update_range()
    
    def update_today_bar(self, quote: QuoteData):
        """
        根据实时行情更新当日K线
        
        Args:
            quote: 实时行情数据
        """
        if self._display_df is None or self._display_df.empty:
            return
        
        if quote.last_price <= 0:
            return
        
        today = date.today()
        last_idx = len(self._display_df) - 1
        last_row = self._display_df.iloc[last_idx]
        last_date = pd.Timestamp(last_row['date']).date()
        
        if last_date == today:
            # 更新今日K线数据（数据已存在于 _display_df）
            self._display_df.loc[self._display_df.index[last_idx], 'close'] = quote.last_price
            self._display_df.loc[self._display_df.index[last_idx], 'high'] = max(
                self._display_df.iloc[last_idx]['high'],
                quote.high_price if quote.high_price > 0 else quote.last_price
            )
            self._display_df.loc[self._display_df.index[last_idx], 'low'] = min(
                self._display_df.iloc[last_idx]['low'],
                quote.low_price if quote.low_price > 0 else quote.last_price
            )
        else:
            # 今日数据不存在，添加新的一行
            new_row = {
                'date': pd.Timestamp(today),
                'open': quote.open_price if quote.open_price > 0 else quote.last_price,
                'high': quote.high_price if quote.high_price > 0 else quote.last_price,
                'low': quote.low_price if quote.low_price > 0 else quote.last_price,
                'close': quote.last_price,
                'volume': quote.volume if quote.volume > 0 else 0,
            }
            new_df = pd.DataFrame([new_row])
            self._display_df = pd.concat([self._display_df, new_df], ignore_index=True)
            # 注意：不截断数据，保持 _today_x_pos 不变
        
        # 重绘当日K线（使用预设的x坐标位置）
        self._redraw_today_candle()
        
        # 更新Y轴范围
        self._update_range()
    
    def _redraw_today_candle(self):
        """重绘当日K线"""
        if self._display_df is None or self._today_x_pos is None:
            return
        
        # 查找今天的数据
        today = date.today()
        today_data = None
        for i in range(len(self._display_df) - 1, -1, -1):
            row_date = pd.Timestamp(self._display_df.iloc[i]['date']).date()
            if row_date == today:
                today_data = self._display_df.iloc[i]
                break
        
        if today_data is None:
            return
        
        # 移除旧的当日K线图形项
        if self._today_bar_item is not None:
            self.plot.removeItem(self._today_bar_item)
            self._today_bar_item = None
        
        # 创建新的当日K线图形项（使用预设的x坐标位置）
        self._today_bar_item = MiniTodayCandleItem(
            self._today_x_pos,  # 使用预设的位置，不会和历史K线重叠
            today_data['open'], today_data['high'], today_data['low'], today_data['close'],
            up_color=self.UP_COLOR,
            down_color=self.DOWN_COLOR
        )
        self.plot.addItem(self._today_bar_item)
    
    def _update_range(self):
        """更新显示范围"""
        if self._display_df is None or self._display_df.empty:
            return
        
        low = self._display_df['low'].min()
        high = self._display_df['high'].max()
        padding = (high - low) * 0.1 if high != low else 0.1
        self.plot.setYRange(low - padding, high + padding, padding=0)
        
        # X轴范围：历史数据 + 当日K线（如果有）
        x_max = self._history_count
        if self._today_x_pos is not None:
            x_max = self._today_x_pos + 1
        self.plot.setXRange(0, x_max, padding=0.02)


class MiniTodayCandleItem(pg.GraphicsObject):
    """单根K线图形项，用于实时更新当日K线"""
    
    def __init__(self, x, open_price, high_price, low_price, close_price,
                 up_color="#ec0000", down_color="#00da3c"):
        super().__init__()
        self.x = x
        self.o = open_price
        self.h = high_price
        self.l = low_price
        self.c = close_price
        self.up_color = QColor(up_color)
        self.down_color = QColor(down_color)
        self.picture = None
        self.generatePicture()
    
    def generatePicture(self):
        from PyQt6.QtGui import QPicture, QPainter
        
        self.picture = QPicture()
        painter = QPainter(self.picture)
        
        w = 0.3
        color = self.up_color if self.c >= self.o else self.down_color
        
        pen = QPen(color)
        pen.setCosmetic(True)
        pen.setWidthF(1.0)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(QBrush(color))
        
        # 影线
        painter.drawLine(
            pg.QtCore.QPointF(float(self.x), float(self.l)),
            pg.QtCore.QPointF(float(self.x), float(self.h))
        )
        
        # 实体
        body_height = abs(self.c - self.o)
        if body_height < 0.001:
            painter.drawLine(
                pg.QtCore.QPointF(float(self.x - w), float(self.c)),
                pg.QtCore.QPointF(float(self.x + w), float(self.c))
            )
        else:
            body_top = min(self.o, self.c)
            rect = pg.QtCore.QRectF(
                float(self.x - w), float(body_top),
                float(w * 2), float(body_height)
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillRect(rect, QBrush(color))
        
        painter.end()
    
    def paint(self, painter, *args):
        if self.picture:
            self.picture.play(painter)
    
    def boundingRect(self):
        if self.picture is None:
            return pg.QtCore.QRectF()
        return pg.QtCore.QRectF(self.picture.boundingRect())


class StockCard(QFrame):
    """单只股票的卡片显示 - 支持实时行情更新和当日K线"""
    clicked = pyqtSignal(str, str)  # code, name

    def __init__(self, code, name, parent=None):
        super().__init__(parent)
        self.code = code
        self.name = name
        self._last_price = 0.0
        self._prev_close = 0.0
        self._history_df = None  # 保存历史数据引用
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
        """从历史数据更新卡片"""
        self._history_df = df  # 保存引用
        
        if df is not None and not df.empty:
            last_row = df.iloc[-1]
            price = last_row['close']
            
            # 计算涨跌幅
            change_pct = 0
            if len(df) > 1:
                self._prev_close = df.iloc[-2]['close']
                change_pct = (price - self._prev_close) / self._prev_close * 100
            
            self._last_price = price
            self._update_display(price, change_pct)
            self.mini_kline.set_data(df)
    
    def update_realtime(self, quote: QuoteData):
        """
        实时行情更新（同时更新价格和当日K线）
        
        Args:
            quote: 实时行情数据
        """
        if quote is None:
            return
        
        price = quote.last_price
        if price <= 0:
            return
        
        # 使用实时数据的昨收价
        if quote.prev_close > 0:
            self._prev_close = quote.prev_close
        
        change_pct = quote.change_pct
        self._last_price = price
        self._update_display(price, change_pct)
        
        # 更新当日K线
        self.mini_kline.update_today_bar(quote)
    
    def _update_display(self, price: float, change_pct: float):
        """更新显示"""
        color = "#ff4d4f" if change_pct >= 0 else "#2ecc71"  # 优化后的红绿色
        self.price_label.setText(f"{price:.2f}")
        self.price_label.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {color}; font-family: Consolas;")
        self.change_label.setText(f"{change_pct:+.2f}%")
        self.change_label.setStyleSheet(f"font-size: 11px; color: {color}; font-weight: normal;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.code, self.name)


class WatchlistPanelWidget(QWidget):
    """面板组件 - 矩阵式展示 (跟随左侧列表，仅限分组模式) + 实时行情支持"""
    stockSelected = pyqtSignal(str, str)  # code, name

    def __init__(self, watchlist_manager, name_map, data_dir, parent=None):
        super().__init__(parent)
        self.manager = watchlist_manager
        self.name_map = name_map
        self.data_dir = data_dir
        self.cards = []
        self.cards_map = {}  # code -> StockCard 映射，用于快速查找
        self.current_stocks = []
        self.is_group_mode = False
        
        # 实时行情相关
        self._realtime_enabled = False
        self._quote_service = None
        
        self.setupUI()
        
    def setupUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 顶部工具栏
        toolbar = QHBoxLayout()
        
        # 实时行情开关
        self.realtime_checkbox = QCheckBox("📡 实时行情")
        self.realtime_checkbox.setToolTip("开启后将实时更新价格和涨跌幅（需要连接 miniQMT）")
        self.realtime_checkbox.setStyleSheet("""
            QCheckBox {
                color: #ffffff;
                font-size: 12px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
            QCheckBox::indicator:checked {
                background-color: #0078d4;
                border: 1px solid #0078d4;
                border-radius: 3px;
            }
            QCheckBox::indicator:unchecked {
                background-color: #2d2d2d;
                border: 1px solid #555;
                border-radius: 3px;
            }
        """)
        self.realtime_checkbox.toggled.connect(self._on_realtime_toggled)
        toolbar.addWidget(self.realtime_checkbox)
        
        # 刷新按钮
        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.setFixedWidth(70)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #ffffff;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3d3d3d;
                border-color: #0078d4;
            }
        """)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        toolbar.addWidget(self.refresh_btn)
        
        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self.status_label)
        
        toolbar.addStretch()
        layout.addLayout(toolbar)
        
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
            self._stop_realtime()
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
        
        # 如果实时行情已开启，重新订阅
        if self._realtime_enabled:
            self._subscribe_quotes()

    def set_group_mode(self, enabled: bool):
        """设置是否为分组模式"""
        self.is_group_mode = enabled
        if not enabled:
            self.current_stocks = []
            self.refresh_grid()
            self.scroll_area.hide()
            self.placeholder_label.show()
            self._stop_realtime()
        else:
            self.placeholder_label.hide()
            self.scroll_area.show()
        
    def refresh_grid(self):
        """刷新网格布局"""
        # 清除现有卡片
        for card in self.cards:
            self.grid_layout.removeWidget(card)
            card.deleteLater()
        self.cards = []
        self.cards_map = {}
        
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
            
            # 加载历史数据
            from data_loader import load_stock_data
            df = load_stock_data(code, self.data_dir)
            card.update_data(df)
            
            row = i // cols
            col = i % cols
            self.grid_layout.addWidget(card, row, col)
            self.cards.append(card)
            self.cards_map[code] = card
        
        self.status_label.setText(f"共 {len(self.cards)} 只")

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
    
    # ==================== 实时行情相关方法 ====================
    
    def _on_realtime_toggled(self, checked: bool):
        """实时行情开关切换"""
        if checked:
            self._start_realtime()
        else:
            self._stop_realtime()
    
    def _on_refresh_clicked(self):
        """刷新按钮点击"""
        if self._realtime_enabled and self._quote_service:
            # 主动刷新行情
            self._quote_service.refresh_quotes()
            self.status_label.setText("已刷新")
        else:
            # 刷新历史数据
            self.refresh_grid()
            self.status_label.setText(f"共 {len(self.cards)} 只")
    
    def _start_realtime(self):
        """启动实时行情"""
        if self._realtime_enabled:
            return
        
        try:
            self._quote_service = get_quote_service()
            
            if not self._quote_service.is_available:
                self.realtime_checkbox.setChecked(False)
                self.status_label.setText("⚠ xtquant 未安装")
                return
            
            # 连接信号
            self._quote_service.quotes_batch_updated.connect(self._on_quotes_updated)
            self._quote_service.connection_status_changed.connect(self._on_connection_status)
            
            # 启动服务
            if self._quote_service.start():
                self._realtime_enabled = True
                self._subscribe_quotes()
                self.status_label.setText("📡 实时行情已开启")
            else:
                self.realtime_checkbox.setChecked(False)
                self.status_label.setText("⚠ 启动失败")
                
        except Exception as e:
            self.realtime_checkbox.setChecked(False)
            self.status_label.setText(f"⚠ 错误: {e}")
    
    def _stop_realtime(self):
        """停止实时行情"""
        if not self._realtime_enabled:
            return
        
        try:
            if self._quote_service:
                # 取消订阅当前列表
                if self.current_stocks:
                    self._quote_service.unsubscribe(self.current_stocks)
                
                # 断开信号
                try:
                    self._quote_service.quotes_batch_updated.disconnect(self._on_quotes_updated)
                    self._quote_service.connection_status_changed.disconnect(self._on_connection_status)
                except:
                    pass
            
            self._realtime_enabled = False
            self.status_label.setText(f"共 {len(self.cards)} 只")
            
        except Exception as e:
            self.status_label.setText(f"⚠ 停止失败: {e}")
    
    def _subscribe_quotes(self):
        """订阅当前列表的行情"""
        if not self._quote_service or not self.current_stocks:
            return
        
        if self._quote_service.subscribe(self.current_stocks):
            # 首次主动获取一次行情
            QTimer.singleShot(500, lambda: self._quote_service.refresh_quotes(self.current_stocks))
    
    def _on_quotes_updated(self, quotes: dict):
        """
        处理批量行情更新
        
        Args:
            quotes: Dict[xt_code, QuoteData]
        """
        if not self._realtime_enabled:
            return
        
        for xt_code, quote in quotes.items():
            simple_code = quote.simple_code
            card = self.cards_map.get(simple_code)
            if card:
                card.update_realtime(quote)
        
        # 更新状态
        self.status_label.setText(f"📡 {len(self.cards)} 只 | 已更新")
    
    def _on_connection_status(self, connected: bool, message: str):
        """处理连接状态变化"""
        if connected:
            self.status_label.setText(f"📡 {message}")
        else:
            self.status_label.setText(f"⚠ {message}")
    
    def closeEvent(self, event):
        """关闭时停止实时行情"""
        self._stop_realtime()
        super().closeEvent(event)
