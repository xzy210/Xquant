import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, 
    QTableWidget, QTableWidgetItem, QProgressBar, QLabel, QHeaderView,
    QSplitter, QGroupBox, QDateEdit, QSpinBox, QMessageBox, QTabWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDate
from PyQt6.QtGui import QColor

try:
    from strategies import get_all_strategies, get_strategy
    from strategies.cross_sectional_strategy import CrossSectionalStrategy
    from data_loader import get_stock_list, load_stock_data, load_stock_name_map
except ImportError:
    from ..strategies import get_all_strategies, get_strategy
    from ..strategies.cross_sectional_strategy import CrossSectionalStrategy
    from ..data_loader import get_stock_list, load_stock_data, load_stock_name_map

class BacktestThread(QThread):
    """回测后台线程"""
    progress_updated = pyqtSignal(int, int) # current, total
    finished_signal = pyqtSignal(dict) # result dict
    error_signal = pyqtSignal(str)

    def __init__(self, strategy_name, code, start_date, end_date, initial_cash, data_dir):
        super().__init__()
        self.strategy_name = strategy_name
        self.code = code
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.data_dir = data_dir

    def run(self):
        try:
            # 1. 获取策略实例
            strategy = get_strategy(self.strategy_name)
            if not strategy:
                self.error_signal.emit("策略未找到")
                return

            # 2. 加载数据
            df = load_stock_data(
                self.code, 
                self.data_dir, 
                start_date=self.start_date, 
                end_date=self.end_date
            )
            
            if df is None or df.empty:
                self.error_signal.emit(f"未找到 {self.code} 的历史数据")
                return
                
            # 3. 运行回测
            # 注意：这里我们直接调用 strategy.run_backtest 
            # 如果需要进度回调，可能需要修改 BacktestEngine 支持 callback
            result = strategy.run_backtest(df, self.code, self.initial_cash)
            
            self.finished_signal.emit(result)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error_signal.emit(f"回测出错: {str(e)}")

