# timeshare_widget.py - 分时图组件
"""
分时图组件，用于显示单日的分时走势
通达信风格：白色价格线、黄色均价线、前收盘中位线
支持交易时段内实时刷新
"""
import numpy as np
import pandas as pd
from typing import Optional
import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox, QDialog, QPushButton
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QPen, QBrush

import pyqtgraph as pg

# 设置日志
import logging
logger = logging.getLogger(__name__)

# 尝试导入数据获取模块
import sys
import os
from pathlib import Path

# 添加项目根目录到 sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from widgets.order_book_widget import OrderBookWidget

# 优先使用 xtquant，备用 akshare
try:
    from fetch_kline_xtquant import get_minute_data, check_xtquant_available, check_connection, _to_xt_code
    from xtquant import xtdata
    HAS_XTQUANT = check_xtquant_available()
except ImportError:
    get_minute_data = None
    HAS_XTQUANT = False
    check_connection = None
    xtdata = None

# 备用：akshare
try:
    from fetch_minute import fetch_minute_data_with_cache
except ImportError:
    fetch_minute_data_with_cache = None


# Trading hours configuration
MORNING_SESSION = (datetime.time(9, 30), datetime.time(11, 30))
AFTERNOON_SESSION = (datetime.time(13, 0), datetime.time(15, 0))


def is_trading_time() -> bool:
    """Check if current time is within trading hours"""
    now = datetime.datetime.now()
    current_time = now.time()
    
    # Check if it's a weekday (Monday=0, Sunday=6)
    if now.weekday() >= 5:
        return False
    
    # Check trading sessions
    morning_start, morning_end = MORNING_SESSION
    afternoon_start, afternoon_end = AFTERNOON_SESSION
    
    is_morning = morning_start <= current_time <= morning_end
    is_afternoon = afternoon_start <= current_time <= afternoon_end
    
    return is_morning or is_afternoon


