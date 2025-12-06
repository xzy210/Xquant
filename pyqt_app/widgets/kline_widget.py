# kline_widget.py - K线图组件
"""
基于 pyqtgraph 的高性能K线图组件
"""
import numpy as np
import pandas as pd
from typing import List, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QCheckBox, QComboBox, QGroupBox, QMenu, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPen, QBrush, QAction

# 导入分时图窗口
from .timeshare_widget import TimeShareWindow
# 导入绘图工具
from .drawing_tools import DrawingManager

import pyqtgraph as pg


# 配置 pyqtgraph
pg.setConfigOptions(antialias=True)


class CandlestickItem(pg.GraphicsObject):
    """蜡烛图图形项"""
    
    def __init__(
        self,
        data: pd.DataFrame,
        up_color: str = "#ec0000",
        down_color: str = "#00da3c"
    ):
        """
        Args:
            data: 包含 open/high/low/close 的DataFrame（数值索引）
            up_color: 上涨颜色
            down_color: 下跌颜色
        """
        super().__init__()
        self.data = data
        self.up_color = QColor(up_color)
        self.down_color = QColor(down_color)
        self.picture = None
        self.generatePicture()
    
    def generatePicture(self):
        """预渲染K线图形"""
        from PyQt6.QtGui import QPicture, QPainter
        
        self.picture = QPicture()
        painter = QPainter(self.picture)
        
        w = 0.3  # K线宽度的一半（减小宽度避免重叠）
        
        for i in range(len(self.data)):
            row = self.data.iloc[i]
            o, h, l, c = row["open"], row["high"], row["low"], row["close"]
            
            if c >= o:
                color = self.up_color
            else:
                color = self.down_color
            
            pen = QPen(color)
            pen.setCosmetic(True)  # 使用装饰笔，宽度为像素单位
            pen.setWidthF(1.0)     # 1 像素宽
            pen.setCapStyle(Qt.PenCapStyle.FlatCap) # 使用平头端点，避免线条比矩形宽
            painter.setPen(pen)
            painter.setBrush(QBrush(color))
            
            # 绘制上下影线
            # 使用 int 坐标，避免浮点数坐标在 cosmetic 模式下的潜在问题
            # 注意：pg.Point 只是 QPointF 的别名，这里我们显式使用 QPointF
            painter.drawLine(
                pg.QtCore.QPointF(float(i), float(l)),
                pg.QtCore.QPointF(float(i), float(h))
            )
            
            # 绘制实体
            body_height = abs(c - o)
            
            if body_height < 0.001:
                # 一字板：画线
                # FlatCap 确保线条长度与矩形宽度一致
                painter.drawLine(
                    pg.QtCore.QPointF(float(i - w), float(c)),
                    pg.QtCore.QPointF(float(i + w), float(c))
                )
            else:
                # 有实体：画矩形（无边框）
                body_top = min(o, c)
                rect = pg.QtCore.QRectF(
                    float(i - w),
                    float(body_top),
                    float(w * 2),
                    float(body_height)
                )
                painter.setPen(Qt.PenStyle.NoPen)  # 禁用边框
                painter.fillRect(rect, QBrush(color))
                painter.setPen(pen)  # 恢复画笔
        
        painter.end()
    
    def paint(self, painter, *args):
        if self.picture:
            self.picture.play(painter)
    
    def boundingRect(self):
        if self.picture is None:
            return pg.QtCore.QRectF()
        return pg.QtCore.QRectF(self.picture.boundingRect())


class VolumeBarItem(pg.BarGraphItem):
    """成交量柱状图项"""
    
    def __init__(
        self,
        data: pd.DataFrame,
        up_color: str = "#ec0000",
        down_color: str = "#00da3c"
    ):
        """
        Args:
            data: 包含 open/close/volume 的DataFrame
            up_color: 上涨颜色
            down_color: 下跌颜色
        """
        x = np.arange(len(data))
        heights = data["volume"].values
        
        # 根据涨跌设置颜色
        brushes = []
        for i in range(len(data)):
            if data.iloc[i]["close"] >= data.iloc[i]["open"]:
                brushes.append(pg.mkBrush(up_color))
            else:
                brushes.append(pg.mkBrush(down_color))
        
        super().__init__(x=x, height=heights, width=0.6, brushes=brushes)


