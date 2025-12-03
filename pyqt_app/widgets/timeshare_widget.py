# timeshare_widget.py - 分时图组件
"""
分时图组件，用于显示单日的分时走势
通达信风格：白色价格线、黄色均价线、前收盘中位线
"""
import numpy as np
import pandas as pd
from typing import Optional
import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QMessageBox, QDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPen, QBrush

import pyqtgraph as pg

# 尝试导入 fetch_minute 模块
try:
    import sys
    import os
    # 添加项目根目录到 sys.path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(os.path.dirname(current_dir))
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)
        
    from fetch_minute import fetch_minute_data_with_cache
    from pathlib import Path
except ImportError:
    fetch_minute_data_with_cache = None


class TimeShareWidget(QWidget):
    """分时图组件 - 通达信风格"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.data: Optional[pd.DataFrame] = None
        self.code = ""
        self.date_str = ""
        self.prev_close: Optional[float] = None  # 前收盘价
        self.avg_prices: Optional[np.ndarray] = None  # 均价数组
        
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
        layout.addWidget(self.info_label)
        
        # 创建图形布局
        self.graphics_layout = pg.GraphicsLayoutWidget()
        self.graphics_layout.setBackground('#1a1a1a')
        layout.addWidget(self.graphics_layout, stretch=1)
        
        self.setup_plots()
    
    def setup_plots(self):
        """设置图表"""
        self.graphics_layout.clear()
        
        Y_AXIS_WIDTH = 60
        
        # 主图（价格）
        self.price_plot = self.graphics_layout.addPlot(row=0, col=0)
        self.price_plot.setLabel('left', '')
        self.price_plot.showGrid(x=True, y=True, alpha=0.2)
        self.price_plot.getAxis('left').setWidth(Y_AXIS_WIDTH)
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
        
        Args:
            code: 股票代码
            date_str: 日期字符串 YYYY-MM-DD
            data_dir: 数据目录路径
            prev_close: 前收盘价（可选）
        """
        self.code = code
        self.date_str = date_str
        self.prev_close = prev_close
        self.info_label.setText(f"正在加载 {code} {date_str} 分时数据...")
        
        # 强制刷新界面
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        
        if fetch_minute_data_with_cache is None:
            self.info_label.setText("错误: 无法加载 fetch_minute 模块，请确保已安装 akshare")
            return
            
        # 转换日期格式 YYYY-MM-DD -> YYYYMMDD
        date_param = date_str.replace("-", "")
        
        # 确保 data_dir 是 Path 对象
        data_path = Path(data_dir)
        
        # 获取数据
        try:
            self.info_label.setText(f"正在从网络获取 {code} {date_str} 分时数据...")
            QApplication.processEvents()
            
            df = fetch_minute_data_with_cache(
                code=code,
                trade_date=date_param,
                data_dir=data_path,
                freq="1",
                force_refresh=False  # 优先使用缓存
            )
            
            if df is None or df.empty:
                # 尝试强制刷新获取
                self.info_label.setText(f"本地无缓存，正在从 AkShare 拉取 {code} {date_str} 分时数据...")
                QApplication.processEvents()
                
                df = fetch_minute_data_with_cache(
                    code=code,
                    trade_date=date_param,
                    data_dir=data_path,
                    freq="1",
                    force_refresh=True  # 强制从网络获取
                )
            
            if df is None or df.empty:
                self.info_label.setText(f"未找到 {code} {date_str} 的分时数据（可能是非交易日或数据源暂不支持）")
                return
            
            self.data = df
            
            # 如果没有传入前收盘价，使用当日开盘价作为替代
            if self.prev_close is None:
                self.prev_close = df['open'].iloc[0]
            
            self.draw_chart()
            
            # 更新标题信息
            current_price = df['close'].iloc[-1]
            if self.prev_close and self.prev_close > 0:
                change_pct = (current_price - self.prev_close) / self.prev_close * 100
                pct_color = self.up_color if change_pct >= 0 else self.down_color
                self.info_label.setText(
                    f"<span style='color:#ffffff'>{code} {date_str}</span> | "
                    f"<span style='color:#ffffff'>现价: {current_price:.2f}</span> | "
                    f"涨跌: <span style='color:{pct_color}'>{change_pct:+.2f}%</span> | "
                    f"<span style='color:{self.avg_color}'>均价: {self.avg_prices[-1]:.2f}</span> | "
                    f"共 {len(df)} 条数据"
                )
            else:
                self.info_label.setText(f"{code} {date_str} 分时图 (共 {len(df)} 条数据)")
            
        except Exception as e:
            self.info_label.setText(f"加载失败: {str(e)}")
            import traceback
            traceback.print_exc()

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
        
        # 绘制成交量（颜色根据相对前收盘价涨跌）
        if self.prev_close and self.prev_close > 0:
            colors = [self.up_color if p >= self.prev_close else self.down_color for p in prices]
        else:
            # 如果没有前收盘价，根据相对前一分钟涨跌
            colors = [self.up_color]  # 第一根默认红色
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


class TimeShareWindow(QDialog):
    """分时图弹窗"""
    def __init__(self, code: str, date_str: str, data_dir: str, parent=None, prev_close: float = None):
        super().__init__(parent)
        self.setWindowTitle(f"分时图 - {code} {date_str}")
        self.resize(900, 650)
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
