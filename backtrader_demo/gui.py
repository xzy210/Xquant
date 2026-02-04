"""
Backtrader Demo - Interactive GUI
==================================
A comprehensive PyQt6 GUI for running backtrader backtests interactively.
Features rich performance metrics and multiple chart visualizations.
"""

import sys
import numpy as np
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QSpinBox, QDoubleSpinBox,
    QDateEdit, QTextEdit, QGroupBox, QMessageBox, QSplitter,
    QProgressBar, QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QScrollArea,
)
from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor

import backtrader as bt
import matplotlib
matplotlib.use('QtAgg')
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial']
matplotlib.rcParams['axes.unicode_minus'] = False
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

# Import local modules
from data_loader import (
    create_bt_data_feed,
    get_available_stocks,
    load_stock_data_for_bt,
    PROJECT_ROOT,
)
from strategies import STRATEGIES


class TradeRecorder(bt.Analyzer):
    """Custom analyzer to record all trades with details"""
    
    def __init__(self):
        self.trades = []
        self.open_trades = {}  # Track open trades by ref
    
    def notify_trade(self, trade):
        # When trade is opened, record the size and direction
        if trade.isopen:
            self.open_trades[trade.ref] = {
                'size': trade.size,  # Positive for long, negative for short
                'open_price': trade.price,
            }
        
        # When trade is closed, calculate final values
        if trade.isclosed:
            # Get the original trade info
            open_info = self.open_trades.get(trade.ref, {})
            original_size = open_info.get('size', 0)
            open_price = open_info.get('open_price', trade.price)
            
            # Calculate close price from PnL
            # For long: pnl = (close_price - open_price) * size
            # For short: pnl = (open_price - close_price) * abs(size)
            if original_size != 0:
                if original_size > 0:  # Long position
                    close_price = open_price + trade.pnl / abs(original_size)
                else:  # Short position
                    close_price = open_price - trade.pnl / abs(original_size)
                pnl_pct = (trade.pnl / (open_price * abs(original_size))) * 100
            else:
                close_price = 0
                pnl_pct = 0
            
            self.trades.append({
                'ref': trade.ref,
                'open_date': bt.num2date(trade.dtopen),
                'close_date': bt.num2date(trade.dtclose),
                'size': original_size,  # Use the original size (with direction)
                'open_price': open_price,
                'close_price': close_price,
                'pnl': trade.pnl,
                'pnl_pct': pnl_pct,
                'commission': trade.commission,
                'pnl_net': trade.pnlcomm,
                'bars': trade.barclose - trade.baropen,
            })
            
            # Clean up
            if trade.ref in self.open_trades:
                del self.open_trades[trade.ref]
    
    def get_analysis(self):
        return self.trades


class BacktestThread(QThread):
    """Thread for running backtest in background"""
    finished = pyqtSignal(object, object)  # (results, cerebro)
    log_message = pyqtSignal(str)
    
    def __init__(self, cerebro, stock_code):
        super().__init__()
        self.cerebro = cerebro
        self.stock_code = stock_code
    
    def run(self):
        try:
            self.log_message.emit(f"Running backtest for {self.stock_code}...")
            results = self.cerebro.run()
            self.finished.emit(results, self.cerebro)
        except Exception as e:
            self.log_message.emit(f"Error: {str(e)}")
            self.finished.emit(None, None)