class KLineWidget(QWidget):
    """K线图组件"""
    
    # 信号：十字光标位置变化
    crosshairMoved = pyqtSignal(int, dict)  # 索引, 数据字典
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 设置焦点策略，以便接收键盘事件
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        self.data: Optional[pd.DataFrame] = None
        self.stock_code = ""
        self.stock_name = ""
        
        # 颜色配置
        self.up_color = "#ec0000"
        self.down_color = "#00da3c"
        self.ma_colors = ["#d62728", "#2ca02c", "#9467bd", "#8c564b", "#17becf"]
        
        # 设置
        self.ma_windows = [5, 10, 20]
        self.show_volume = True
        self.show_macd = True
        self.show_kdj = False
        
        self.last_click_idx = -1
        
        self.setupUI()
        
        # 初始化绘图管理器
        self.drawing_manager = DrawingManager(self)
        # 将绘图工具栏插入到布局顶部
        self.layout().insertWidget(0, self.drawing_manager.toolbar)
        
        # 浮动价格标签
        self.price_label = QLabel(self)
        self.price_label.setStyleSheet("""
            QLabel {
                background-color: #1e1e1e;
                color: #ffffff;
                padding: 2px;
                font-family: Arial;
                font-size: 10px;
                border: 1px solid #555555;
            }
        """)
        self.price_label.hide()

    def keyPressEvent(self, event):
        """处理键盘事件"""
        if event.key() == Qt.Key.Key_Delete:
            if hasattr(self, 'drawing_manager'):
                self.drawing_manager.delete_selection()
        else:
            super().keyPressEvent(event)

    def setupUI(self):
        """设置界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        # 信息栏
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("""
            QLabel {
                background-color: #1e1e1e;
                color: #ffffff;
                padding: 5px 10px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
        """)
        # 设置大小策略，防止文本变化导致布局抖动
        self.info_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.info_label.setMinimumWidth(1)
        
        layout.addWidget(self.info_label)
        
        # 创建图形布局
        self.graphics_layout = pg.GraphicsLayoutWidget()
        self.graphics_layout.setBackground('#1e1e1e')
        layout.addWidget(self.graphics_layout, stretch=1)
        
        # 创建各个子图
        self.setup_plots()
    
    def setup_plots(self):
        """设置图表区域"""
        # 清空现有图表
        self.graphics_layout.clear()
        
        # 固定Y轴宽度，确保所有子图右侧对齐
        Y_AXIS_WIDTH = 60
        
        # 主图（K线 + 均线）
        self.price_plot = self.graphics_layout.addPlot(row=0, col=0)
        self.price_plot.showAxis('right')
        self.price_plot.hideAxis('left')
        self.price_plot.setLabel('right', '价格')
        self.price_plot.showGrid(x=True, y=True, alpha=0.3)
        self.price_plot.setMinimumHeight(300)
        self.price_plot.getAxis('right').setWidth(Y_AXIS_WIDTH)
        
        # 成交量图
        if self.show_volume:
            self.volume_plot = self.graphics_layout.addPlot(row=1, col=0)
            self.volume_plot.showAxis('right')
            self.volume_plot.hideAxis('left')
            self.volume_plot.setLabel('right', '成交量')
            self.volume_plot.showGrid(x=True, y=True, alpha=0.3)
            self.volume_plot.setMaximumHeight(100)
            self.volume_plot.setXLink(self.price_plot)
            self.volume_plot.getAxis('right').setWidth(Y_AXIS_WIDTH)
        else:
            self.volume_plot = None
        
        # MACD图
        if self.show_macd:
            row_idx = 2 if self.show_volume else 1
            self.macd_plot = self.graphics_layout.addPlot(row=row_idx, col=0)
            self.macd_plot.showAxis('right')
            self.macd_plot.hideAxis('left')
            self.macd_plot.setLabel('right', 'MACD')
            self.macd_plot.showGrid(x=True, y=True, alpha=0.3)
            self.macd_plot.setMaximumHeight(120)
            self.macd_plot.setXLink(self.price_plot)
            self.macd_plot.getAxis('right').setWidth(Y_AXIS_WIDTH)
        else:
            self.macd_plot = None
        
        # KDJ图
        if self.show_kdj:
            row_idx = 1
            if self.show_volume:
                row_idx += 1
            if self.show_macd:
                row_idx += 1
            self.kdj_plot = self.graphics_layout.addPlot(row=row_idx, col=0)
            self.kdj_plot.showAxis('right')
            self.kdj_plot.hideAxis('left')
            self.kdj_plot.setLabel('right', 'KDJ')
            self.kdj_plot.showGrid(x=True, y=True, alpha=0.3)
            self.kdj_plot.setMaximumHeight(100)
            self.kdj_plot.setXLink(self.price_plot)
            self.kdj_plot.getAxis('right').setWidth(Y_AXIS_WIDTH)
        else:
            self.kdj_plot = None
        
        # 设置十字光标
        self.setup_crosshair()
        
        # 禁用 pyqtgraph 默认的右键菜单
        self.price_plot.vb.setMenuEnabled(False)
        if self.volume_plot:
            self.volume_plot.vb.setMenuEnabled(False)
        if self.macd_plot:
            self.macd_plot.vb.setMenuEnabled(False)
        if self.kdj_plot:
            self.kdj_plot.vb.setMenuEnabled(False)
        
        # 使用 pyqtgraph 的场景鼠标点击事件来实现右键菜单
        self.price_plot.scene().sigMouseClicked.connect(self.on_mouse_clicked)

    def on_mouse_clicked(self, evt):
        """处理鼠标点击事件"""
        # 如果正在绘图，不处理右键菜单
        if hasattr(self, 'drawing_manager') and self.drawing_manager.is_drawing_active:
            return

        # 只处理右键点击
        if evt.button() != Qt.MouseButton.RightButton:
            return
            
        if self.data is None:
            return
        
        # 获取点击位置
        pos = evt.scenePos()
        
        # 检查是否在主图区域内
        if self.price_plot.sceneBoundingRect().contains(pos):
            mouse_point = self.price_plot.vb.mapSceneToView(pos)
            x = int(round(mouse_point.x()))
            
            if 0 <= x < len(self.data):
                self.last_click_idx = x
                self.show_context_menu(evt.screenPos())

    def show_context_menu(self, screen_pos):
        """显示右键菜单"""
        if self.data is None:
            return
            
        if self.last_click_idx < 0 or self.last_click_idx >= len(self.data):
            return
        
        # 获取当前K线的日期信息
        row = self.data.iloc[self.last_click_idx]
        date_str = row["date"].strftime("%Y-%m-%d")
            
        menu = QMenu(self)
        action_minute = QAction(f"查看 {date_str} 分时图", self)
        action_minute.triggered.connect(self.show_minute_chart)
        menu.addAction(action_minute)
        
        # 使用屏幕坐标显示菜单
        from PyQt6.QtCore import QPoint
        menu.exec(QPoint(int(screen_pos.x()), int(screen_pos.y())))

    def show_minute_chart(self):
        """显示分时图"""
        if self.last_click_idx < 0 or self.last_click_idx >= len(self.data):
            return
            
        row = self.data.iloc[self.last_click_idx]
        date_str = row["date"].strftime("%Y-%m-%d")
        
        # 获取前一天的收盘价（用于计算涨跌幅）
        prev_close = None
        if self.last_click_idx > 0:
            prev_row = self.data.iloc[self.last_click_idx - 1]
            prev_close = prev_row["close"]
        else:
            # 如果是第一天，使用当天开盘价
            prev_close = row["open"]
        
        # 获取数据目录 - 使用绝对路径
        from pathlib import Path
        
        # 基于当前文件位置计算项目根目录
        current_file = Path(__file__).resolve()
        pyqt_app_dir = current_file.parent.parent  # pyqt_app 目录
        project_root = pyqt_app_dir.parent  # 项目根目录
        data_dir = project_root / "data"
        
        if not data_dir.exists():
            # 尝试其他可能的路径
            data_dir = Path.cwd() / "data"
            
        # 创建并显示窗口
        # 注意：我们需要保持窗口引用，否则会被垃圾回收
        self.minute_window = TimeShareWindow(
            self.stock_code, date_str, str(data_dir), self, prev_close=prev_close
        )
        self.minute_window.show()
    
    def setup_crosshair(self):
        """设置十字光标"""
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#888888', width=1))
        self.hLine = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('#888888', width=1))
        
        self.price_plot.addItem(self.vLine, ignoreBounds=True)
        self.price_plot.addItem(self.hLine, ignoreBounds=True)
        
        # 连接鼠标移动事件
        self.price_plot.scene().sigMouseMoved.connect(self.on_mouse_moved)
    
    def on_mouse_moved(self, pos):
        """处理鼠标移动"""
        if self.data is None:
            self.price_label.hide()
            return
        
        if self.price_plot.sceneBoundingRect().contains(pos):
            mouse_point = self.price_plot.vb.mapSceneToView(pos)
            x = int(round(mouse_point.x()))
            y_value = mouse_point.y()
            
            if 0 <= x < len(self.data):
                self.vLine.setPos(x)
                self.hLine.setPos(y_value)
                
                # 更新信息栏
                row = self.data.iloc[x]
                self.update_info_label(x, row)
                
                # 更新浮动价格标签
                self.update_floating_label(y_value, pos)
                
                # 记录当前索引，供右键菜单使用
                self.last_click_idx = x
            else:
                self.price_label.hide()
        else:
            self.price_label.hide()

    def update_floating_label(self, price, scene_pos):
        """更新浮动价格标签位置和内容"""
        # 设置文本
        self.price_label.setText(f"{price:.2f}")
        self.price_label.adjustSize()
        
        # 计算位置
        # 将 Scene 坐标转换为 View 坐标 (QPoint)
        view_pos = self.graphics_layout.mapFromScene(scene_pos)
        
        # 转换为全局坐标再转回本地坐标，确保位置准确
        global_pos = self.graphics_layout.mapToGlobal(view_pos)
        local_pos = self.mapFromGlobal(global_pos)
        
        # X 轴位置：靠右对齐，留一点边距
        # 确保标签显示在右侧坐标轴区域
        x = self.width() - self.price_label.width()
        
        # Y 轴位置：中心对齐鼠标位置
        y = local_pos.y() - self.price_label.height() / 2
        
        self.price_label.move(int(x), int(y))
        self.price_label.show()
        self.price_label.raise_()
    
    def update_info_label(self, idx: int, row: pd.Series):
        """更新信息栏"""
        date_str = row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else ""
        
        # 计算涨跌幅
        change_pct = 0
        if idx > 0:
            prev_close = self.data.iloc[idx - 1]["close"]
            if prev_close > 0:
                change_pct = (row["close"] - prev_close) / prev_close * 100
        
        color = self.up_color if row["close"] >= row["open"] else self.down_color
        
        info_text = (
            f"<span style='color: #ffffff;'>{self.stock_code} {self.stock_name}</span> | "
            f"<span style='color: #aaaaaa;'>{date_str}</span> | "
            f"开: <span style='color: {color};'>{row['open']:.2f}</span> | "
            f"高: <span style='color: {color};'>{row['high']:.2f}</span> | "
            f"低: <span style='color: {color};'>{row['low']:.2f}</span> | "
            f"收: <span style='color: {color};'>{row['close']:.2f}</span> | "
            f"涨跌: <span style='color: {color};'>{change_pct:+.2f}%</span> | "
            f"量: <span style='color: #aaaaaa;'>{row['volume']/10000:.0f}万</span>"
        )
        
        # 添加均线信息
        for i, w in enumerate(self.ma_windows):
            ma_col = f"MA{w}"
            if ma_col in row and pd.notna(row[ma_col]):
                ma_color = self.ma_colors[i % len(self.ma_colors)]
                info_text += f" | <span style='color: {ma_color};'>MA{w}: {row[ma_col]:.2f}</span>"
        
        self.info_label.setText(info_text)
    
    def set_data(
        self,
        data: pd.DataFrame,
        code: str = "",
        name: str = ""
    ):
        """
        设置K线数据
        
        Args:
            data: 包含 date/open/high/low/close/volume 及指标的DataFrame
            code: 股票代码
            name: 股票名称
        """
        self.data = data.copy()
        self.stock_code = code
        self.stock_name = name
        
        self.update_chart()
        
        # 默认显示最后一天的数据
        if not self.data.empty:
            last_idx = len(self.data) - 1
            self.update_info_label(last_idx, self.data.iloc[last_idx])
        
        # 恢复绘图
        if hasattr(self, 'drawing_manager'):
            self.drawing_manager.restore_drawings()
    
    def update_chart(self):
        """更新图表"""
        if self.data is None or self.data.empty:
            return
        
        # 重新设置图表区域
        self.setup_plots()
        
        # 更新绘图管理器的 PlotItem 引用
        if hasattr(self, 'drawing_manager'):
            self.drawing_manager.update_plot_item(self.price_plot)
        
        # 绘制K线
        self.draw_candlesticks()
        
        # 绘制均线
        self.draw_ma_lines()
        
        # 绘制成交量
        if self.show_volume and self.volume_plot:
            self.draw_volume()
        
        # 绘制MACD
        if self.show_macd and self.macd_plot:
            self.draw_macd()
        
        # 绘制KDJ
        if self.show_kdj and self.kdj_plot:
            self.draw_kdj()
        
        # 设置X轴范围（默认显示最近60天）
        data_len = len(self.data)
        visible_count = min(60, data_len)
        
        # 连接 X 轴变化信号，实现自动 Y 轴缩放
        # 断开旧的连接以防止重复连接
        try:
            self.price_plot.vb.sigXRangeChanged.disconnect(self.update_y_range)
        except Exception:
            pass
        self.price_plot.vb.sigXRangeChanged.connect(self.update_y_range)

        self.price_plot.setXRange(data_len - visible_count, data_len, padding=0.02)
        
        # 设置X轴日期标签
        self.setup_x_axis()
        
        # 初始自动缩放
        self.update_y_range()

    def update_y_range(self):
        """根据当前可见 X 轴范围，自动调整 Y 轴范围"""
        if self.data is None or self.data.empty:
            return

        # 获取可见 X 轴范围
        view_range = self.price_plot.viewRange()
        min_x, max_x = view_range[0]
        
        # 转换为整数索引
        start_idx = max(0, int(min_x))
        end_idx = min(len(self.data), int(max_x) + 1)
        
        if start_idx >= end_idx:
            return

        # 获取该范围内的数据
        subset = self.data.iloc[start_idx:end_idx]
        if subset.empty:
            return

        # 1. 更新主图 Y 轴 (K线 + 均线)
        # 基础价格
        highs = subset["high"]
        lows = subset["low"]
        
        min_y = lows.min()
        max_y = highs.max()
        
        # 考虑均线
        for w in self.ma_windows:
            ma_col = f"MA{w}"
            if ma_col in subset.columns:
                valid_ma = subset[ma_col].dropna()
                if not valid_ma.empty:
                    min_y = min(min_y, valid_ma.min())
                    max_y = max(max_y, valid_ma.max())
        
        if not pd.isna(min_y) and not pd.isna(max_y):
            # 添加一点 padding
            padding = (max_y - min_y) * 0.05 if max_y != min_y else max_y * 0.01
            self.price_plot.setYRange(min_y - padding, max_y + padding, padding=0)

        # 2. 更新成交量图 Y 轴
        if self.show_volume and self.volume_plot and "volume" in subset.columns:
            vol_max = subset["volume"].max()
            if "VOL_MA5" in subset.columns:
                valid_vma = subset["VOL_MA5"].dropna()
                if not valid_vma.empty:
                    vol_max = max(vol_max, valid_vma.max())
            
            if not pd.isna(vol_max):
                self.volume_plot.setYRange(0, vol_max * 1.05, padding=0)

        # 3. 更新 MACD 图 Y 轴
        if self.show_macd and self.macd_plot and "MACD" in subset.columns:
            vals = []
            if "MACD" in subset.columns: vals.append(subset["MACD"])
            if "DIF" in subset.columns: vals.append(subset["DIF"])
            if "DEA" in subset.columns: vals.append(subset["DEA"])
            
            current_min, current_max = float('inf'), float('-inf')
            has_data = False
            for s in vals:
                valid_s = s.dropna()
                if not valid_s.empty:
                    current_min = min(current_min, valid_s.min())
                    current_max = max(current_max, valid_s.max())
                    has_data = True
            
            if has_data:
                # 对称显示或自动范围
                # 这里使用自动范围
                padding = (current_max - current_min) * 0.05 if current_max != current_min else 0.1
                self.macd_plot.setYRange(current_min - padding, current_max + padding, padding=0)

        # 4. 更新 KDJ 图 Y 轴
        if self.show_kdj and self.kdj_plot:
            vals = []
            if "K" in subset.columns: vals.append(subset["K"])
            if "D" in subset.columns: vals.append(subset["D"])
            if "J" in subset.columns: vals.append(subset["J"])
            
            current_min, current_max = float('inf'), float('-inf')
            has_data = False
            for s in vals:
                valid_s = s.dropna()
                if not valid_s.empty:
                    current_min = min(current_min, valid_s.min())
                    current_max = max(current_max, valid_s.max())
                    has_data = True
            
            if has_data:
                padding = (current_max - current_min) * 0.05 if current_max != current_min else 5
                self.kdj_plot.setYRange(current_min - padding, current_max + padding, padding=0)

    
    def draw_candlesticks(self):
        """绘制K线"""
        candle_item = CandlestickItem(
            self.data,
            up_color=self.up_color,
            down_color=self.down_color
        )
        self.price_plot.addItem(candle_item)
    
    def draw_ma_lines(self):
        """绘制均线"""
        x = np.arange(len(self.data))
        
        for i, w in enumerate(self.ma_windows):
            ma_col = f"MA{w}"
            if ma_col in self.data.columns:
                color = self.ma_colors[i % len(self.ma_colors)]
                y = self.data[ma_col].values
                
                # 过滤掉NaN值
                valid_mask = ~np.isnan(y)
                if valid_mask.any():
                    pen = pg.mkPen(color, width=1.5)
                    self.price_plot.plot(
                        x[valid_mask], y[valid_mask],
                        pen=pen, name=f"MA{w}"
                    )
    
    def draw_volume(self):
        """绘制成交量"""
        if self.volume_plot is None:
            return
        
        vol_item = VolumeBarItem(
            self.data,
            up_color=self.up_color,
            down_color=self.down_color
        )
        self.volume_plot.addItem(vol_item)
        
        # 成交量均线
        if "VOL_MA5" in self.data.columns:
            x = np.arange(len(self.data))
            y = self.data["VOL_MA5"].values
            valid_mask = ~np.isnan(y)
            if valid_mask.any():
                pen = pg.mkPen('#555555', width=1)
                self.volume_plot.plot(x[valid_mask], y[valid_mask], pen=pen)
    
    def draw_macd(self):
        """绘制MACD"""
        if self.macd_plot is None:
            return
        
        if "MACD" not in self.data.columns:
            return
        
        x = np.arange(len(self.data))
        macd_vals = self.data["MACD"].values
        
        # MACD柱
        colors = [
            self.up_color if v >= 0 else self.down_color
            for v in macd_vals
        ]
        brushes = [pg.mkBrush(c) for c in colors]
        
        bar_item = pg.BarGraphItem(
            x=x, height=macd_vals, width=0.6, brushes=brushes
        )
        self.macd_plot.addItem(bar_item)
        
        # DIF线
        if "DIF" in self.data.columns:
            y = self.data["DIF"].values
            valid_mask = ~np.isnan(y)
            if valid_mask.any():
                pen = pg.mkPen('#e377c2', width=1.2)
                self.macd_plot.plot(x[valid_mask], y[valid_mask], pen=pen, name="DIF")
        
        # DEA线
        if "DEA" in self.data.columns:
            y = self.data["DEA"].values
            valid_mask = ~np.isnan(y)
            if valid_mask.any():
                pen = pg.mkPen('#1f77b4', width=1.2)
                self.macd_plot.plot(x[valid_mask], y[valid_mask], pen=pen, name="DEA")
        
        # 添加零线
        self.macd_plot.addItem(
            pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen('#555555', width=1, style=Qt.PenStyle.DashLine))
        )
    
    def draw_kdj(self):
        """绘制KDJ"""
        if self.kdj_plot is None:
            return
        
        x = np.arange(len(self.data))
        
        # K线
        if "K" in self.data.columns:
            y = self.data["K"].values
            valid_mask = ~np.isnan(y)
            if valid_mask.any():
                pen = pg.mkPen('#f1c40f', width=1.2)
                self.kdj_plot.plot(x[valid_mask], y[valid_mask], pen=pen, name="K")
        
        # D线
        if "D" in self.data.columns:
            y = self.data["D"].values
            valid_mask = ~np.isnan(y)
            if valid_mask.any():
                pen = pg.mkPen('#3498db', width=1.2)
                self.kdj_plot.plot(x[valid_mask], y[valid_mask], pen=pen, name="D")
        
        # J线
        if "J" in self.data.columns:
            y = self.data["J"].values
            valid_mask = ~np.isnan(y)
            if valid_mask.any():
                pen = pg.mkPen('#8e44ad', width=1.2)
                self.kdj_plot.plot(x[valid_mask], y[valid_mask], pen=pen, name="J")
    
    def setup_x_axis(self):
        """设置X轴日期标签"""
        if self.data is None:
            return
        
        # 创建日期-索引映射
        dates = self.data["date"].values
        
        # 每隔一定数量显示一个标签
        step = max(1, len(dates) // 10)
        
        ticks = []
        for i in range(0, len(dates), step):
            date_str = pd.Timestamp(dates[i]).strftime("%m-%d")
            ticks.append((i, date_str))
        
        # 设置X轴
        x_axis = self.price_plot.getAxis('bottom')
        x_axis.setTicks([ticks])
    
    def set_indicators(
        self,
        show_volume: bool = True,
        show_macd: bool = True,
        show_kdj: bool = False
    ):
        """
        设置显示的指标
        
        Args:
            show_volume: 是否显示成交量
            show_macd: 是否显示MACD
            show_kdj: 是否显示KDJ
        """
        self.show_volume = show_volume
        self.show_macd = show_macd
        self.show_kdj = show_kdj
        
        if self.data is not None:
            self.update_chart()
    
    def set_ma_windows(self, windows: List[int]):
        """
        设置均线周期
        
        Args:
            windows: 均线周期列表
        """
        self.ma_windows = windows
        
        if self.data is not None:
            self.update_chart()
