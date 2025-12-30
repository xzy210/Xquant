# chip_distribution_widget.py - 筹码分布图组件
"""
基于 AkShare 的筹码分布图组件

功能：
- 获取并显示股票筹码分布
- 横向柱状图展示各价位筹码
- 显示获利比例、平均成本等关键指标
- 支持日期选择

数据源：AkShare - stock_cyq_em (东方财富)
"""
import logging
from typing import Optional
from datetime import datetime, date
import threading

import numpy as np
import pandas as pd

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QDateEdit, QFrame, QMessageBox,
    QDialog, QProgressBar, QApplication
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal, QThread
from PyQt6.QtGui import QColor

import pyqtgraph as pg

logger = logging.getLogger(__name__)

# 尝试导入 akshare
try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False
    ak = None
    logger.warning("akshare 未安装，筹码分布功能不可用")


class ChipDataLoader(QThread):
    """后台加载筹码数据的线程"""
    finished = pyqtSignal(object, str)  # data, error_msg
    
    def __init__(self, code: str, trade_date: str):
        super().__init__()
        self.code = code
        self.trade_date = trade_date
    
    def run(self):
        try:
            if not HAS_AKSHARE:
                self.finished.emit(None, "akshare 未安装")
                return
            
            # 调用 akshare 接口获取筹码分布
            # stock_cyq_em 接口参数: symbol (股票代码), adjust (复权: "", "qfq", "hfq")
            df = ak.stock_cyq_em(symbol=self.code, adjust="")
            
            if df is None or df.empty:
                self.finished.emit(None, f"未获取到 {self.code} 的筹码数据")
                return
            
            self.finished.emit(df, "")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"获取筹码数据失败: {error_msg}")
            self.finished.emit(None, f"获取数据失败: {error_msg}")


