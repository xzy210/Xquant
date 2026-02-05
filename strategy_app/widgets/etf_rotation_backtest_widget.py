"""
ETF轮动策略回测组件

专门为ETF多因子轮动策略设计的回测界面，支持同时回测多个ETF标的
"""
import os
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, 
    QTableWidget, QTableWidgetItem, QLabel, QHeaderView,
    QSplitter, QGroupBox, QDateEdit, QSpinBox, QMessageBox, 
    QTabWidget, QListWidget, QListWidgetItem, QCheckBox,
    QDoubleSpinBox, QGridLayout
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDate
from PyQt6.QtGui import QColor

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from strategies import ETFThreeFactorMomentumStrategy
# 使用优化版策略（速度提升5-10倍）
from strategies.etf_three_factor_momentum_strategy_fast import ETFThreeFactorMomentumStrategyFast
from data_loader import load_stock_data


class ETFBacktestThread(QThread):
    """ETF轮动策略回测线程"""
    progress_updated = pyqtSignal(int, int)  # current, total
    finished_signal = pyqtSignal(dict)  # result dict
    error_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)  # 日志输出

    def __init__(self, etf_pool, start_date, end_date, initial_cash, 
                 data_dir, params=None):
        super().__init__()
        self.etf_pool = etf_pool
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.data_dir = data_dir
        self.etf_data_dir = os.path.join(data_dir, "etf")  # ETF数据子目录
        self.params = params or {}
        self._is_running = True

    def run(self):
        try:
            import os
            self.log_signal.emit(f"开始加载ETF数据...")
            self.log_signal.emit(f"ETF数据目录: {self.etf_data_dir}")
            self.log_signal.emit(f"ETF列表: {self.etf_pool}")
            
            # 检查ETF数据目录是否存在
            if not os.path.exists(self.etf_data_dir):
                self.error_signal.emit(f"ETF数据目录不存在: {self.etf_data_dir}\n请确保ETF数据已下载到 data/etf/ 目录")
                return
            
            # 检查有哪些数据文件
            import glob
            parquet_files = glob.glob(os.path.join(self.etf_data_dir, "*.parquet"))
            self.log_signal.emit(f"发现 {len(parquet_files)} 个ETF数据文件")
            
            # 1. 加载所有ETF数据
            all_data = {}
            for code in self.etf_pool:
                if not self._is_running:
                    return
                
                # 检查文件是否存在
                file_path = os.path.join(self.etf_data_dir, f"{code}.parquet")
                if not os.path.exists(file_path):
                    self.log_signal.emit(f"  ✗ {code}: 文件不存在 {file_path}")
                    continue
                    
                df = load_stock_data(
                    code, 
                    self.etf_data_dir,  # 使用ETF子目录
                    start_date=self.start_date, 
                    end_date=self.end_date
                )
                
                if df is not None and not df.empty:
                    all_data[code] = df
                    self.log_signal.emit(f"  ✓ {code}: {len(df)} 条数据 ({df['date'].min()} ~ {df['date'].max()})")
                else:
                    self.log_signal.emit(f"  ✗ {code}: 数据为空或加载失败")
            
            if len(all_data) < 2:
                missing = set(self.etf_pool) - set(all_data.keys())
                self.error_signal.emit(f"至少需要2只ETF的数据才能进行轮动回测\n缺失数据: {', '.join(missing)}")
                return
            
            # 2. 创建策略实例（使用优化版）
            self.log_signal.emit("\n初始化优化版策略...")
            strategy = ETFThreeFactorMomentumStrategyFast()
            strategy.set_params(self.params)
            
            # 3. 预计算所有因子（关键优化步骤）
            self.log_signal.emit("预计算因子得分（这可能需要几秒钟）...")
            import time
            start_time = time.time()
            strategy.precompute_scores(all_data)
            precompute_time = time.time() - start_time
            self.log_signal.emit(f"预计算完成，耗时: {precompute_time:.2f}秒")
            
            # 4. 运行回测
            self.log_signal.emit("\n开始运行回测...")
            result = self._run_backtest(strategy, all_data)
            
            if self._is_running:
                self.finished_signal.emit(result)
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error_signal.emit(f"回测出错: {str(e)}")
    
    def _run_backtest(self, strategy, all_data):
        """运行回测逻辑（优化版）"""
        from backtest.context import Context
        import time
        
        # 获取所有日期（以第一个ETF的日期为基准）
        base_code = list(all_data.keys())[0]
        dates = all_data[base_code]['date'].tolist()
        
        self.log_signal.emit(f"回测区间: {len(dates)} 个交易日")
        
        # 初始化上下文
        context = Context(self.initial_cash)
        strategy.initialize(context)
        
        # 准备结果记录
        equity_curve = []
        daily_scores = []  # 记录每日得分
        
        # 时间步进循环
        start_time = time.time()
        last_log_time = start_time
        
        for i, current_date in enumerate(dates):
            if not self._is_running:
                break
                
            self.progress_updated.emit(i + 1, len(dates))
            
            # 准备当前时刻的数据切片
            bars = {}
            current_scores = {'date': current_date}
            
            for code, df in all_data.items():
                # 找到当前日期的数据
                day_data = df[df['date'] == current_date]
                if day_data.empty:
                    continue
                
                row = day_data.iloc[0]
                bars[code] = row
                
                # 快速获取预计算的得分（O(1)时间复杂度）
                score = strategy.get_score_for_date(code, current_date)
                if score is not None:
                    current_scores[code] = score
            
            if not bars:
                continue
            
            # 更新上下文
            context.current_dt = current_date
            for code, row in bars.items():
                context.current_prices[code] = row['close']
            
            # 执行策略（使用预计算得分）
            # 传递空history，因为策略会使用预计算的数据
            strategy.on_bar(context, bars, {})
            
            # 记录资产
            market_value = 0
            for pos_code, pos in context.positions.items():
                p = context.current_prices.get(pos_code, pos.avg_price)
                market_value += pos.quantity * p
            
            total_asset = context.cash + market_value
            equity_curve.append({
                'date': current_date,
                'total_asset': total_asset,
                'cash': context.cash,
                'market_value': market_value,
                'holding': strategy.current_holding
            })
            
            # 记录每日得分
            if len(current_scores) > 1:
                daily_scores.append(current_scores)
            
            # 每5秒输出一次进度
            current_time = time.time()
            if current_time - last_log_time > 5:
                elapsed = current_time - start_time
                progress = (i + 1) / len(dates) * 100
                eta = elapsed / (i + 1) * (len(dates) - i - 1) if i > 0 else 0
                self.log_signal.emit(
                    f"进度: {progress:.1f}% ({i+1}/{len(dates)}), "
                    f"耗时: {elapsed:.1f}s, 预计剩余: {eta:.1f}s, "
                    f"持仓: {strategy.current_holding or '无'}"
                )
                last_log_time = current_time
        
        # 回测完成
        total_time = time.time() - start_time
        self.log_signal.emit(f"\n回测完成！总耗时: {total_time:.2f}秒")
        
        return {
            'equity_curve': pd.DataFrame(equity_curve),
            'trades': context.trade_history,
            'closed_trades': context.closed_trades,
            'final_value': equity_curve[-1]['total_asset'] if equity_curve else self.initial_cash,
            'daily_scores': pd.DataFrame(daily_scores),
            'params': self.params
        }
    
    def stop(self):
        self._is_running = False


