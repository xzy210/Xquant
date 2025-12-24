# etf_grid_widget.py - ETF Grid Trading Widget
"""
ETF Grid Trading Strategy UI Widget

Features:
- Parameter configuration panel
- Grid visualization
- Real-time signal monitoring  
- Backtest with performance charts
- Trade history display
"""

import sys
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime

import pandas as pd
import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QGroupBox, QFormLayout, QSplitter, QFrame, QProgressBar,
    QCheckBox, QScrollArea, QSizePolicy, QGridLayout,
    QTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QColor, QBrush, QFont, QPainter, QPen

# Import pyqtgraph for charting
import pyqtgraph as pg
from pyqtgraph import PlotWidget, InfiniteLine

# Import strategy
sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies.etf_grid_strategy import (
    ETFGridStrategy, GridConfig, GridType, SignalType,
    create_default_etf_config
)
from data_loader import load_etf_data, load_etf_name_map, get_etf_list


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
        
        self.setup_ui()
        self.load_etf_list()
    
    def setup_ui(self):
        """Initialize UI"""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # Left panel: Configuration and controls
        left_panel = QWidget()
        left_panel.setFixedWidth(320)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        
        # ETF Selection
        etf_group = QGroupBox("ETF选择")
        etf_layout = QFormLayout(etf_group)
        
        self.etf_combo = QComboBox()
        self.etf_combo.setMinimumWidth(180)
        self.etf_combo.currentIndexChanged.connect(self.on_etf_changed)
        etf_layout.addRow("ETF:", self.etf_combo)
        
        self.etf_type_combo = QComboBox()
        self.etf_type_combo.addItems(["宽基指数", "行业主题", "商品", "债券"])
        self.etf_type_combo.currentIndexChanged.connect(self.on_etf_type_changed)
        etf_layout.addRow("类型:", self.etf_type_combo)
        
        left_layout.addWidget(etf_group)
        
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
        
        main_layout.addWidget(left_panel)
        
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
        
        # Tab 4: Daily Stats
        daily_tab = QWidget()
        daily_layout = QVBoxLayout(daily_tab)
        
        self.daily_table = QTableWidget()
        self.daily_table.setColumnCount(7)
        self.daily_table.setHorizontalHeaderLabels([
            "日期", "价格", "持仓", "持仓市值", "总资产", "收益率%", "仓位%"
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
        daily_layout.addWidget(self.daily_table)
        
        self.result_tabs.addTab(daily_tab, "每日统计")
        
        right_layout.addWidget(self.result_tabs)
        
        main_layout.addWidget(right_panel, stretch=1)
    
    def load_etf_list(self):
        """Load ETF list"""
        self.etf_name_map = load_etf_name_map()
        etf_codes = get_etf_list(self.data_dir)
        
        self.etf_combo.clear()
        for code in etf_codes:
            name = self.etf_name_map.get(code, code)
            self.etf_combo.addItem(f"{code} {name}", code)
        
        # Add some default ETFs if no data
        if self.etf_combo.count() == 0:
            default_etfs = [
                ("510300", "沪深300ETF"),
                ("510500", "中证500ETF"),
                ("159915", "创业板ETF"),
            ]
            for code, name in default_etfs:
                self.etf_combo.addItem(f"{code} {name}", code)
    
    def on_etf_changed(self, index):
        """Handle ETF selection change"""
        code = self.etf_combo.currentData()
        if code:
            self.current_data = load_etf_data(code, self.data_dir)
            if self.current_data is not None and not self.current_data.empty:
                # Update grid visualization with current price
                current_price = self.current_data.iloc[-1]['close']
                self.update_status(f"已加载 {code} 数据，共 {len(self.current_data)} 条记录，最新价格: {current_price:.3f}")
    
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
            self.current_data = load_etf_data(code, self.data_dir)
            if self.current_data is None or self.current_data.empty:
                QMessageBox.warning(self, "错误", f"无法加载 {code} 的数据，请先更新ETF数据")
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
        
        # Display results
        self.display_results(results)
        
        # Update grid visualization
        if self.strategy:
            grids = self.strategy.get_grids()
            current_price = self.current_data.iloc[-1]['close'] if self.current_data is not None else 0
            self.grid_visual.set_data(grids, current_price, self.strategy.state.base_price)
            
            # Update grid info
            self.update_grid_info(grids)
        
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