class BacktestWidget(QWidget):
    """回测功能主界面 (单标的时序回测)"""
    
    def __init__(self, data_dir="../data", stocklist_path=None):
        super().__init__()
        self.data_dir = data_dir
        self.stocklist_path = stocklist_path
        self.backtest_thread = None
        self.stock_list = []
        self.name_map = {}
        
        self.setupUI()
        self.load_data()

    def setupUI(self):
        layout = QHBoxLayout(self)
        
        # --- 左侧设置面板 ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 10, 0)
        
        # 1. 策略选择
        strat_group = QGroupBox("策略设置")
        strat_layout = QVBoxLayout(strat_group)
        strat_layout.addWidget(QLabel("选择策略:"))
        self.strategy_combo = QComboBox()
        strategies = get_all_strategies()
        for sid, name in strategies.items():
            # 过滤掉截面策略
            strat = get_strategy(sid)
            if not isinstance(strat, CrossSectionalStrategy):
                self.strategy_combo.addItem(name, sid)
                
        strat_layout.addWidget(self.strategy_combo)
        left_layout.addWidget(strat_group)
        
        # 2. 标的选择
        stock_group = QGroupBox("回测标的")
        stock_layout = QVBoxLayout(stock_group)
        
        stock_layout.addWidget(QLabel("股票代码:"))
        self.stock_combo = QComboBox()
        self.stock_combo.setEditable(True) # 允许搜索/输入
        self.stock_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert) # 不自动插入新项
        
        # 启用过滤功能
        self.stock_combo.completer().setCompletionMode(self.stock_combo.completer().CompletionMode.PopupCompletion)
        self.stock_combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        
        stock_layout.addWidget(self.stock_combo)
        
        left_layout.addWidget(stock_group)
        
        # 3. 参数设置
        param_group = QGroupBox("回测参数")
        param_layout = QVBoxLayout(param_group)
        
        param_layout.addWidget(QLabel("起始日期:"))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDate(QDate.currentDate().addYears(-1))
        param_layout.addWidget(self.start_date_edit)
        
        param_layout.addWidget(QLabel("结束日期:"))
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDate(QDate.currentDate())
        param_layout.addWidget(self.end_date_edit)
        
        param_layout.addWidget(QLabel("初始资金:"))
        self.capital_spin = QSpinBox()
        self.capital_spin.setRange(1000, 100000000)
        self.capital_spin.setSingleStep(10000)
        self.capital_spin.setValue(100000)
        self.capital_spin.setSuffix(" 元")
        param_layout.addWidget(self.capital_spin)
        
        left_layout.addWidget(param_group)
        
        # 按钮
        self.run_btn = QPushButton("开始回测")
        self.run_btn.setProperty("class", "primary")
        self.run_btn.clicked.connect(self.run_backtest)
        left_layout.addWidget(self.run_btn)
        
        left_layout.addStretch()
        
        # --- 右侧结果面板 ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # 结果选项卡
        self.result_tabs = QTabWidget()
        
        # Tab 1: 资金曲线
        self.chart_widget = pg.PlotWidget()
        self.chart_widget.setBackground('#1e1e1e')
        self.chart_widget.showGrid(x=True, y=True, alpha=0.3)
        self.chart_widget.setLabel('left', '总资产')
        self.chart_widget.setLabel('bottom', '日期')
        self.chart_widget.addLegend()
        self.result_tabs.addTab(self.chart_widget, "资金曲线")
        
        # Tab 2: 交易详情
        self.trade_table = QTableWidget()
        self.trade_table.setColumnCount(8)
        self.trade_table.setHorizontalHeaderLabels([
            "日期", "标的", "操作", "价格", "数量", "手续费", "原因", "剩余资金"
        ])
        self.result_tabs.addTab(self.trade_table, "交易记录")
        
        # Tab 3: 统计摘要
        self.stats_label = QLabel("暂无结果")
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.stats_label.setStyleSheet("font-family: Consolas, monospace;")
        self.result_tabs.addTab(self.stats_label, "回测报告")
        
        right_layout.addWidget(self.result_tabs)
        
        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([250, 750])
        
        layout.addWidget(splitter)
        
    def load_data(self):
        """加载股票列表"""
        self.stock_list = get_stock_list(self.data_dir)
        self.name_map = load_stock_name_map(self.stocklist_path) if self.stocklist_path else load_stock_name_map()
        
        self.stock_combo.clear()
        for code in self.stock_list:
            name = self.name_map.get(code, "")
            self.stock_combo.addItem(f"{code} {name}", code)
            
    def run_backtest(self):
        if self.backtest_thread and self.backtest_thread.isRunning():
            return

        sid = self.strategy_combo.currentData()
        
        # 获取用户输入的股票代码
        # currentData() 可能为空（如果用户手动输入了内容），所以优先解析 currentText()
        text = self.stock_combo.currentText()
        code = None
        
        # 尝试从输入文本中提取代码
        import re
        # 匹配6位数字代码
        match = re.search(r'\d{6}', text)
        if match:
            code = match.group(0)
        else:
            # 如果没找到代码，看看是不是直接输入了代码
            # 尝试从 data 中获取
            code = self.stock_combo.currentData()
            
        if not code:
            QMessageBox.warning(self, "提示", "请输入有效的6位股票代码")
            return

        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        initial_cash = self.capital_spin.value()
        
        self.run_btn.setEnabled(False)
        self.run_btn.setText("回测中...")
        self.chart_widget.clear()
        self.trade_table.setRowCount(0)
        self.stats_label.setText("正在运行...")
        
        self.backtest_thread = BacktestThread(
            sid, code, start_date, end_date, initial_cash, self.data_dir
        )
        self.backtest_thread.finished_signal.connect(self.on_finished)
        self.backtest_thread.error_signal.connect(self.on_error)
        self.backtest_thread.start()
        
    def on_finished(self, result):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("开始回测")
        
        # 1. 绘制曲线
        equity_df = result['equity_curve']
        if not equity_df.empty:
            # 日期转时间戳或索引
            x = range(len(equity_df))
            y = equity_df['total_asset'].values
            
            # 绘制总资产
            self.chart_widget.plot(x, y, pen=pg.mkPen('b', width=2), name="策略净值")
            
            # 如果有基准（比如一直持有现金），可以画条线
            # self.chart_widget.addLine(y=self.capital_spin.value(), pen='g')
            
            # 设置X轴标签 (显示部分日期)
            ax = self.chart_widget.getAxis('bottom')
            dates = equity_df['date'].astype(str).tolist()
            # 简单的刻度策略：每隔N个显示一个
            ticks = []
            n = max(1, len(dates) // 10)
            for i in range(0, len(dates), n):
                ticks.append((i, dates[i]))
            ax.setTicks([ticks])

        # 2. 填充交易列表
        trades = result['trades']
        self.trade_table.setRowCount(len(trades))
        for i, t in enumerate(trades):
            # t is TradeRecord object
            self.trade_table.setItem(i, 0, QTableWidgetItem(str(t.date)))
            self.trade_table.setItem(i, 1, QTableWidgetItem(t.symbol))
            
            # 颜色区分买卖
            action_item = QTableWidgetItem(t.action)
            if t.action == 'BUY':
                action_item.setForeground(QColor("red"))
            else:
                action_item.setForeground(QColor("green"))
            self.trade_table.setItem(i, 2, action_item)
            
            self.trade_table.setItem(i, 3, QTableWidgetItem(f"{t.price:.2f}"))
            self.trade_table.setItem(i, 4, QTableWidgetItem(str(t.quantity)))
            self.trade_table.setItem(i, 5, QTableWidgetItem(f"{t.commission:.2f}"))
            self.trade_table.setItem(i, 6, QTableWidgetItem(t.reason))
            self.trade_table.setItem(i, 7, QTableWidgetItem(f"{t.cash_after:.2f}"))

        # 3. 统计报告
        final_value = result['final_value']
        init_cash = self.capital_spin.value()
        ret = (final_value - init_cash) / init_cash * 100
        
        closed_trades = result['closed_trades']
        win_count = sum(1 for t in closed_trades if t.pnl > 0)
        total_closed = len(closed_trades)
        win_rate = (win_count / total_closed * 100) if total_closed > 0 else 0
        
        report = f"""
        === 回测报告 ===
        
        初始资金: {init_cash:,.2f}
        最终资产: {final_value:,.2f}
        收益率  : {ret:+.2f}%
        
        交易次数: {len(trades)}
        平仓次数: {total_closed}
        胜率    : {win_rate:.2f}%
        """
        self.stats_label.setText(report)
        
    def on_error(self, msg):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("开始回测")
        self.stats_label.setText(f"错误: {msg}")
        QMessageBox.critical(self, "回测失败", msg)