class ChipDistributionWidget(QWidget):
    """
    筹码分布图组件
    
    显示股票在各价位的筹码分布情况，类似通达信的筹码图
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.code = ""
        self.name = ""
        self.data: Optional[pd.DataFrame] = None
        self.current_price = 0.0
        self.loader_thread: Optional[ChipDataLoader] = None
        
        # 颜色配置
        self.profit_color = "#ff4d4d"    # 获利筹码（红色）
        self.loss_color = "#00b800"      # 套牢筹码（绿色）
        self.avg_line_color = "#ffdd00"  # 平均成本线（黄色）
        self.price_line_color = "#ffffff"  # 当前价格线（白色）
        
        self.setupUI()
    
    def setupUI(self):
        """设置界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # 顶部控制栏
        control_bar = QHBoxLayout()
        
        # 标题
        self.title_label = QLabel("筹码分布")
        self.title_label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
            }
        """)
        control_bar.addWidget(self.title_label)
        
        control_bar.addStretch()
        
        # 刷新按钮
        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.setFixedWidth(70)
        self.refresh_btn.clicked.connect(self.refresh_data)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                border: 1px solid #555;
                border-radius: 3px;
                color: #fff;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #3d3d3d;
            }
        """)
        control_bar.addWidget(self.refresh_btn)
        
        layout.addLayout(control_bar)
        
        # 指标信息栏
        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background-color: #252525;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
            }
        """)
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(10, 5, 10, 5)
        
        # 获利比例
        self.profit_ratio_label = QLabel("获利比例: --%")
        self.profit_ratio_label.setStyleSheet("color: #ff4d4d; font-size: 12px;")
        info_layout.addWidget(self.profit_ratio_label)
        
        info_layout.addSpacing(20)
        
        # 平均成本
        self.avg_cost_label = QLabel("平均成本: --")
        self.avg_cost_label.setStyleSheet("color: #ffdd00; font-size: 12px;")
        info_layout.addWidget(self.avg_cost_label)
        
        info_layout.addSpacing(20)
        
        # 90%成本区间
        self.cost_range_label = QLabel("90%成本: -- ~ --")
        self.cost_range_label.setStyleSheet("color: #aaaaaa; font-size: 12px;")
        info_layout.addWidget(self.cost_range_label)
        
        info_layout.addSpacing(20)
        
        # 集中度
        self.concentration_label = QLabel("集中度: --%")
        self.concentration_label.setStyleSheet("color: #aaaaaa; font-size: 12px;")
        info_layout.addWidget(self.concentration_label)
        
        info_layout.addStretch()
        
        layout.addWidget(info_frame)
        
        # 图表区域
        self.graphics_widget = pg.GraphicsLayoutWidget()
        self.graphics_widget.setBackground('#1a1a1a')
        layout.addWidget(self.graphics_widget, stretch=1)
        
        # 状态栏
        self.status_label = QLabel("请选择股票查看筹码分布")
        self.status_label.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(self.status_label)
        
        self.setup_plot()
    
    def setup_plot(self):
        """设置图表"""
        self.graphics_widget.clear()
        
        # 创建绘图区
        self.plot = self.graphics_widget.addPlot()
        self.plot.setLabel('left', '价格', color='#888888')
        self.plot.setLabel('bottom', '筹码占比 (%)', color='#888888')
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        
        # 隐藏自动范围按钮
        self.plot.hideButtons()
        
        # 设置轴样式
        for axis in ['left', 'bottom']:
            ax = self.plot.getAxis(axis)
            ax.setTextPen('#888888')
            ax.setPen('#555555')
    
    def load_data(self, code: str, name: str = "", current_price: float = 0.0):
        """
        加载筹码分布数据
        
        Args:
            code: 股票代码（6位）
            name: 股票名称
            current_price: 当前价格
        """
        self.code = code
        self.name = name
        self.current_price = current_price
        
        self.title_label.setText(f"筹码分布 - {name}({code})" if name else f"筹码分布 - {code}")
        self.status_label.setText(f"正在加载 {code} 筹码数据...")
        self.refresh_btn.setEnabled(False)
        
        # 清空当前图表
        self.plot.clear()
        
        # 启动后台加载
        today_str = date.today().strftime("%Y%m%d")
        self.loader_thread = ChipDataLoader(code, today_str)
        self.loader_thread.finished.connect(self._on_data_loaded)
        self.loader_thread.start()
    
    def _on_data_loaded(self, data: Optional[pd.DataFrame], error_msg: str):
        """数据加载完成回调"""
        self.refresh_btn.setEnabled(True)
        
        if error_msg:
            self.status_label.setText(f"⚠ {error_msg}")
            return
        
        if data is None or data.empty:
            self.status_label.setText("未获取到筹码数据")
            return
        
        self.data = data
        self.draw_chart()
        self.status_label.setText(f"✓ 已加载 {self.code} 筹码分布")
    
    def draw_chart(self):
        """绘制筹码分布图"""
        if self.data is None or self.data.empty:
            return
        
        self.plot.clear()
        
        try:
            # 解析数据
            # AkShare stock_cyq_em 返回的字段通常包含: price, percent 等
            df = self.data.copy()
            
            # 检查数据列
            # logger.info(f"筹码数据列: {df.columns.tolist()}")
            # logger.info(f"筹码数据前5行:\n{df.head()}")
            
            # 根据实际返回字段调整
            if '获利比例' in df.columns:
                # 东方财富格式
                prices = df['价格'].values if '价格' in df.columns else np.arange(len(df))
                percents = df['占比'].values if '占比' in df.columns else df.iloc[:, 1].values
                profit_ratio = df['获利比例'].iloc[0] if len(df) > 0 else 0
            elif 'price' in df.columns:
                prices = df['price'].values
                percents = df['percent'].values if 'percent' in df.columns else df['ratio'].values
                profit_ratio = None
            else:
                # 尝试使用第一列作为价格，第二列作为占比
                prices = df.iloc[:, 0].values
                percents = df.iloc[:, 1].values
                profit_ratio = None
            
            # 转换为数值
            prices = pd.to_numeric(prices, errors='coerce')
            percents = pd.to_numeric(percents, errors='coerce')
            
            # 移除无效数据
            valid_mask = ~(np.isnan(prices) | np.isnan(percents))
            prices = prices[valid_mask]
            percents = percents[valid_mask]
            
            if len(prices) == 0:
                self.status_label.setText("筹码数据解析失败")
                return
            
            # 计算获利/套牢筹码
            if self.current_price > 0:
                profit_mask = prices <= self.current_price
            else:
                # 如果没有当前价格，使用中位数
                self.current_price = np.median(prices)
                profit_mask = prices <= self.current_price
            
            # 绘制获利筹码（红色）
            profit_prices = prices[profit_mask]
            profit_percents = percents[profit_mask]
            if len(profit_prices) > 0:
                bar_profit = pg.BarGraphItem(
                    x0=np.zeros(len(profit_prices)),
                    y=profit_prices,
                    width=profit_percents,
                    height=(prices.max() - prices.min()) / len(prices) * 0.8,
                    brush=self.profit_color,
                    pen=None
                )
                self.plot.addItem(bar_profit)
            
            # 绘制套牢筹码（绿色）
            loss_prices = prices[~profit_mask]
            loss_percents = percents[~profit_mask]
            if len(loss_prices) > 0:
                bar_loss = pg.BarGraphItem(
                    x0=np.zeros(len(loss_prices)),
                    y=loss_prices,
                    width=loss_percents,
                    height=(prices.max() - prices.min()) / len(prices) * 0.8,
                    brush=self.loss_color,
                    pen=None
                )
                self.plot.addItem(bar_loss)
            
            # 绘制当前价格线
            if self.current_price > 0:
                price_line = pg.InfiniteLine(
                    pos=self.current_price,
                    angle=0,
                    pen=pg.mkPen(self.price_line_color, width=2, style=Qt.PenStyle.DashLine),
                    label=f'现价: {self.current_price:.2f}',
                    labelOpts={'color': self.price_line_color, 'position': 0.95}
                )
                self.plot.addItem(price_line)
            
            # 计算并显示指标
            self._update_indicators(prices, percents, profit_mask)
            
            # 设置Y轴范围（价格）
            price_range = prices.max() - prices.min()
            self.plot.setYRange(prices.min() - price_range * 0.05, prices.max() + price_range * 0.05)
            
            # 设置X轴范围（占比）
            self.plot.setXRange(0, percents.max() * 1.1)
            
        except Exception as e:
            logger.error(f"绘制筹码图失败: {e}")
            self.status_label.setText(f"绘制失败: {e}")
    
    def _update_indicators(self, prices: np.ndarray, percents: np.ndarray, profit_mask: np.ndarray):
        """更新指标信息"""
        try:
            # 获利比例
            profit_ratio = percents[profit_mask].sum() / percents.sum() * 100 if percents.sum() > 0 else 0
            self.profit_ratio_label.setText(f"获利比例: {profit_ratio:.1f}%")
            
            # 平均成本（加权平均）
            avg_cost = np.average(prices, weights=percents) if percents.sum() > 0 else 0
            self.avg_cost_label.setText(f"平均成本: {avg_cost:.2f}")
            
            # 绘制平均成本线
            if avg_cost > 0:
                avg_line = pg.InfiniteLine(
                    pos=avg_cost,
                    angle=0,
                    pen=pg.mkPen(self.avg_line_color, width=1.5, style=Qt.PenStyle.DotLine),
                    label=f'均成本: {avg_cost:.2f}',
                    labelOpts={'color': self.avg_line_color, 'position': 0.05}
                )
                self.plot.addItem(avg_line)
            
            # 90%成本区间（5%~95%分位）
            cumsum = np.cumsum(percents) / percents.sum()
            idx_5 = np.searchsorted(cumsum, 0.05)
            idx_95 = np.searchsorted(cumsum, 0.95)
            cost_low = prices[min(idx_5, len(prices)-1)]
            cost_high = prices[min(idx_95, len(prices)-1)]
            self.cost_range_label.setText(f"90%成本: {cost_low:.2f} ~ {cost_high:.2f}")
            
            # 集中度 = (95%价格 - 5%价格) / 平均成本
            if avg_cost > 0:
                concentration = (cost_high - cost_low) / avg_cost * 100
                self.concentration_label.setText(f"集中度: {concentration:.1f}%")
            
        except Exception as e:
            logger.error(f"计算指标失败: {e}")
    
    def refresh_data(self):
        """刷新数据"""
        if self.code:
            self.load_data(self.code, self.name, self.current_price)
    
    def set_current_price(self, price: float):
        """设置当前价格并重绘"""
        self.current_price = price
        if self.data is not None:
            self.draw_chart()


class ChipDistributionDialog(QDialog):
    """筹码分布弹窗"""
    
    def __init__(self, code: str, name: str = "", current_price: float = 0.0, parent=None):
        super().__init__(parent)
        
        self.setWindowTitle(f"筹码分布 - {name}({code})" if name else f"筹码分布 - {code}")
        self.resize(500, 600)
        self.setStyleSheet("background-color: #1a1a1a; color: #ffffff;")
        
        # 设置窗口标志
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinMaxButtonsHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        self.chip_widget = ChipDistributionWidget()
        layout.addWidget(self.chip_widget)
        
        # 加载数据
        self.chip_widget.load_data(code, name, current_price)