class MetricCard(QFrame):
    """A styled card widget for displaying a single metric"""
    
    def __init__(self, title: str, value: str = "-", color: str = "#333"):
        super().__init__()
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setStyleSheet(f"""
            MetricCard {{
                background-color: white;
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 10px;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #666; font-size: 11px;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: bold;")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
    
    def set_value(self, value: str, color: str = None):
        self.value_label.setText(value)
        if color:
            self.value_label.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: bold;")


class BacktestGUI(QMainWindow):
    """Main GUI window for backtrader demo"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Backtrader Demo - 策略回测")
        self.setMinimumSize(1400, 900)
        
        # Get available stocks
        self.stocks = get_available_stocks()
        
        # Store backtest data
        self.equity_data = None
        self.trade_records = None
        self.drawdown_data = None
        self.last_cerebro = None  # Store cerebro for native plot
        
        self.init_ui()
    
    def init_ui(self):
        """Initialize the UI"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        
        # Left panel - Parameters
        left_panel = self.create_param_panel()
        
        # Right panel - Results
        right_panel = self.create_result_panel()
        
        # Use splitter for resizable panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([280, 1120])
        
        main_layout.addWidget(splitter)
    
    def create_param_panel(self) -> QWidget:
        """Create parameter configuration panel"""
        panel = QWidget()
        panel.setMaximumWidth(350)
        layout = QVBoxLayout(panel)
        
        # Stock selection
        stock_group = QGroupBox("股票选择")
        stock_layout = QVBoxLayout(stock_group)
        
        self.stock_combo = QComboBox()
        self.stock_combo.addItems(self.stocks)
        if "000001" in self.stocks:
            self.stock_combo.setCurrentText("000001")
        self.stock_combo.setEditable(True)
        stock_layout.addWidget(QLabel("股票代码:"))
        stock_layout.addWidget(self.stock_combo)
        
        layout.addWidget(stock_group)
        
        # Strategy selection
        strategy_group = QGroupBox("策略选择")
        strategy_layout = QVBoxLayout(strategy_group)
        
        self.strategy_combo = QComboBox()
        for name, cls in STRATEGIES.items():
            desc = cls.__doc__.split("\n")[1].strip() if cls.__doc__ else name
            self.strategy_combo.addItem(f"{name} - {desc}", name)
        strategy_layout.addWidget(self.strategy_combo)
        
        layout.addWidget(strategy_group)
        
        # Date range
        date_group = QGroupBox("日期范围")
        date_layout = QVBoxLayout(date_group)
        
        date_layout.addWidget(QLabel("开始日期:"))
        self.start_date = QDateEdit()
        self.start_date.setDate(QDate(2020, 1, 1))
        self.start_date.setCalendarPopup(True)
        date_layout.addWidget(self.start_date)
        
        date_layout.addWidget(QLabel("结束日期:"))
        self.end_date = QDateEdit()
        self.end_date.setDate(QDate.currentDate())
        self.end_date.setCalendarPopup(True)
        date_layout.addWidget(self.end_date)
        
        layout.addWidget(date_group)
        
        # Capital settings
        capital_group = QGroupBox("资金设置")
        capital_layout = QVBoxLayout(capital_group)
        
        capital_layout.addWidget(QLabel("初始资金:"))
        self.cash_spin = QSpinBox()
        self.cash_spin.setRange(10000, 10000000)
        self.cash_spin.setValue(100000)
        self.cash_spin.setSingleStep(10000)
        self.cash_spin.setSuffix(" 元")
        capital_layout.addWidget(self.cash_spin)
        
        capital_layout.addWidget(QLabel("手续费率:"))
        self.commission_spin = QDoubleSpinBox()
        self.commission_spin.setRange(0, 0.01)
        self.commission_spin.setValue(0.001)
        self.commission_spin.setSingleStep(0.0001)
        self.commission_spin.setDecimals(4)
        self.commission_spin.setSuffix(" (0.1%)")
        capital_layout.addWidget(self.commission_spin)
        
        layout.addWidget(capital_group)
        
        # Options
        options_group = QGroupBox("选项")
        options_layout = QVBoxLayout(options_group)
        
        self.show_trades_check = QCheckBox("控制台输出交易详情")
        self.show_trades_check.setChecked(False)
        options_layout.addWidget(self.show_trades_check)
        
        layout.addWidget(options_group)
        
        # Run button
        self.run_button = QPushButton("🚀 运行回测")
        self.run_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 12px;
                font-size: 14px;
                font-weight: bold;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.run_button.clicked.connect(self.run_backtest)
        layout.addWidget(self.run_button)
        
        # Native plot button
        self.native_plot_button = QPushButton("📊 显示原生Backtrader图表")
        self.native_plot_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 10px;
                font-size: 13px;
                font-weight: bold;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.native_plot_button.clicked.connect(self.show_native_plot)
        self.native_plot_button.setEnabled(False)  # Disabled until backtest is run
        layout.addWidget(self.native_plot_button)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        layout.addStretch()
        
        return panel
    
    def create_result_panel(self) -> QWidget:
        """Create results display panel with tabs"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)
        
        # Metric cards at the top
        self.metrics_widget = self.create_metrics_panel()
        layout.addWidget(self.metrics_widget)
        
        # Tab widget for different views
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #ccc;
                border-radius: 4px;
                background: white;
            }
            QTabBar::tab {
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #4CAF50;
                color: white;
                border-radius: 4px 4px 0 0;
            }
        """)
        
        # Tab 1: Equity Curve
        equity_tab = QWidget()
        equity_layout = QVBoxLayout(equity_tab)
        self.equity_figure = Figure(figsize=(10, 5), dpi=100)
        self.equity_canvas = FigureCanvas(self.equity_figure)
        equity_layout.addWidget(self.equity_canvas)
        self.tab_widget.addTab(equity_tab, "📈 资金曲线")
        
        # Tab 2: Drawdown
        drawdown_tab = QWidget()
        drawdown_layout = QVBoxLayout(drawdown_tab)
        self.drawdown_figure = Figure(figsize=(10, 5), dpi=100)
        self.drawdown_canvas = FigureCanvas(self.drawdown_figure)
        drawdown_layout.addWidget(self.drawdown_canvas)
        self.tab_widget.addTab(drawdown_tab, "📉 回撤分析")
        
        # Tab 3: Monthly Returns
        monthly_tab = QWidget()
        monthly_layout = QVBoxLayout(monthly_tab)
        self.monthly_figure = Figure(figsize=(10, 5), dpi=100)
        self.monthly_canvas = FigureCanvas(self.monthly_figure)
        monthly_layout.addWidget(self.monthly_canvas)
        self.tab_widget.addTab(monthly_tab, "📊 月度收益")
        
        # Tab 4: Trade Analysis
        trade_tab = QWidget()
        trade_layout = QVBoxLayout(trade_tab)
        self.trade_table = QTableWidget()
        self.trade_table.setAlternatingRowColors(True)
        self.trade_table.setStyleSheet("""
            QTableWidget {
                gridline-color: #ddd;
                font-size: 11px;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                padding: 6px;
                border: 1px solid #ddd;
                font-weight: bold;
            }
        """)
        trade_layout.addWidget(self.trade_table)
        self.tab_widget.addTab(trade_tab, "📋 交易明细")
        
        # Tab 5: Statistics Summary
        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setFont(QFont("Consolas", 10))
        stats_layout.addWidget(self.stats_text)
        self.tab_widget.addTab(stats_tab, "📑 详细统计")
        
        layout.addWidget(self.tab_widget)
        
        return panel
    
    def create_metrics_panel(self) -> QWidget:
        """Create the top metrics panel with key indicators"""
        widget = QWidget()
        widget.setMaximumHeight(100)
        layout = QHBoxLayout(widget)
        layout.setSpacing(10)
        
        # Create metric cards
        self.card_total_return = MetricCard("总收益率", "-")
        self.card_annual_return = MetricCard("年化收益率", "-")
        self.card_sharpe = MetricCard("夏普比率", "-")
        self.card_max_dd = MetricCard("最大回撤", "-")
        self.card_win_rate = MetricCard("胜率", "-")
        self.card_profit_factor = MetricCard("盈亏比", "-")
        self.card_total_trades = MetricCard("总交易次数", "-")
        self.card_final_value = MetricCard("最终资金", "-")
        
        layout.addWidget(self.card_total_return)
        layout.addWidget(self.card_annual_return)
        layout.addWidget(self.card_sharpe)
        layout.addWidget(self.card_max_dd)
        layout.addWidget(self.card_win_rate)
        layout.addWidget(self.card_profit_factor)
        layout.addWidget(self.card_total_trades)
        layout.addWidget(self.card_final_value)
        
        return widget
    
    def run_backtest(self):
        """Run the backtest"""
        # Get parameters
        stock_code = self.stock_combo.currentText()
        strategy_name = self.strategy_combo.currentData()
        start_date = self.start_date.date().toString("yyyy-MM-dd")
        end_date = self.end_date.date().toString("yyyy-MM-dd")
        initial_cash = self.cash_spin.value()
        commission = self.commission_spin.value()
        
        # Validate stock code
        if not stock_code:
            QMessageBox.warning(self, "错误", "请选择股票代码")
            return
        
        # Get strategy class
        strategy_class = STRATEGIES.get(strategy_name)
        if not strategy_class:
            QMessageBox.warning(self, "错误", f"未知策略: {strategy_name}")
            return
        
        # Create Cerebro
        cerebro = bt.Cerebro()
        
        # Add strategy
        printlog = self.show_trades_check.isChecked()
        cerebro.addstrategy(strategy_class, printlog=printlog)
        
        # Load data
        data = create_bt_data_feed(
            stock_code,
            start_date=start_date,
            end_date=end_date,
        )
        
        if data is None:
            QMessageBox.warning(self, "错误", f"无法加载 {stock_code} 的数据")
            return
        
        cerebro.adddata(data, name=stock_code)
        
        # Set broker
        cerebro.broker.setcash(initial_cash)
        cerebro.broker.setcommission(commission=commission)
        
        # Add analyzers
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.03)
        cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
        cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="time_return")
        cerebro.addanalyzer(bt.analyzers.SQN, _name="sqn")
        cerebro.addanalyzer(bt.analyzers.VWR, _name="vwr")
        cerebro.addanalyzer(TradeRecorder, _name="trade_recorder")
        
        # Store parameters for later
        self.initial_cash = initial_cash
        self.start_date_str = start_date
        self.end_date_str = end_date
        self.strategy_name = strategy_name
        self.stock_code = stock_code
        
        # Disable button and show progress
        self.run_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        
        # Run in thread
        self.backtest_thread = BacktestThread(cerebro, stock_code)
        self.backtest_thread.finished.connect(self.on_backtest_finished)
        self.backtest_thread.start()
    
    def on_backtest_finished(self, results, cerebro):
        """Handle backtest completion"""
        self.run_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        
        if results is None:
            QMessageBox.warning(self, "错误", "回测执行失败!")
            return
        
        # Store cerebro for native plot
        self.last_cerebro = cerebro
        self.native_plot_button.setEnabled(True)
        
        strategy_result = results[0]
        final_value = cerebro.broker.getvalue()
        
        # Extract all analysis data
        self.process_results(strategy_result, final_value)
    
    def show_native_plot(self):
        """Show the native backtrader plot in a separate window"""
        if self.last_cerebro is None:
            QMessageBox.warning(self, "提示", "请先运行回测!")
            return
        
        try:
            # Close any existing matplotlib figures to avoid memory issues
            plt.close('all')
            
            # Show the native backtrader plot
            # This will open a separate matplotlib window
            figs = self.last_cerebro.plot(
                style='candle',  # Candlestick chart
                barup='green',
                bardown='red',
                volup='green',
                voldown='red',
                volume=True,
                plotstyle='line',  # For indicators
            )
            
            # Use non-blocking show to avoid event loop conflicts
            plt.show(block=False)
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法显示原生图表: {str(e)}")
    
    def process_results(self, strategy_result, final_value):
        """Process and display all backtest results"""
        # Basic metrics
        total_return = (final_value / self.initial_cash - 1) * 100
        
        # Calculate trading days and annual return
        time_return = strategy_result.analyzers.time_return.get_analysis()
        trading_days = len(time_return) if time_return else 0
        years = trading_days / 252 if trading_days > 0 else 1
        annual_return = ((final_value / self.initial_cash) ** (1 / years) - 1) * 100 if years > 0 else 0
        
        # Sharpe Ratio
        sharpe_ratio = None
        try:
            sharpe = strategy_result.analyzers.sharpe.get_analysis()
            sharpe_ratio = sharpe.get("sharperatio", None)
        except:
            pass
        
        # Drawdown
        max_dd = 0
        max_dd_len = 0
        try:
            drawdown = strategy_result.analyzers.drawdown.get_analysis()
            max_dd = drawdown.get("max", {}).get("drawdown", 0)
            max_dd_len = drawdown.get("max", {}).get("len", 0)
        except:
            pass
        
        # Trade statistics
        trades_analysis = strategy_result.analyzers.trades.get_analysis()
        total_trades = trades_analysis.get("total", {}).get("total", 0)
        won_trades = trades_analysis.get("won", {}).get("total", 0)
        lost_trades = trades_analysis.get("lost", {}).get("total", 0)
        win_rate = (won_trades / total_trades * 100) if total_trades > 0 else 0
        
        # Profit factor
        gross_profit = trades_analysis.get("won", {}).get("pnl", {}).get("total", 0)
        gross_loss = abs(trades_analysis.get("lost", {}).get("pnl", {}).get("total", 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0
        
        # Average trade metrics
        avg_win = trades_analysis.get("won", {}).get("pnl", {}).get("average", 0)
        avg_loss = trades_analysis.get("lost", {}).get("pnl", {}).get("average", 0)
        avg_trade = trades_analysis.get("pnl", {}).get("net", {}).get("average", 0)
        
        # SQN
        sqn = None
        try:
            sqn_analysis = strategy_result.analyzers.sqn.get_analysis()
            sqn = sqn_analysis.get("sqn", None)
        except:
            pass
        
        # Trade records
        self.trade_records = strategy_result.analyzers.trade_recorder.get_analysis()
        
        # Update metric cards
        self._update_metric_cards(
            total_return, annual_return, sharpe_ratio, max_dd,
            win_rate, profit_factor, total_trades, final_value
        )
        
        # Store equity data
        self.equity_data = self._calculate_equity_curve(time_return)
        self.drawdown_data = self._calculate_drawdown_curve()
        
        # Update all visualizations
        self._plot_equity_curve()
        self._plot_drawdown()
        self._plot_monthly_returns(time_return)
        self._populate_trade_table()
        self._generate_stats_report(
            final_value, total_return, annual_return, sharpe_ratio, max_dd,
            max_dd_len, total_trades, won_trades, lost_trades, win_rate,
            profit_factor, avg_win, avg_loss, avg_trade, sqn, trading_days
        )
    
    def _update_metric_cards(self, total_return, annual_return, sharpe_ratio, max_dd,
                             win_rate, profit_factor, total_trades, final_value):
        """Update all metric cards with values"""
        # Total return
        color = "#4CAF50" if total_return >= 0 else "#F44336"
        self.card_total_return.set_value(f"{total_return:.2f}%", color)
        
        # Annual return
        color = "#4CAF50" if annual_return >= 0 else "#F44336"
        self.card_annual_return.set_value(f"{annual_return:.2f}%", color)
        
        # Sharpe ratio - handle extreme values
        if sharpe_ratio is not None and -10 <= sharpe_ratio <= 10:
            color = "#4CAF50" if sharpe_ratio >= 1 else "#FF9800" if sharpe_ratio >= 0 else "#F44336"
            self.card_sharpe.set_value(f"{sharpe_ratio:.3f}", color)
        else:
            # Extreme or invalid sharpe ratio
            self.card_sharpe.set_value("N/A", "#999")
        
        # Max drawdown
        color = "#4CAF50" if max_dd < 10 else "#FF9800" if max_dd < 20 else "#F44336"
        self.card_max_dd.set_value(f"-{max_dd:.2f}%", color)
        
        # Win rate
        color = "#4CAF50" if win_rate >= 50 else "#FF9800" if win_rate >= 40 else "#F44336"
        self.card_win_rate.set_value(f"{win_rate:.1f}%", color)
        
        # Profit factor
        if profit_factor == float('inf'):
            self.card_profit_factor.set_value("∞", "#4CAF50")
        else:
            color = "#4CAF50" if profit_factor >= 1.5 else "#FF9800" if profit_factor >= 1 else "#F44336"
            self.card_profit_factor.set_value(f"{profit_factor:.2f}", color)
        
        # Total trades
        self.card_total_trades.set_value(str(total_trades), "#333")
        
        # Final value
        color = "#4CAF50" if final_value >= self.initial_cash else "#F44336"
        self.card_final_value.set_value(f"{final_value:,.0f}元", color)
    
    def _calculate_equity_curve(self, time_return):
        """Calculate equity curve from time returns"""
        if not time_return:
            return None
        
        dates = list(time_return.keys())
        returns = list(time_return.values())
        
        cumulative = [1]
        for r in returns:
            cumulative.append(cumulative[-1] * (1 + r))
        
        equity = [self.initial_cash * c for c in cumulative[1:]]
        
        return {'dates': dates, 'equity': equity, 'returns': returns}
    
    def _calculate_drawdown_curve(self):
        """Calculate drawdown curve from equity data"""
        if not self.equity_data:
            return None
        
        equity = self.equity_data['equity']
        dates = self.equity_data['dates']
        
        running_max = []
        drawdowns = []
        
        current_max = equity[0] if equity else 0
        for e in equity:
            current_max = max(current_max, e)
            running_max.append(current_max)
            dd = (e - current_max) / current_max * 100 if current_max > 0 else 0
            drawdowns.append(dd)
        
        return {'dates': dates, 'drawdowns': drawdowns, 'running_max': running_max}
    
    def _plot_equity_curve(self):
        """Plot equity curve with benchmark"""
        self.equity_figure.clear()
        
        if not self.equity_data:
            return
        
        ax = self.equity_figure.add_subplot(111)
        
        dates = self.equity_data['dates']
        equity = self.equity_data['equity']
        
        # Plot equity curve
        ax.plot(dates, equity, 'b-', linewidth=1.5, label='策略净值')
        
        # Initial capital line
        ax.axhline(y=self.initial_cash, color='gray', linestyle='--', alpha=0.5, label='初始资金')
        
        # Fill areas
        ax.fill_between(dates, self.initial_cash, equity,
                       where=[e > self.initial_cash for e in equity],
                       color='green', alpha=0.2)
        ax.fill_between(dates, self.initial_cash, equity,
                       where=[e <= self.initial_cash for e in equity],
                       color='red', alpha=0.2)
        
        # Mark highest and lowest points
        max_idx = equity.index(max(equity))
        min_idx = equity.index(min(equity))
        
        ax.scatter([dates[max_idx]], [equity[max_idx]], color='green', s=80, zorder=5, marker='^')
        ax.scatter([dates[min_idx]], [equity[min_idx]], color='red', s=80, zorder=5, marker='v')
        
        ax.annotate(f'最高: {equity[max_idx]:,.0f}元', 
                   xy=(dates[max_idx], equity[max_idx]),
                   xytext=(10, 10), textcoords='offset points', fontsize=9)
        ax.annotate(f'最低: {equity[min_idx]:,.0f}元',
                   xy=(dates[min_idx], equity[min_idx]),
                   xytext=(10, -15), textcoords='offset points', fontsize=9)
        
        ax.set_xlabel('日期')
        ax.set_ylabel('资金 (元)')
        ax.set_title(f'{self.stock_code} - {self.strategy_name} 策略资金曲线')
        ax.legend(loc='upper left')
        ax.grid(True, alpha=0.3)
        
        # Disable scientific notation on Y axis to show actual values
        ax.ticklabel_format(style='plain', axis='y')
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:,.0f}'))
        
        # Set Y axis limits with some padding
        min_equity = min(equity)
        max_equity = max(equity)
        y_range = max_equity - min_equity
        if y_range < self.initial_cash * 0.01:  # If range is less than 1% of initial capital
            # Use initial cash as reference for range
            y_padding = self.initial_cash * 0.05
            ax.set_ylim(min(min_equity, self.initial_cash) - y_padding, 
                       max(max_equity, self.initial_cash) + y_padding)
        else:
            y_padding = y_range * 0.1
            ax.set_ylim(min_equity - y_padding, max_equity + y_padding)
        
        self.equity_figure.autofmt_xdate()
        self.equity_figure.tight_layout()
        self.equity_canvas.draw()
    
    def _plot_drawdown(self):
        """Plot drawdown chart"""
        self.drawdown_figure.clear()
        
        if not self.drawdown_data:
            return
        
        fig = self.drawdown_figure
        ax1 = fig.add_subplot(211)
        ax2 = fig.add_subplot(212)
        
        dates = self.drawdown_data['dates']
        drawdowns = self.drawdown_data['drawdowns']
        equity = self.equity_data['equity']
        running_max = self.drawdown_data['running_max']
        
        # Top plot: Equity and running max
        ax1.plot(dates, equity, 'b-', linewidth=1, label='策略净值')
        ax1.plot(dates, running_max, 'g--', linewidth=1, alpha=0.7, label='历史最高')
        ax1.fill_between(dates, equity, running_max, color='red', alpha=0.3)
        ax1.set_ylabel('资金 (元)')
        ax1.set_title('资金曲线与历史最高')
        ax1.legend(loc='upper left')
        ax1.grid(True, alpha=0.3)
        
        # Bottom plot: Drawdown percentage
        ax2.fill_between(dates, 0, drawdowns, color='red', alpha=0.5)
        ax2.plot(dates, drawdowns, 'r-', linewidth=1)
        ax2.axhline(y=0, color='black', linewidth=0.5)
        
        # Mark max drawdown
        min_dd = min(drawdowns)
        min_dd_idx = drawdowns.index(min_dd)
        ax2.scatter([dates[min_dd_idx]], [min_dd], color='darkred', s=80, zorder=5)
        ax2.annotate(f'最大回撤: {min_dd:.2f}%',
                    xy=(dates[min_dd_idx], min_dd),
                    xytext=(10, -15), textcoords='offset points', fontsize=9)
        
        ax2.set_xlabel('日期')
        ax2.set_ylabel('回撤 (%)')
        ax2.set_title('回撤曲线')
        ax2.grid(True, alpha=0.3)
        
        fig.autofmt_xdate()
        fig.tight_layout()
        self.drawdown_canvas.draw()
    
    def _plot_monthly_returns(self, time_return):
        """Plot monthly returns heatmap"""
        self.monthly_figure.clear()
        
        if not time_return:
            return
        
        # Calculate monthly returns
        monthly_returns = {}
        for date, ret in time_return.items():
            year = date.year
            month = date.month
            key = (year, month)
            if key not in monthly_returns:
                monthly_returns[key] = 1
            monthly_returns[key] *= (1 + ret)
        
        # Convert to percentage
        for key in monthly_returns:
            monthly_returns[key] = (monthly_returns[key] - 1) * 100
        
        if not monthly_returns:
            return
        
        # Create matrix for heatmap
        years = sorted(set(k[0] for k in monthly_returns.keys()))
        months = list(range(1, 13))
        
        data = np.full((len(years), 12), np.nan)
        for (year, month), ret in monthly_returns.items():
            year_idx = years.index(year)
            data[year_idx, month - 1] = ret
        
        fig = self.monthly_figure
        ax = fig.add_subplot(111)
        
        # Create heatmap
        cmap = plt.cm.RdYlGn
        im = ax.imshow(data, cmap=cmap, aspect='auto', vmin=-20, vmax=20)
        
        # Labels
        ax.set_xticks(range(12))
        ax.set_xticklabels(['1月', '2月', '3月', '4月', '5月', '6月',
                          '7月', '8月', '9月', '10月', '11月', '12月'])
        ax.set_yticks(range(len(years)))
        ax.set_yticklabels(years)
        
        # Add text annotations
        for i in range(len(years)):
            for j in range(12):
                if not np.isnan(data[i, j]):
                    text = f'{data[i, j]:.1f}%'
                    color = 'white' if abs(data[i, j]) > 10 else 'black'
                    ax.text(j, i, text, ha='center', va='center', fontsize=8, color=color)
        
        ax.set_title('月度收益率 (%)')
        fig.colorbar(im, ax=ax, label='收益率 (%)')
        
        fig.tight_layout()
        self.monthly_canvas.draw()
    
    def _populate_trade_table(self):
        """Populate trade details table"""
        if not self.trade_records:
            self.trade_table.setRowCount(0)
            return
        
        headers = ['序号', '开仓日期', '平仓日期', '持仓天数', '方向', 
                  '开仓价', '平仓价', '盈亏', '盈亏%', '手续费']
        self.trade_table.setColumnCount(len(headers))
        self.trade_table.setHorizontalHeaderLabels(headers)
        self.trade_table.setRowCount(len(self.trade_records))
        
        for row, trade in enumerate(self.trade_records):
            self.trade_table.setItem(row, 0, QTableWidgetItem(str(trade['ref'])))
            self.trade_table.setItem(row, 1, QTableWidgetItem(
                trade['open_date'].strftime('%Y-%m-%d') if trade['open_date'] else '-'))
            self.trade_table.setItem(row, 2, QTableWidgetItem(
                trade['close_date'].strftime('%Y-%m-%d') if trade['close_date'] else '-'))
            self.trade_table.setItem(row, 3, QTableWidgetItem(str(trade['bars'])))
            self.trade_table.setItem(row, 4, QTableWidgetItem('多' if trade['size'] > 0 else '空'))
            self.trade_table.setItem(row, 5, QTableWidgetItem(f"{trade['open_price']:.2f}"))
            self.trade_table.setItem(row, 6, QTableWidgetItem(f"{trade['close_price']:.2f}"))
            
            # PnL with color
            pnl_item = QTableWidgetItem(f"{trade['pnl']:.2f}")
            pnl_item.setForeground(QColor('#4CAF50') if trade['pnl'] >= 0 else QColor('#F44336'))
            self.trade_table.setItem(row, 7, pnl_item)
            
            # PnL percentage with color
            pnl_pct_item = QTableWidgetItem(f"{trade['pnl_pct']:.2f}%")
            pnl_pct_item.setForeground(QColor('#4CAF50') if trade['pnl_pct'] >= 0 else QColor('#F44336'))
            self.trade_table.setItem(row, 8, pnl_pct_item)
            
            self.trade_table.setItem(row, 9, QTableWidgetItem(f"{trade['commission']:.2f}"))
        
        self.trade_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    
    def _generate_stats_report(self, final_value, total_return, annual_return, sharpe_ratio,
                               max_dd, max_dd_len, total_trades, won_trades, lost_trades,
                               win_rate, profit_factor, avg_win, avg_loss, avg_trade, sqn, trading_days):
        """Generate detailed statistics report"""
        lines = []
        lines.append("=" * 60)
        lines.append("                    详 细 回 测 报 告")
        lines.append("=" * 60)
        lines.append("")
        
        # Basic info
        lines.append("【基本信息】")
        lines.append(f"  股票代码:     {self.stock_code}")
        lines.append(f"  策略名称:     {self.strategy_name}")
        lines.append(f"  回测区间:     {self.start_date_str} 至 {self.end_date_str}")
        lines.append(f"  交易天数:     {trading_days} 天")
        lines.append("")
        
        # Capital
        lines.append("【资金情况】")
        lines.append(f"  初始资金:     {self.initial_cash:,.2f} 元")
        lines.append(f"  最终资金:     {final_value:,.2f} 元")
        lines.append(f"  净利润:       {final_value - self.initial_cash:,.2f} 元")
        lines.append("")
        
        # Returns
        lines.append("【收益指标】")
        lines.append(f"  总收益率:     {total_return:.2f}%")
        lines.append(f"  年化收益率:   {annual_return:.2f}%")
        if sharpe_ratio is not None:
            lines.append(f"  夏普比率:     {sharpe_ratio:.3f}")
        if sqn is not None:
            lines.append(f"  SQN:          {sqn:.3f}")
        lines.append("")
        
        # Risk
        lines.append("【风险指标】")
        lines.append(f"  最大回撤:     {max_dd:.2f}%")
        lines.append(f"  最长回撤期:   {max_dd_len} 天")
        if sharpe_ratio and max_dd > 0:
            calmar = annual_return / max_dd
            lines.append(f"  卡玛比率:     {calmar:.3f}")
        lines.append("")
        
        # Trading
        lines.append("【交易统计】")
        lines.append(f"  总交易次数:   {total_trades}")
        lines.append(f"  盈利次数:     {won_trades}")
        lines.append(f"  亏损次数:     {lost_trades}")
        lines.append(f"  胜率:         {win_rate:.2f}%")
        if profit_factor != float('inf'):
            lines.append(f"  盈亏比:       {profit_factor:.2f}")
        else:
            lines.append(f"  盈亏比:       ∞")
        lines.append("")
        
        # Average per trade
        lines.append("【单笔交易】")
        lines.append(f"  平均盈利:     {avg_win:.2f} 元")
        lines.append(f"  平均亏损:     {avg_loss:.2f} 元")
        lines.append(f"  平均每笔:     {avg_trade:.2f} 元")
        
        if self.trade_records:
            # Calculate additional stats
            pnls = [t['pnl'] for t in self.trade_records]
            pnl_pcts = [t['pnl_pct'] for t in self.trade_records]
            holding_days = [t['bars'] for t in self.trade_records]
            
            if pnls:
                lines.append(f"  最大单笔盈利: {max(pnls):.2f} 元")
                lines.append(f"  最大单笔亏损: {min(pnls):.2f} 元")
            
            if holding_days:
                lines.append(f"  平均持仓:     {np.mean(holding_days):.1f} 天")
                lines.append(f"  最长持仓:     {max(holding_days)} 天")
                lines.append(f"  最短持仓:     {min(holding_days)} 天")
            
            # Consecutive wins/losses
            if pnls:
                max_consecutive_wins = 0
                max_consecutive_losses = 0
                current_wins = 0
                current_losses = 0
                
                for pnl in pnls:
                    if pnl > 0:
                        current_wins += 1
                        current_losses = 0
                        max_consecutive_wins = max(max_consecutive_wins, current_wins)
                    else:
                        current_losses += 1
                        current_wins = 0
                        max_consecutive_losses = max(max_consecutive_losses, current_losses)
                
                lines.append("")
                lines.append("【连续统计】")
                lines.append(f"  最大连胜:     {max_consecutive_wins} 次")
                lines.append(f"  最大连亏:     {max_consecutive_losses} 次")
        
        lines.append("")
        lines.append("=" * 60)
        
        self.stats_text.setText("\n".join(lines))


def main():
    """Run the GUI application"""
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle("Fusion")
    
    window = BacktestGUI()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