class ETFRotationBacktestWidget(QWidget):
    """ETF轮动策略回测界面"""
    
    # 默认ETF池
    DEFAULT_ETF_POOL = [
        ('510880', '红利ETF'),
        ('159949', '创业板50ETF'),
        ('513100', '纳指ETF'),
        ('518880', '黄金ETF'),
    ]
    
    def __init__(self, data_dir="../data"):
        super().__init__()
        self.data_dir = data_dir
        self.etf_data_dir = os.path.join(data_dir, "etf")  # ETF数据子目录
        self.backtest_thread = None
        
        self.setupUI()
        self.check_data_available()
    
    def setupUI(self):
        layout = QHBoxLayout(self)
        
        # --- 左侧设置面板 ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 10, 0)
        
        # 1. ETF选择
        etf_group = QGroupBox("ETF标的池")
        etf_layout = QVBoxLayout(etf_group)
        
        self.etf_list = QListWidget()
        self.etf_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        
        for code, name in self.DEFAULT_ETF_POOL:
            item = QListWidgetItem(f"{code} {name}")
            item.setData(Qt.ItemDataRole.UserRole, code)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.etf_list.addItem(item)
        
        etf_layout.addWidget(self.etf_list)
        left_layout.addWidget(etf_group)
        
        # 2. 策略参数
        param_group = QGroupBox("策略参数")
        param_layout = QGridLayout(param_group)
        
        row = 0
        
        # 权重设置
        param_layout.addWidget(QLabel("乖离动量权重:"), row, 0)
        self.bias_weight_spin = QDoubleSpinBox()
        self.bias_weight_spin.setRange(0, 1)
        self.bias_weight_spin.setSingleStep(0.1)
        self.bias_weight_spin.setValue(0.3)
        self.bias_weight_spin.setDecimals(2)
        param_layout.addWidget(self.bias_weight_spin, row, 1)
        row += 1
        
        param_layout.addWidget(QLabel("斜率动量权重:"), row, 0)
        self.slope_weight_spin = QDoubleSpinBox()
        self.slope_weight_spin.setRange(0, 1)
        self.slope_weight_spin.setSingleStep(0.1)
        self.slope_weight_spin.setValue(0.3)
        self.slope_weight_spin.setDecimals(2)
        param_layout.addWidget(self.slope_weight_spin, row, 1)
        row += 1
        
        param_layout.addWidget(QLabel("效率动量权重:"), row, 0)
        self.efficiency_weight_spin = QDoubleSpinBox()
        self.efficiency_weight_spin.setRange(0, 1)
        self.efficiency_weight_spin.setSingleStep(0.1)
        self.efficiency_weight_spin.setValue(0.4)
        self.efficiency_weight_spin.setDecimals(2)
        param_layout.addWidget(self.efficiency_weight_spin, row, 1)
        row += 1
        
        # 调仓阈值
        param_layout.addWidget(QLabel("调仓阈值:"), row, 0)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(1.0, 3.0)
        self.threshold_spin.setSingleStep(0.1)
        self.threshold_spin.setValue(1.5)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setToolTip("第二名得分需超过第一名的倍数才调仓")
        param_layout.addWidget(self.threshold_spin, row, 1)
        row += 1
        
        # 动量窗口
        param_layout.addWidget(QLabel("动量窗口:"), row, 0)
        self.momentum_window_spin = QSpinBox()
        self.momentum_window_spin.setRange(10, 60)
        self.momentum_window_spin.setValue(25)
        param_layout.addWidget(self.momentum_window_spin, row, 1)
        row += 1
        
        # Z-Score窗口
        param_layout.addWidget(QLabel("Z-Score窗口:"), row, 0)
        self.zscore_window_spin = QSpinBox()
        self.zscore_window_spin.setRange(20, 120)
        self.zscore_window_spin.setValue(60)
        param_layout.addWidget(self.zscore_window_spin, row, 1)
        
        left_layout.addWidget(param_group)
        
        # 3. 回测参数
        backtest_group = QGroupBox("回测参数")
        backtest_layout = QGridLayout(backtest_group)
        
        backtest_layout.addWidget(QLabel("起始日期:"), 0, 0)
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDate(QDate.currentDate().addYears(-3))
        backtest_layout.addWidget(self.start_date_edit, 0, 1)
        
        backtest_layout.addWidget(QLabel("结束日期:"), 1, 0)
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDate(QDate.currentDate())
        backtest_layout.addWidget(self.end_date_edit, 1, 1)
        
        backtest_layout.addWidget(QLabel("初始资金:"), 2, 0)
        self.capital_spin = QSpinBox()
        self.capital_spin.setRange(10000, 10000000)
        self.capital_spin.setSingleStep(10000)
        self.capital_spin.setValue(100000)
        self.capital_spin.setSuffix(" 元")
        backtest_layout.addWidget(self.capital_spin, 2, 1)
        
        left_layout.addWidget(backtest_group)
        
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
        
        # Tab 2: 交易记录
        self.trade_table = QTableWidget()
        self.trade_table.setColumnCount(7)
        self.trade_table.setHorizontalHeaderLabels([
            "日期", "标的", "操作", "价格", "数量", "手续费", "剩余资金"
        ])
        self.result_tabs.addTab(self.trade_table, "交易记录")
        
        # Tab 3: 每日得分
        self.score_table = QTableWidget()
        self.result_tabs.addTab(self.score_table, "每日得分")
        
        # Tab 4: 回测报告
        self.stats_label = QLabel("暂无结果")
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.stats_label.setStyleSheet("font-family: Consolas, monospace;")
        self.result_tabs.addTab(self.stats_label, "回测报告")
        
        # Tab 5: 运行日志
        self.log_text = QLabel("等待开始...")
        self.log_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        self.log_text.setWordWrap(True)
        self.result_tabs.addTab(self.log_text, "运行日志")
        
        right_layout.addWidget(self.result_tabs)
        
        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 700])
        
        layout.addWidget(splitter)
    
    def check_data_available(self):
        """检查ETF数据是否可用"""
        import os
        import glob
        
        # 检查ETF数据目录
        if not os.path.exists(self.etf_data_dir):
            msg = f"ETF数据目录不存在: {self.etf_data_dir}\n\n"
            msg += f"请确保ETF数据已下载到 data/etf/ 目录\n"
            msg += "运行命令: python fetch_etf_data.py"
            self.stats_label.setText(msg)
            self.run_btn.setEnabled(False)
            return
        
        # 检查有哪些ETF数据文件
        parquet_files = glob.glob(os.path.join(self.etf_data_dir, "*.parquet"))
        available_etfs = [os.path.basename(f).replace('.parquet', '') for f in parquet_files]
        
        # 检查默认ETF池的数据是否存在
        missing = []
        for i in range(self.etf_list.count()):
            item = self.etf_list.item(i)
            code = item.data(Qt.ItemDataRole.UserRole)
            if code not in available_etfs:
                missing.append(code)
                # 灰色显示缺失数据的ETF
                item.setForeground(QColor(128, 128, 128))
                item.setToolTip(f"数据文件不存在: etf/{code}.parquet")
            else:
                item.setForeground(QColor(255, 255, 255))
                item.setToolTip(f"数据就绪: etf/{code}.parquet")
        
        # 显示状态信息
        if missing:
            msg = f"⚠ 数据缺失警告\n\n以下ETF数据文件不存在:\n"
            for code in missing:
                msg += f"  - etf/{code}.parquet\n"
            msg += f"\nETF数据目录: {self.etf_data_dir}\n"
            msg += f"可用ETF数据: {', '.join(available_etfs[:10])}{'...' if len(available_etfs) > 10 else ''}\n"
            msg += "\n请运行以下命令下载数据:\n"
            msg += "python fetch_etf_data.py"
            self.stats_label.setText(msg)
        else:
            self.stats_label.setText(f"✓ 所有ETF数据已就绪\n\nETF数据目录: {self.etf_data_dir}")
    
    def run_backtest(self):
        """开始回测"""
        if self.backtest_thread and self.backtest_thread.isRunning():
            return
        
        # 获取选中的ETF
        selected_etfs = []
        for i in range(self.etf_list.count()):
            item = self.etf_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_etfs.append(item.data(Qt.ItemDataRole.UserRole))
        
        if len(selected_etfs) < 2:
            QMessageBox.warning(self, "提示", "请至少选择2只ETF进行轮动回测")
            return
        
        # 收集参数
        params = {
            'etf_pool': selected_etfs,
            'bias_weight': self.bias_weight_spin.value(),
            'slope_weight': self.slope_weight_spin.value(),
            'efficiency_weight': self.efficiency_weight_spin.value(),
            'rebalance_threshold': self.threshold_spin.value(),
            'momentum_window': self.momentum_window_spin.value(),
            'zscore_window': self.zscore_window_spin.value(),
        }
        
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        initial_cash = self.capital_spin.value()
        
        # 清空结果
        self.chart_widget.clear()
        self.trade_table.setRowCount(0)
        self.stats_label.setText("正在运行...")
        self.log_text.setText("")
        
        self.run_btn.setEnabled(False)
        self.run_btn.setText("回测中...")
        
        # 启动线程
        self.backtest_thread = ETFBacktestThread(
            selected_etfs, start_date, end_date, 
            initial_cash, self.data_dir, params
        )
        self.backtest_thread.progress_updated.connect(self.on_progress)
        self.backtest_thread.finished_signal.connect(self.on_finished)
        self.backtest_thread.error_signal.connect(self.on_error)
        self.backtest_thread.log_signal.connect(self.on_log)
        self.backtest_thread.start()
    
    def on_progress(self, current, total):
        """进度更新"""
        pass  # 可以在这里添加进度条
    
    def on_log(self, msg):
        """日志输出"""
        current = self.log_text.text()
        self.log_text.setText(current + "\n" + msg)
    
    def on_finished(self, result):
        """回测完成"""
        self.run_btn.setEnabled(True)
        self.run_btn.setText("开始回测")
        
        # 1. 绘制资金曲线
        equity_df = result['equity_curve']
        if not equity_df.empty:
            x = range(len(equity_df))
            y = equity_df['total_asset'].values
            
            self.chart_widget.plot(x, y, pen=pg.mkPen('b', width=2), name="策略净值")
            
            # 绘制持仓切换点
            holdings = equity_df['holding'].values
            for i in range(1, len(holdings)):
                if holdings[i] != holdings[i-1] and holdings[i] is not None:
                    # 在切换点画标记
                    self.chart_widget.plot([i], [y[i]], 
                        pen=None, symbol='o', symbolSize=8, 
                        symbolBrush=('r' if i > 0 else 'g'))
            
            # 设置X轴标签
            ax = self.chart_widget.getAxis('bottom')
            dates = equity_df['date'].astype(str).tolist()
            ticks = []
            n = max(1, len(dates) // 10)
            for i in range(0, len(dates), n):
                ticks.append((i, dates[i][:10]))
            ax.setTicks([ticks])
        
        # 2. 填充交易记录
        trades = result['trades']
        self.trade_table.setRowCount(len(trades))
        for i, t in enumerate(trades):
            self.trade_table.setItem(i, 0, QTableWidgetItem(str(t.date)))
            self.trade_table.setItem(i, 1, QTableWidgetItem(t.symbol))
            
            action_item = QTableWidgetItem(t.action)
            if t.action == 'BUY':
                action_item.setForeground(QColor("red"))
            else:
                action_item.setForeground(QColor("green"))
            self.trade_table.setItem(i, 2, action_item)
            
            self.trade_table.setItem(i, 3, QTableWidgetItem(f"{t.price:.3f}"))
            self.trade_table.setItem(i, 4, QTableWidgetItem(str(t.quantity)))
            self.trade_table.setItem(i, 5, QTableWidgetItem(f"{t.commission:.2f}"))
            self.trade_table.setItem(i, 6, QTableWidgetItem(f"{t.cash_after:.2f}"))
        
        # 3. 填充每日得分表
        scores_df = result['daily_scores']
        if not scores_df.empty:
            etf_codes = [c for c in scores_df.columns if c != 'date']
            self.score_table.setColumnCount(len(etf_codes) + 1)
            self.score_table.setHorizontalHeaderLabels(['日期'] + etf_codes)
            
            self.score_table.setRowCount(len(scores_df))
            for i, row in scores_df.iterrows():
                self.score_table.setItem(i, 0, QTableWidgetItem(str(row['date'])))
                for j, code in enumerate(etf_codes):
                    val = row.get(code)
                    if pd.notna(val):
                        item = QTableWidgetItem(f"{val:.4f}")
                        # 标记最高得分
                        if val == max([row.get(c, 0) for c in etf_codes if pd.notna(row.get(c))]):
                            item.setBackground(QColor(100, 200, 100, 100))
                        self.score_table.setItem(i, j + 1, item)
        
        # 4. 统计报告
        final_value = result['final_value']
        init_cash = self.capital_spin.value()
        ret = (final_value - init_cash) / init_cash * 100
        
        # 计算年化收益
        equity_df = result['equity_curve']
        if not equity_df.empty:
            start_date = pd.to_datetime(equity_df['date'].iloc[0])
            end_date = pd.to_datetime(equity_df['date'].iloc[-1])
            years = (end_date - start_date).days / 365.25
            annual_ret = ((final_value / init_cash) ** (1/years) - 1) * 100 if years > 0 else 0
            
            # 计算最大回撤
            equity_df['peak'] = equity_df['total_asset'].cummax()
            equity_df['drawdown'] = (equity_df['total_asset'] - equity_df['peak']) / equity_df['peak']
            max_dd = equity_df['drawdown'].min() * 100
        else:
            annual_ret = 0
            max_dd = 0
        
        closed_trades = result['closed_trades']
        win_count = sum(1 for t in closed_trades if t.pnl > 0)
        total_closed = len(closed_trades)
        
        report = f"""
=== ETF三因子轮动策略回测报告 ===

【回测参数】
ETF池: {', '.join(result['params']['etf_pool'])}
权重: 乖离{result['params']['bias_weight']}, 斜率{result['params']['slope_weight']}, 效率{result['params']['efficiency_weight']}
调仓阈值: {result['params']['rebalance_threshold']}
动量窗口: {result['params']['momentum_window']}天

【回测结果】
初始资金: {init_cash:,.2f}
最终资产: {final_value:,.2f}
总收益率: {ret:+.2f}%
年化收益: {annual_ret:+.2f}%
最大回撤: {max_dd:.2f}%

【交易统计】
交易次数: {len(trades)}
平仓次数: {total_closed}
盈利次数: {win_count}
胜率: {(win_count/total_closed*100) if total_closed > 0 else 0:.2f}%
        """
        self.stats_label.setText(report)
    
    def on_error(self, msg):
        """错误处理"""
        self.run_btn.setEnabled(True)
        self.run_btn.setText("开始回测")
        self.stats_label.setText(f"错误: {msg}")
        QMessageBox.critical(self, "回测失败", msg)