def is_trading_day(date_str: str) -> bool:
    """Check if the given date is today"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    return date_str == today


class ColorAxisItem(pg.AxisItem):
    """自定义颜色的坐标轴"""
    def __init__(self, *args, **kwargs):
        self.is_percent = kwargs.pop('is_percent', False)
        super().__init__(*args, **kwargs)
        self.prev_close = None
        self.up_color = QColor("#ff4d4d")
        self.down_color = QColor("#00b800")
        self.normal_color = QColor("#ffffff")

    def tickStrings(self, values, scale, spacing):
        if self.is_percent and self.prev_close:
            strings = []
            for v in values:
                pct = (v - self.prev_close) / self.prev_close * 100
                strings.append(f"{pct:+.2f}%")
            return strings
        return super().tickStrings(values, scale, spacing)

    def drawPicture(self, p, axisSpec, tickSpecs, textSpecs):
        super().drawPicture(p, axisSpec, tickSpecs, [])
        
        p.setRenderHint(p.RenderHint.Antialiasing, False)
        
        for rect, flags, text in textSpecs:
            color = self.normal_color
            try:
                if self.is_percent:
                    val_str = text.replace('%', '').replace('+', '')
                    val = float(val_str)
                    if val > 0.001: color = self.up_color
                    elif val < -0.001: color = self.down_color
                else:
                    val = float(text.replace(',', ''))
                    if self.prev_close:
                        if val > self.prev_close + 0.001: color = self.up_color
                        elif val < self.prev_close - 0.001: color = self.down_color
            except ValueError:
                pass
            
            p.setPen(color)
            p.drawText(rect, flags, text)


class TimeShareWidget(QWidget):
    """分时图组件 - 通达信风格，支持实时刷新"""
    
    # Signal emitted when refresh status changes
    refreshStatusChanged = pyqtSignal(bool, str)  # is_refreshing, message
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.data: Optional[pd.DataFrame] = None
        self.code = ""
        self.date_str = ""
        self.data_dir = ""
        self.prev_close: Optional[float] = None  # 前收盘价
        self.avg_prices: Optional[np.ndarray] = None  # 均价数组
        self._data_source = ""  # Track data source for status display
        
        # Real-time refresh settings
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_timer)
        self._refresh_interval = 5000  # 60 seconds (1 minute) - matches the data frequency
        self._is_auto_refresh_enabled = True
        self._last_data_count = 0  # For detecting new data
        
        # 颜色配置（通达信风格）
        self.price_color = "#ffffff"       # 白色价格线
        self.avg_color = "#ffdd00"         # 黄色均价线
        self.prev_close_color = "#888888"  # 灰色前收盘线
        self.up_color = "#ff4d4d"          # 红色上涨
        self.down_color = "#00b800"        # 绿色下跌
        
        self.setupUI()
    
    def setupUI(self):
        """设置界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        # Top control bar
        control_bar = QHBoxLayout()
        control_bar.setContentsMargins(5, 2, 5, 2)
        
        # 信息栏
        self.info_label = QLabel("加载中...")
        self.info_label.setStyleSheet("""
            QLabel {
                background-color: #1e1e1e;
                color: #ffffff;
                padding: 5px 10px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
        """)
        control_bar.addWidget(self.info_label, stretch=1)
        
        # Refresh status label
        self.refresh_status_label = QLabel("")
        self.refresh_status_label.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 11px;
                padding: 0 5px;
            }
        """)
        control_bar.addWidget(self.refresh_status_label)
        
        # Manual refresh button
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setToolTip("手动刷新数据")
        self.refresh_btn.setFixedSize(28, 24)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                border: 1px solid #555;
                border-radius: 3px;
                color: #fff;
            }
            QPushButton:hover {
                background-color: #3d3d3d;
            }
            QPushButton:pressed {
                background-color: #1d1d1d;
            }
        """)
        self.refresh_btn.clicked.connect(self.manual_refresh)
        control_bar.addWidget(self.refresh_btn)
        
        layout.addLayout(control_bar)
        
        # 创建主显示区域（图表 + 盘口）
        main_display = QHBoxLayout()
        main_display.setSpacing(0)
        
        # 创建图形布局
        self.graphics_layout = pg.GraphicsLayoutWidget()
        self.graphics_layout.setBackground('#1a1a1a')
        main_display.addWidget(self.graphics_layout, stretch=1)
        
        # 创建盘口区域
        self.order_book = OrderBookWidget()
        self.order_book.setFixedWidth(180)
        main_display.addWidget(self.order_book)
        
        layout.addLayout(main_display, stretch=1)
        
        self.setup_plots()
    
    def setup_plots(self):
        """设置图表"""
        self.graphics_layout.clear()
        
        Y_AXIS_WIDTH = 60
        
        # 创建自定义轴
        self.left_axis = ColorAxisItem(orientation='left')
        self.right_axis = ColorAxisItem(orientation='right', is_percent=True)
        
        # 主图（价格）
        # 使用自定义轴
        self.price_plot = self.graphics_layout.addPlot(
            row=0, col=0, 
            axisItems={'left': self.left_axis, 'right': self.right_axis}
        )
        self.price_plot.setLabel('left', '')
        self.price_plot.showGrid(x=True, y=True, alpha=0.2)
        self.price_plot.getAxis('left').setWidth(Y_AXIS_WIDTH)
        self.price_plot.getAxis('right').setWidth(Y_AXIS_WIDTH)
        self.price_plot.getAxis('bottom').setHeight(0)  # 隐藏底部轴
        
        # 成交量图
        self.volume_plot = self.graphics_layout.addPlot(row=1, col=0)
        self.volume_plot.setLabel('left', '')
        self.volume_plot.showGrid(x=True, y=True, alpha=0.2)
        self.volume_plot.setMaximumHeight(120)
        self.volume_plot.setXLink(self.price_plot)
        self.volume_plot.getAxis('left').setWidth(Y_AXIS_WIDTH)
        
        # 设置十字光标
        self.setup_crosshair()

    def setup_crosshair(self):
        """设置十字光标"""
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#888888', width=1))
        self.hLine = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('#888888', width=1))
        
        self.price_plot.addItem(self.vLine, ignoreBounds=True)
        self.price_plot.addItem(self.hLine, ignoreBounds=True)
        
        self.proxy = pg.SignalProxy(self.price_plot.scene().sigMouseMoved, rateLimit=60, slot=self.on_mouse_moved)

    def on_mouse_moved(self, evt):
        """处理鼠标移动"""
        pos = evt[0]
        if self.data is None:
            return
            
        if self.price_plot.sceneBoundingRect().contains(pos):
            mouse_point = self.price_plot.vb.mapSceneToView(pos)
            x = int(round(mouse_point.x()))
            
            if 0 <= x < len(self.data):
                self.vLine.setPos(x)
                self.hLine.setPos(mouse_point.y())
                
                # 更新信息栏
                row = self.data.iloc[x]
                self.update_info_label(row, x)

    def update_info_label(self, row, idx: int):
        """更新信息栏"""
        time_str = row["time"].strftime("%H:%M") if hasattr(row["time"], "strftime") else str(row["time"])
        price = row["close"]
        volume = row["volume"]
        
        # 计算涨跌幅（相对前收盘价）
        if self.prev_close and self.prev_close > 0:
            change_pct = (price - self.prev_close) / self.prev_close * 100
            pct_color = self.up_color if change_pct >= 0 else self.down_color
            pct_str = f"<span style='color:{pct_color}'>{change_pct:+.2f}%</span>"
        else:
            pct_str = "N/A"
        
        # 获取均价
        avg_price_str = ""
        if self.avg_prices is not None and idx < len(self.avg_prices):
            avg_price = self.avg_prices[idx]
            avg_price_str = f" | <span style='color:{self.avg_color}'>均价: {avg_price:.2f}</span>"
        
        # 格式化成交量（手）
        vol_str = f"{volume:.0f}手" if volume < 10000 else f"{volume/10000:.2f}万手"
        
        info = (
            f"<span style='color:#ffffff'>{self.code}</span> | "
            f"<span style='color:#aaaaaa'>{time_str}</span> | "
            f"<span style='color:#ffffff'>价格: {price:.2f}</span> | "
            f"涨跌: {pct_str}{avg_price_str} | "
            f"<span style='color:#aaaaaa'>成交量: {vol_str}</span>"
        )
        self.info_label.setText(info)

    def load_data(self, code: str, date_str: str, data_dir: str = "../data", prev_close: float = None):
        """
        加载分时数据
        
        优先使用 xtquant（miniQMT）获取数据，如果不可用则回退到 AkShare
        
        Args:
            code: 股票代码
            date_str: 日期字符串 YYYY-MM-DD
            data_dir: 数据目录路径
            prev_close: 前收盘价（可选）
        """
        self.code = code
        self.date_str = date_str
        self.data_dir = data_dir
        self.prev_close = prev_close
        self.info_label.setText(f"正在加载 {code} {date_str} 分时数据...")
        
        # 强制刷新界面
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        
        # Fetch the data
        self._fetch_and_update_data(is_initial=True)
        
        # Start auto refresh if this is today and it's trading time
        self._update_auto_refresh_state()

    def _fetch_and_update_data(self, is_initial: bool = False):
        """
        Fetch data from source and update chart
        
        Args:
            is_initial: True if this is the initial load, False for refresh
        """
        from PyQt6.QtWidgets import QApplication
        
        # 转换日期格式 YYYY-MM-DD -> YYYYMMDD
        date_param = self.date_str.replace("-", "")
        
        df = None
        data_source = ""
        
        # Check if the code is an ETF (starts with 51/56/58 for SH, or 15/16/159 for SZ)
        is_etf = self._is_etf_code(self.code)
        
        # 优先使用 xtquant
        if HAS_XTQUANT and get_minute_data is not None:
            try:
                # 检查 miniQMT 连接
                if check_connection:
                    connected, msg = check_connection()
                    if connected:
                        if is_initial:
                            self.info_label.setText(f"正在从 miniQMT 获取 {self.code} {self.date_str} 分时数据...")
                            QApplication.processEvents()
                        
                        df = get_minute_data(code=self.code, trade_date=date_param, freq="1m")
                        if df is not None and not df.empty:
                            data_source = "miniQMT"
                        
                        # 获取五档盘口
                        try:
                            xt_code = _to_xt_code(self.code)
                            full_tick = xtdata.get_full_tick([xt_code])
                            if xt_code in full_tick:
                                self.order_book.update_data(full_tick[xt_code], self.prev_close)
                        except Exception as e:
                            logger.error(f"获取盘口数据失败: {e}")
                    else:
                        if is_initial:
                            self.info_label.setText(f"miniQMT 未连接，尝试其他数据源...")
                            QApplication.processEvents()
            except Exception as e:
                if is_initial:
                    self.info_label.setText(f"xtquant 获取失败: {e}，尝试其他数据源...")
                    QApplication.processEvents()
        
        # 如果 xtquant 获取失败，对于股票可以回退到 AkShare（ETF不支持akshare）
        if (df is None or df.empty) and fetch_minute_data_with_cache is not None and not is_etf:
            try:
                data_path = Path(self.data_dir)
                
                if is_initial:
                    self.info_label.setText(f"正在从 AkShare 获取 {self.code} {self.date_str} 分时数据...")
                    QApplication.processEvents()
                
                # For refresh during trading hours, force refresh to get latest data
                force_refresh = not is_initial and is_trading_time()
                
                df = fetch_minute_data_with_cache(
                    code=self.code,
                    trade_date=date_param,
                    data_dir=data_path,
                    freq="1",
                    force_refresh=force_refresh
                )
                
                if df is None or df.empty:
                    if is_initial:
                        self.info_label.setText(f"本地无缓存，正在从 AkShare 网络拉取...")
                        QApplication.processEvents()
                    
                    df = fetch_minute_data_with_cache(
                        code=self.code,
                        trade_date=date_param,
                        data_dir=data_path,
                        freq="1",
                        force_refresh=True
                    )
                
                if df is not None and not df.empty:
                    data_source = "AkShare"
                    
            except Exception as e:
                if is_initial:
                    self.info_label.setText(f"AkShare 获取失败: {e}")
        
        # 检查是否获取到数据
        if df is None or df.empty:
            error_msg = f"未找到 {self.code} {self.date_str} 的分时数据"
            if is_etf:
                error_msg += "（ETF分时数据需要miniQMT连接）"
            elif not HAS_XTQUANT and fetch_minute_data_with_cache is None:
                error_msg += "（请确保 miniQMT 已启动或已安装 akshare）"
            elif not HAS_XTQUANT:
                error_msg += "（miniQMT 未连接，AkShare 也无数据）"
            else:
                error_msg += "（可能是非交易日）"
            self.info_label.setText(error_msg)
            return
        
        # Check if data has changed (for refresh optimization)
        new_data_count = len(df)
        data_changed = new_data_count != self._last_data_count
        
        if not is_initial and not data_changed:
            # No new data, just update the status
            self._update_refresh_status("数据无更新")
            return
        
        self._last_data_count = new_data_count
        self.data = df
        self._data_source = data_source
        
        # 如果没有传入前收盘价，使用当日开盘价作为替代
        if self.prev_close is None:
            self.prev_close = df['open'].iloc[0]
        
        self.draw_chart()
        
        # 更新标题信息
        self._update_title_info()
    
    def _is_etf_code(self, code: str) -> bool:
        """
        判断是否为ETF代码
        
        ETF代码规则：
        - 上交所: 51xxxx, 56xxxx, 58xxxx
        - 深交所: 15xxxx, 16xxxx, 159xxx
        """
        if not code or len(code) < 2:
            return False
        prefix2 = code[:2]
        prefix3 = code[:3]
        return prefix2 in ('51', '56', '58', '15', '16') or prefix3 == '159'

    def _update_title_info(self):
        """Update the title info label"""
        if self.data is None or self.data.empty:
            return
            
        current_price = self.data['close'].iloc[-1]
        if self.prev_close and self.prev_close > 0:
            change_pct = (current_price - self.prev_close) / self.prev_close * 100
            pct_color = self.up_color if change_pct >= 0 else self.down_color
            avg_price_str = f"{self.avg_prices[-1]:.2f}" if self.avg_prices is not None and len(self.avg_prices) > 0 else "N/A"
            self.info_label.setText(
                f"<span style='color:#ffffff'>{self.code} {self.date_str}</span> | "
                f"<span style='color:#ffffff'>现价: {current_price:.2f}</span> | "
                f"涨跌: <span style='color:{pct_color}'>{change_pct:+.2f}%</span> | "
                f"<span style='color:{self.avg_color}'>均价: {avg_price_str}</span> | "
                f"<span style='color:#888888'>数据源: {self._data_source}</span>"
            )
        else:
            self.info_label.setText(f"{self.code} {self.date_str} 分时图 (共 {len(self.data)} 条，来源: {self._data_source})")

    def draw_chart(self):
        """绘制图表"""
        if self.data is None:
            return
        
        self.setup_plots()  # 重新初始化图表
        
        x = np.arange(len(self.data))
        prices = self.data["close"].values
        volumes = self.data["volume"].values
        
        # 计算均价线
        # AkShare (东方财富): amount 单位是元，volume 单位是手（1手=100股）
        # 均价 = 累计成交额(元) / (累计成交量(手) * 100)
        if "amount" in self.data.columns and "volume" in self.data.columns:
            cum_amount = self.data["amount"].cumsum()
            cum_volume = self.data["volume"].cumsum()
            # 注意：volume 是手，需要乘以 100 转换成股
            self.avg_prices = (cum_amount / (cum_volume * 100 + 1e-9)).values
        else:
            # 如果没有 amount 数据，使用典型价格估算
            self.avg_prices = ((self.data["high"] + self.data["low"] + self.data["close"]) / 3).values
        
        # 计算 Y 轴范围（以前收盘价为中心，对称显示）
        if self.prev_close and self.prev_close > 0:
            # 更新轴的 prev_close
            self.left_axis.prev_close = self.prev_close
            self.right_axis.prev_close = self.prev_close
            
            # 计算最大偏离幅度
            all_prices = np.concatenate([prices, self.avg_prices])
            max_price = np.nanmax(all_prices)
            min_price = np.nanmin(all_prices)
            
            max_deviation = max(abs(max_price - self.prev_close), abs(min_price - self.prev_close))
            max_deviation *= 1.05  # 增加 5% 边距
            
            y_min = self.prev_close - max_deviation
            y_max = self.prev_close + max_deviation
            
            # 绘制前收盘中位线
            prev_close_line = pg.InfiniteLine(
                pos=self.prev_close, 
                angle=0, 
                pen=pg.mkPen(self.prev_close_color, width=1.5, style=Qt.PenStyle.DashLine)
            )
            self.price_plot.addItem(prev_close_line)
            
            # 设置 Y 轴范围
            self.price_plot.setYRange(y_min, y_max, padding=0)
        else:
            self.price_plot.enableAutoRange(axis='y')
        
        # 绘制价格线（白色）
        self.price_plot.plot(x, prices, pen=pg.mkPen(self.price_color, width=1.5))
        
        # 绘制均价线（黄色）
        self.price_plot.plot(x, self.avg_prices, pen=pg.mkPen(self.avg_color, width=1.2))
        
        # 绘制成交量（颜色根据相对前一分钟涨跌）
        colors = []
        # 第一根
        if self.prev_close and self.prev_close > 0:
            colors.append(self.up_color if prices[0] >= self.prev_close else self.down_color)
        else:
            colors.append(self.up_color)
            
        # 后续
        for i in range(1, len(prices)):
            if prices[i] >= prices[i-1]:
                colors.append(self.up_color)
            else:
                colors.append(self.down_color)
        
        brushes = [pg.mkBrush(c) for c in colors]
        bar_item = pg.BarGraphItem(x=x, height=volumes, width=0.6, brushes=brushes)
        self.volume_plot.addItem(bar_item)
        
        # 设置 X 轴标签（关键时间点）
        self.setup_x_axis()
        
        # 设置成交量 Y 轴范围
        self.volume_plot.setYRange(0, np.max(volumes) * 1.05, padding=0)
    
    def setup_x_axis(self):
        """设置 X 轴标签（关键时间点）"""
        if self.data is None:
            return
        
        times = self.data["time"].dt.strftime("%H:%M").tolist() if hasattr(self.data["time"].iloc[0], "strftime") else [str(t) for t in self.data["time"]]
        
        # 关键时间点
        key_times = ["09:30", "10:00", "10:30", "11:00", "11:30", "13:00", "13:30", "14:00", "14:30", "15:00"]
        
        ticks = []
        for i, t_str in enumerate(times):
            if t_str in key_times:
                ticks.append((i, t_str))
        
        # 如果没有找到关键时间点，使用默认间隔
        if not ticks:
            step = max(1, len(times) // 8)
            for i in range(0, len(times), step):
                ticks.append((i, times[i]))
        
        ax = self.volume_plot.getAxis('bottom')
        ax.setTicks([ticks])

    # ========== Real-time Refresh Methods ==========
    
    def set_refresh_interval(self, interval_ms: int):
        """
        Set the auto-refresh interval
        
        Args:
            interval_ms: Refresh interval in milliseconds (default 5000)
        """
        self._refresh_interval = interval_ms
        if self._refresh_timer.isActive():
            self._refresh_timer.setInterval(interval_ms)
    
    def set_auto_refresh_enabled(self, enabled: bool):
        """
        Enable or disable auto-refresh
        
        Args:
            enabled: True to enable, False to disable
        """
        self._is_auto_refresh_enabled = enabled
        self._update_auto_refresh_state()
    
    def start_auto_refresh(self):
        """Start the auto-refresh timer"""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(self._refresh_interval)
            self._update_refresh_status("自动刷新中")
            self.refreshStatusChanged.emit(True, "自动刷新已启动")
    
    def stop_auto_refresh(self):
        """Stop the auto-refresh timer"""
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
            self._update_refresh_status("刷新已停止")
            self.refreshStatusChanged.emit(False, "自动刷新已停止")
    
    def manual_refresh(self):
        """Manually trigger a data refresh"""
        if not self.code:
            return
        
        self._update_refresh_status("刷新中...")
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        
        self._fetch_and_update_data(is_initial=False)
        
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self._update_refresh_status(f"已刷新 {now}")
    
    def _on_refresh_timer(self):
        """Called by the refresh timer"""
        # Check if we should still be refreshing
        if not self._should_auto_refresh():
            self.stop_auto_refresh()
            return
        
        # Perform the refresh
        self._fetch_and_update_data(is_initial=False)
        
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self._update_refresh_status(f"自动刷新 {now}")
    
    def _should_auto_refresh(self) -> bool:
        """Check if auto-refresh should be active"""
        if not self._is_auto_refresh_enabled:
            return False
        if not self.code or not self.date_str:
            return False
        if not is_trading_day(self.date_str):
            return False
        if not is_trading_time():
            return False
        return True
    
    def _update_auto_refresh_state(self):
        """Update the auto-refresh state based on current conditions"""
        if self._should_auto_refresh():
            self.start_auto_refresh()
        else:
            self.stop_auto_refresh()
            if self.code and self.date_str:
                if not is_trading_day(self.date_str):
                    self._update_refresh_status("非当日")
                elif not is_trading_time():
                    self._update_refresh_status("非交易时段")
    
    def _update_refresh_status(self, status: str):
        """Update the refresh status label"""
        self.refresh_status_label.setText(status)
    
    def showEvent(self, event):
        """Called when widget becomes visible"""
        super().showEvent(event)
        # Start auto-refresh when becoming visible (if applicable)
        self._update_auto_refresh_state()
    
    def hideEvent(self, event):
        """Called when widget becomes hidden"""
        super().hideEvent(event)
        # Stop auto-refresh when hidden to save resources
        self.stop_auto_refresh()
    
    def closeEvent(self, event):
        """Called when widget is closed"""
        self.stop_auto_refresh()
        super().closeEvent(event)


class TimeShareWindow(QDialog):
    """分时图弹窗"""
    def __init__(self, code: str, date_str: str, data_dir: str, parent=None, prev_close: float = None):
        super().__init__(parent)
        self.setWindowTitle(f"分时图 - {code} {date_str}")
        self.resize(600, 400)
        self.setStyleSheet("background-color: #1a1a1a; color: #ffffff;")
        
        # 设置窗口标志：独立窗口，带最小化/最大化按钮
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinMaxButtonsHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        self.widget = TimeShareWidget()
        layout.addWidget(self.widget)
        
        self.widget.load_data(code, date_str, data_dir, prev_close)
