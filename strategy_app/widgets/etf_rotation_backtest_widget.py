"""
ETF轮动策略回测组件

专门为ETF多因子轮动策略设计的回测界面，支持同时回测多个ETF标的
"""
import os
import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, 
    QTableWidget, QTableWidgetItem, QLabel, QHeaderView,
    QSplitter, QGroupBox, QDateEdit, QSpinBox, QMessageBox, 
    QTabWidget, QListWidget, QListWidgetItem, QCheckBox,
    QDoubleSpinBox, QGridLayout, QScrollArea
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
from strategies.etf_three_factor_momentum_strategy_fast import ETFThreeFactorMomentumStrategyFast
from data_loader import load_stock_data, get_etf_list, load_etf_name_map
from factors.registry import factor_registry
import factors.etf_momentum_factors_optimized  # noqa: F401 - trigger registration


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
            from datetime import timedelta
            
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
            
            # 计算预热期：需要向前多加载数据以填充滚动窗口
            momentum_window = self.params.get('momentum_window', 25)
            zscore_window = self.params.get('zscore_window', 60)
            warmup_trading_days = momentum_window + zscore_window
            warmup_calendar_days = int(warmup_trading_days * 1.5) + 15
            warmup_start = (pd.to_datetime(self.start_date) - timedelta(days=warmup_calendar_days)).strftime('%Y-%m-%d')
            self.log_signal.emit(f"预热期: 向前加载 {warmup_trading_days} 个交易日数据 (从 {warmup_start} 开始)")
            
            # 1. 加载所有ETF数据（含预热期）
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
                    self.etf_data_dir,
                    start_date=warmup_start, 
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
        all_dates = all_data[base_code]['date'].tolist()
        
        # 过滤：回测只在用户指定的起止日期内运行（预热期数据仅用于因子计算）
        actual_start = pd.to_datetime(self.start_date)
        dates = [d for d in all_dates if d >= actual_start]
        
        self.log_signal.emit(f"回测区间: {len(dates)} 个交易日 (预热期数据: {len(all_dates) - len(dates)} 天)")
        
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
    
    # Default ETF pool (commonly used for momentum rotation)
    DEFAULT_ETF_POOL = [
        ('510880', '红利ETF'),
        ('159949', '创业板50ETF'),
        ('513100', '纳指ETF'),
        ('518880', '黄金ETF'),
    ]
    
    # Extended ETF candidates for user selection
    EXTENDED_ETF_POOL = [
        ('510300', '沪深300ETF'),
        ('510500', '中证500ETF'),
        ('159915', '创业板ETF'),
        ('512100', '中证1000ETF'),
        ('159901', '深证100ETF'),
        ('510050', '上证50ETF'),
        ('512010', '医药ETF'),
        ('512880', '证券ETF'),
        ('515180', '红利ETF基金'),
        ('512690', '酒ETF'),
        ('512480', '半导体ETF'),
        ('515790', '光伏ETF'),
        ('512660', '军工ETF'),
        ('159869', '游戏ETF'),
        ('513050', '中概互联ETF'),
        ('159941', '纳指ETF(QDII)'),
        ('513500', '标普500ETF'),
        ('518800', '黄金基金ETF'),
        ('511010', '国债ETF'),
        ('511260', '十年国债ETF'),
    ]
    
    def __init__(self, data_dir="../data"):
        super().__init__()
        self.data_dir = data_dir
        self.etf_data_dir = os.path.join(data_dir, "etf")  # ETF data subdirectory
        self.backtest_thread = None
        
        # Load ETF name map from config
        self.etf_name_map = load_etf_name_map()
        # Scan available ETF data files
        self.available_etfs = set(get_etf_list(data_dir))
        
        self.setupUI()
        self.check_data_available()
    
    def setupUI(self):
        layout = QHBoxLayout(self)
        
        # --- 左侧设置面板（带滚动条） ---
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet("QScrollArea { border: none; }")
        
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 10, 0)
        left_layout.setSpacing(6)
        
        # 1. ETF selection
        etf_group = QGroupBox("ETF标的池")
        etf_layout = QVBoxLayout(etf_group)
        
        # Batch operation buttons
        btn_row = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setFixedHeight(24)
        self.select_all_btn.clicked.connect(self._on_select_all)
        btn_row.addWidget(self.select_all_btn)
        
        self.deselect_all_btn = QPushButton("全不选")
        self.deselect_all_btn.setFixedHeight(24)
        self.deselect_all_btn.clicked.connect(self._on_deselect_all)
        btn_row.addWidget(self.deselect_all_btn)
        
        self.select_default_btn = QPushButton("默认")
        self.select_default_btn.setFixedHeight(24)
        self.select_default_btn.setToolTip("恢复默认的4只ETF选中状态")
        self.select_default_btn.clicked.connect(self._on_select_default)
        btn_row.addWidget(self.select_default_btn)
        
        etf_layout.addLayout(btn_row)
        
        # ETF list with checkboxes
        self.etf_list = QListWidget()
        self.etf_list.setMinimumHeight(300)
        self.etf_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.etf_list.itemChanged.connect(self._update_etf_info)
        
        # Build merged ETF pool: default (checked) + extended (unchecked)
        default_codes = {code for code, _ in self.DEFAULT_ETF_POOL}
        added_codes = set()
        
        # Add default ETFs first (checked)
        for code, name in self.DEFAULT_ETF_POOL:
            self._add_etf_item(code, name, checked=True)
            added_codes.add(code)
        
        # Add extended ETFs (unchecked)
        for code, name in self.EXTENDED_ETF_POOL:
            if code not in added_codes:
                self._add_etf_item(code, name, checked=False)
                added_codes.add(code)
        
        # Add any other ETFs found in data directory but not in predefined pools
        for code in sorted(self.available_etfs):
            if code not in added_codes:
                name = self.etf_name_map.get(code, '')
                self._add_etf_item(code, name, checked=False)
                added_codes.add(code)
        
        etf_layout.addWidget(self.etf_list)
        
        # Custom ETF input row
        custom_row = QHBoxLayout()
        self.custom_etf_input = QComboBox()
        self.custom_etf_input.setEditable(True)
        self.custom_etf_input.setPlaceholderText("输入ETF代码添加")
        self.custom_etf_input.lineEdit().setPlaceholderText("输入ETF代码添加")
        custom_row.addWidget(self.custom_etf_input, 1)
        
        self.add_etf_btn = QPushButton("+")
        self.add_etf_btn.setFixedSize(28, 28)
        self.add_etf_btn.setToolTip("添加自定义ETF")
        self.add_etf_btn.clicked.connect(self._on_add_custom_etf)
        custom_row.addWidget(self.add_etf_btn)
        
        self.remove_etf_btn = QPushButton("-")
        self.remove_etf_btn.setFixedSize(28, 28)
        self.remove_etf_btn.setToolTip("删除选中的ETF")
        self.remove_etf_btn.clicked.connect(self._on_remove_selected_etf)
        custom_row.addWidget(self.remove_etf_btn)
        
        etf_layout.addLayout(custom_row)
        
        # ETF count info label
        self.etf_info_label = QLabel()
        self.etf_info_label.setStyleSheet("color: #888; font-size: 11px;")
        self._update_etf_info()
        etf_layout.addWidget(self.etf_info_label)
        
        left_layout.addWidget(etf_group)
        
        # 2. 因子配置（紧凑网格布局）
        factor_group = QGroupBox("因子配置（权重之和建议为1.0）")
        factor_grid = QGridLayout(factor_group)
        factor_grid.setSpacing(2)
        factor_grid.setContentsMargins(6, 4, 6, 4)

        ETF_FACTOR_NAMES = {
            'bias_momentum_fast': '乖离动量',
            'slope_momentum_fast': '斜率动量',
            'efficiency_momentum_fast': '效率动量',
            'risk_adjusted_momentum': '风险调整动量',
            'inverse_volatility': '反向波动率',
            'volume_price_correlation': '量价相关性',
        }

        default_factors = {name for name, _ in ETFThreeFactorMomentumStrategyFast.DEFAULT_FACTOR_CONFIG}
        default_weights = {name: w for name, w in ETFThreeFactorMomentumStrategyFast.DEFAULT_FACTOR_CONFIG}

        self._factor_rows = []
        row_idx = 0
        for fname, display_name in ETF_FACTOR_NAMES.items():
            if factor_registry.get(fname) is None:
                continue
            info = factor_registry.get_factor_info(fname)
            is_default = fname in default_factors

            chk = QCheckBox(display_name)
            chk.setChecked(is_default)
            chk.setToolTip(f"{info['description']}\n注册名: {fname}")
            factor_grid.addWidget(chk, row_idx, 0)

            weight_spin = QDoubleSpinBox()
            weight_spin.setRange(0, 5)
            weight_spin.setSingleStep(0.05)
            weight_spin.setDecimals(2)
            weight_spin.setValue(default_weights.get(fname, 0.2))
            weight_spin.setEnabled(is_default)
            weight_spin.setFixedWidth(65)
            chk.stateChanged.connect(lambda state, ws=weight_spin: ws.setEnabled(state == Qt.CheckState.Checked.value))
            factor_grid.addWidget(weight_spin, row_idx, 1)

            self._factor_rows.append((fname, chk, weight_spin))
            row_idx += 1

        self.factor_weight_info = QLabel()
        self.factor_weight_info.setStyleSheet("color: #888; font-size: 11px;")
        self._update_factor_weight_info()
        factor_grid.addWidget(self.factor_weight_info, row_idx, 0, 1, 2)

        for _, chk, ws in self._factor_rows:
            chk.stateChanged.connect(lambda *_: self._update_factor_weight_info())
            ws.valueChanged.connect(lambda *_: self._update_factor_weight_info())

        left_layout.addWidget(factor_group)

        # 3. 策略参数
        param_group = QGroupBox("策略参数")
        param_layout = QGridLayout(param_group)
        
        row = 0
        
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
        row += 1
        
        # 调仓周期
        param_layout.addWidget(QLabel("调仓周期:"), row, 0)
        self.rebalance_period_combo = QComboBox()
        self.rebalance_period_combo.addItem("每日 (1天)", 1)
        self.rebalance_period_combo.addItem("每2天", 2)
        self.rebalance_period_combo.addItem("每3天", 3)
        self.rebalance_period_combo.addItem("每周 (5天)", 5)
        self.rebalance_period_combo.addItem("每两周 (10天)", 10)
        self.rebalance_period_combo.addItem("每月 (20天)", 20)
        self.rebalance_period_combo.setToolTip(
            "调仓检查的间隔（交易日）\n"
            "例如选5天，则每5个交易日才判断一次是否调仓\n"
            "空仓信号不受此限制，任何时候都会触发"
        )
        self.rebalance_period_combo.setCurrentIndex(0)
        param_layout.addWidget(self.rebalance_period_combo, row, 1)
        row += 1
        
        # Enable empty position signal checkbox
        self.enable_empty_check = QCheckBox("启用空仓信号")
        self.enable_empty_check.setChecked(True)
        self.enable_empty_check.setToolTip("当所有ETF综合得分低于阈值时清仓持有现金，避免系统性下跌")
        self.enable_empty_check.stateChanged.connect(self._on_empty_check_changed)
        param_layout.addWidget(self.enable_empty_check, row, 0, 1, 2)
        row += 1
        
        # Empty position threshold
        param_layout.addWidget(QLabel("空仓阈值:"), row, 0)
        self.empty_threshold_spin = QDoubleSpinBox()
        self.empty_threshold_spin.setRange(-3.0, 1.0)
        self.empty_threshold_spin.setSingleStep(0.1)
        self.empty_threshold_spin.setValue(-0.5)
        self.empty_threshold_spin.setDecimals(2)
        self.empty_threshold_spin.setToolTip(
            "所有ETF的综合Z-Score得分都低于此值时触发空仓\n"
            "值越大越容易触发空仓（保守），值越小越难触发（激进）\n"
            "建议范围: -1.0 ~ 0.0"
        )
        param_layout.addWidget(self.empty_threshold_spin, row, 1)
        row += 1
        
        # === 风控参数分隔 ===
        risk_label = QLabel("风控参数")
        risk_label.setStyleSheet("color: #4FC3F7; font-weight: bold; margin-top: 6px;")
        param_layout.addWidget(risk_label, row, 0, 1, 2)
        row += 1
        
        # 移动止盈
        self.enable_trailing_stop_check = QCheckBox("启用移动止盈")
        self.enable_trailing_stop_check.setChecked(True)
        self.enable_trailing_stop_check.setToolTip(
            "跟踪持仓ETF的最高价，从最高价回撤超过阈值时自动卖出\n"
            "适合防止单只ETF的趋势反转造成大幅亏损"
        )
        self.enable_trailing_stop_check.stateChanged.connect(self._on_trailing_stop_check_changed)
        param_layout.addWidget(self.enable_trailing_stop_check, row, 0, 1, 2)
        row += 1
        
        param_layout.addWidget(QLabel("止盈回撤%:"), row, 0)
        self.trailing_stop_spin = QDoubleSpinBox()
        self.trailing_stop_spin.setRange(1.0, 30.0)
        self.trailing_stop_spin.setSingleStep(1.0)
        self.trailing_stop_spin.setValue(8.0)
        self.trailing_stop_spin.setDecimals(1)
        self.trailing_stop_spin.setSuffix("%")
        self.trailing_stop_spin.setToolTip(
            "持仓ETF从最高价回撤超过此比例时卖出\n"
            "值越小越灵敏（频繁止盈），值越大越宽松\n"
            "建议范围: 5% ~ 15%"
        )
        param_layout.addWidget(self.trailing_stop_spin, row, 1)
        row += 1
        
        # 账户最大回撤保护
        self.enable_drawdown_check = QCheckBox("启用账户回撤保护")
        self.enable_drawdown_check.setChecked(True)
        self.enable_drawdown_check.setToolTip(
            "监控账户总净值，从历史最高点回撤超过阈值时清仓并暂停交易\n"
            "适合防止系统性风险造成的持续亏损"
        )
        self.enable_drawdown_check.stateChanged.connect(self._on_drawdown_check_changed)
        param_layout.addWidget(self.enable_drawdown_check, row, 0, 1, 2)
        row += 1
        
        param_layout.addWidget(QLabel("最大回撤%:"), row, 0)
        self.max_drawdown_spin = QDoubleSpinBox()
        self.max_drawdown_spin.setRange(5.0, 50.0)
        self.max_drawdown_spin.setSingleStep(1.0)
        self.max_drawdown_spin.setValue(15.0)
        self.max_drawdown_spin.setDecimals(1)
        self.max_drawdown_spin.setSuffix("%")
        self.max_drawdown_spin.setToolTip(
            "账户总资产从历史最高点回撤超过此比例时触发熔断\n"
            "清仓后进入冷却期，期间不交易\n"
            "建议范围: 10% ~ 25%"
        )
        param_layout.addWidget(self.max_drawdown_spin, row, 1)
        row += 1
        
        param_layout.addWidget(QLabel("冷却天数:"), row, 0)
        self.cooldown_days_spin = QSpinBox()
        self.cooldown_days_spin.setRange(0, 60)
        self.cooldown_days_spin.setValue(10)
        self.cooldown_days_spin.setSuffix(" 天")
        self.cooldown_days_spin.setToolTip(
            "触发账户回撤保护后暂停交易的天数\n"
            "冷却期结束后恢复正常交易\n"
            "设为0表示不冷却，立即恢复"
        )
        param_layout.addWidget(self.cooldown_days_spin, row, 1)
        
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
        
        # Tab 1: 资金曲线 + 回撤曲线
        self.chart_layout_widget = pg.GraphicsLayoutWidget()
        self.chart_layout_widget.setBackground('#1e1e1e')
        
        self.equity_plot = self.chart_layout_widget.addPlot(row=0, col=0)
        self.equity_plot.showGrid(x=True, y=True, alpha=0.3)
        self.equity_plot.setLabel('left', '总资产')
        self.equity_plot.addLegend()
        
        self.drawdown_plot = self.chart_layout_widget.addPlot(row=1, col=0)
        self.drawdown_plot.showGrid(x=True, y=True, alpha=0.3)
        self.drawdown_plot.setLabel('left', '回撤 %')
        self.drawdown_plot.setLabel('bottom', '日期')
        
        self.drawdown_plot.setXLink(self.equity_plot)
        
        self.chart_layout_widget.ci.layout.setRowStretchFactor(0, 3)
        self.chart_layout_widget.ci.layout.setRowStretchFactor(1, 1)
        
        self.result_tabs.addTab(self.chart_layout_widget, "资金曲线")
        
        # Tab 2: 交易记录
        self.trade_table = QTableWidget()
        self.trade_table.setColumnCount(8)
        self.trade_table.setHorizontalHeaderLabels([
            "日期", "标的", "操作", "价格", "数量", "手续费", "剩余资金", "信号类型"
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
        
        # Set scroll area content
        left_scroll.setWidget(left_panel)
        
        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 700])
        
        layout.addWidget(splitter)
    
    def _add_etf_item(self, code: str, name: str, checked: bool = False):
        """Add an ETF item to the list widget"""
        has_data = code in self.available_etfs
        # Use name from name_map if available
        display_name = name or self.etf_name_map.get(code, '')
        
        if display_name:
            display_text = f"{code} {display_name}"
        else:
            display_text = f"{code}"
        
        if not has_data:
            display_text += " ⚠"
        
        item = QListWidgetItem(display_text)
        item.setData(Qt.ItemDataRole.UserRole, code)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        
        if has_data:
            item.setForeground(QColor(255, 255, 255))
            item.setToolTip(f"数据就绪: etf/{code}.parquet")
        else:
            item.setForeground(QColor(128, 128, 128))
            item.setToolTip(f"数据文件不存在: etf/{code}.parquet")
        
        self.etf_list.addItem(item)
    
    def _on_select_all(self):
        """Check all ETF items"""
        for i in range(self.etf_list.count()):
            self.etf_list.item(i).setCheckState(Qt.CheckState.Checked)
        self._update_etf_info()
    
    def _on_deselect_all(self):
        """Uncheck all ETF items"""
        for i in range(self.etf_list.count()):
            self.etf_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._update_etf_info()
    
    def _on_select_default(self):
        """Restore default 4 ETF selection"""
        default_codes = {code for code, _ in self.DEFAULT_ETF_POOL}
        for i in range(self.etf_list.count()):
            item = self.etf_list.item(i)
            code = item.data(Qt.ItemDataRole.UserRole)
            if code in default_codes:
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._update_etf_info()
    
    def _on_add_custom_etf(self):
        """Add a custom ETF code to the list"""
        code = self.custom_etf_input.currentText().strip()
        if not code:
            return
        
        # Validate: must be 6-digit number
        if not code.isdigit() or len(code) != 6:
            QMessageBox.warning(self, "提示", "请输入有效的6位ETF代码")
            return
        
        # Check if already in list
        for i in range(self.etf_list.count()):
            if self.etf_list.item(i).data(Qt.ItemDataRole.UserRole) == code:
                # Already exists, just check it
                self.etf_list.item(i).setCheckState(Qt.CheckState.Checked)
                self.etf_list.scrollToItem(self.etf_list.item(i))
                self.custom_etf_input.clearEditText()
                self._update_etf_info()
                return
        
        # Add new item (checked by default)
        name = self.etf_name_map.get(code, '')
        self._add_etf_item(code, name, checked=True)
        self.custom_etf_input.clearEditText()
        self._update_etf_info()
        
        # Scroll to the new item
        self.etf_list.scrollToItem(self.etf_list.item(self.etf_list.count() - 1))
    
    def _on_remove_selected_etf(self):
        """Remove currently highlighted (selected) ETF items from the list"""
        selected_items = self.etf_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先点击选中要删除的ETF条目")
            return
        
        for item in selected_items:
            row = self.etf_list.row(item)
            self.etf_list.takeItem(row)
        
        self._update_etf_info()
    
    def _update_etf_info(self):
        """Update the ETF count info label"""
        total = self.etf_list.count()
        checked = 0
        has_data = 0
        for i in range(total):
            item = self.etf_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked += 1
            code = item.data(Qt.ItemDataRole.UserRole)
            if code in self.available_etfs:
                has_data += 1
        self.etf_info_label.setText(f"共{total}只ETF，已选{checked}只，{has_data}只有数据")
    
    def _update_factor_weight_info(self):
        """更新因子权重合计提示"""
        total = 0.0
        count = 0
        for _, chk, ws in self._factor_rows:
            if chk.isChecked():
                total += ws.value()
                count += 1
        color = "#81C784" if abs(total - 1.0) < 0.01 else "#FFB74D"
        self.factor_weight_info.setText(f"已选 {count} 个因子，权重合计: {total:.2f}")
        self.factor_weight_info.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _get_factor_config_from_ui(self) -> list:
        """从UI因子表中获取因子配置 [(name, weight), ...]"""
        config = []
        for fname, chk, ws in self._factor_rows:
            if chk.isChecked():
                config.append((fname, ws.value()))
        return config

    def _on_empty_check_changed(self, state):
        """Toggle empty position threshold spin box enabled state"""
        self.empty_threshold_spin.setEnabled(state == Qt.CheckState.Checked.value)
    
    def _on_trailing_stop_check_changed(self, state):
        """Toggle trailing stop spin box enabled state"""
        self.trailing_stop_spin.setEnabled(state == Qt.CheckState.Checked.value)
    
    def _on_drawdown_check_changed(self, state):
        """Toggle drawdown protection spin boxes enabled state"""
        enabled = (state == Qt.CheckState.Checked.value)
        self.max_drawdown_spin.setEnabled(enabled)
        self.cooldown_days_spin.setEnabled(enabled)
    
    def check_data_available(self):
        """Check ETF data availability and update UI"""
        import os
        import glob
        
        if not os.path.exists(self.etf_data_dir):
            msg = f"ETF数据目录不存在: {self.etf_data_dir}\n\n"
            msg += f"请确保ETF数据已下载到 data/etf/ 目录\n"
            msg += "运行命令: python fetch_etf_data.py"
            self.stats_label.setText(msg)
            self.run_btn.setEnabled(False)
            return
        
        # Count checked ETFs with/without data
        checked_missing = []
        for i in range(self.etf_list.count()):
            item = self.etf_list.item(i)
            code = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() == Qt.CheckState.Checked and code not in self.available_etfs:
                checked_missing.append(code)
        
        if checked_missing:
            msg = f"⚠ 部分已选ETF数据缺失:\n"
            for code in checked_missing:
                msg += f"  - etf/{code}.parquet\n"
            msg += f"\nETF数据目录: {self.etf_data_dir}\n"
            msg += f"可用ETF数据: {len(self.available_etfs)} 只\n"
            msg += "\n缺失数据的ETF在回测时将被跳过"
            self.stats_label.setText(msg)
        else:
            self.stats_label.setText(f"✓ 已选ETF数据全部就绪\n\nETF数据目录: {self.etf_data_dir}\n可用: {len(self.available_etfs)} 只ETF")
    
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
        
        # 收集因子配置
        factor_config = self._get_factor_config_from_ui()
        if not factor_config:
            QMessageBox.warning(self, "提示", "请至少选择1个因子")
            return

        params = {
            'etf_pool': selected_etfs,
            'factor_config': factor_config,
            'rebalance_threshold': self.threshold_spin.value(),
            'momentum_window': self.momentum_window_spin.value(),
            'zscore_window': self.zscore_window_spin.value(),
            'enable_empty_position': self.enable_empty_check.isChecked(),
            'empty_threshold': self.empty_threshold_spin.value(),
            'rebalance_period': self.rebalance_period_combo.currentData(),
            'enable_trailing_stop': self.enable_trailing_stop_check.isChecked(),
            'trailing_stop_pct': self.trailing_stop_spin.value() / 100.0,
            'enable_drawdown_protection': self.enable_drawdown_check.isChecked(),
            'max_drawdown_pct': self.max_drawdown_spin.value() / 100.0,
            'drawdown_cooldown_days': self.cooldown_days_spin.value(),
        }
        
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        initial_cash = self.capital_spin.value()
        
        # 清空结果
        self.equity_plot.clear()
        self.drawdown_plot.clear()
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
        
        # 1. 绘制资金曲线 + 回撤曲线
        equity_df = result['equity_curve']
        if not equity_df.empty:
            x = list(range(len(equity_df)))
            y = equity_df['total_asset'].values
            
            self.equity_plot.clear()
            self.equity_plot.addLegend()
            self.equity_plot.plot(x, y, pen=pg.mkPen('b', width=2), name="策略净值")
            
            # 绘制持仓切换点
            holdings = equity_df['holding'].values
            for i in range(1, len(holdings)):
                if holdings[i] != holdings[i-1] and holdings[i] is not None:
                    self.equity_plot.plot([i], [y[i]], 
                        pen=None, symbol='o', symbolSize=8, 
                        symbolBrush=('r' if i > 0 else 'g'))
            
            # 计算并绘制回撤曲线
            peak = np.maximum.accumulate(y)
            drawdown_pct = (y - peak) / peak * 100
            
            self.drawdown_plot.clear()
            fill_curve = self.drawdown_plot.plot(x, drawdown_pct, 
                pen=pg.mkPen('#E57373', width=1.5))
            zero_line = self.drawdown_plot.plot(x, np.zeros(len(x)), 
                pen=pg.mkPen('#555555', width=0.5))
            fill = pg.FillBetweenItem(fill_curve, zero_line, 
                brush=pg.mkBrush(229, 115, 115, 60))
            self.drawdown_plot.addItem(fill)
            
            # 设置X轴标签（两个图共用）
            dates = equity_df['date'].astype(str).tolist()
            ticks = []
            n = max(1, len(dates) // 10)
            for i in range(0, len(dates), n):
                ticks.append((i, dates[i][:10]))
            self.equity_plot.getAxis('bottom').setTicks([ticks])
            self.drawdown_plot.getAxis('bottom').setTicks([ticks])
        
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
            
            reason_item = QTableWidgetItem(t.reason or "")
            reason_colors = {
                "调仓": "#4FC3F7",
                "初始建仓": "#81C784",
                "移动止盈": "#FFB74D",
                "回撤保护": "#E57373",
                "空仓信号": "#CE93D8",
            }
            color = reason_colors.get(t.reason, "#AAAAAA")
            reason_item.setForeground(QColor(color))
            self.trade_table.setItem(i, 7, reason_item)
        
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
            
            # Calculate Sharpe Ratio
            daily_returns = equity_df['total_asset'].pct_change().dropna()
            if len(daily_returns) > 0 and daily_returns.std() > 0:
                sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
            else:
                sharpe_ratio = None
        else:
            annual_ret = 0
            max_dd = 0
            sharpe_ratio = None
        
        closed_trades = result['closed_trades']
        win_count = sum(1 for t in closed_trades if t.pnl > 0)
        total_closed = len(closed_trades)
        
        fc = result['params'].get('factor_config', [])
        factor_str = ', '.join(f"{n}({w})" for n, w in fc) if fc else '默认'

        report = f"""
=== ETF动量轮动策略回测报告 ===

【回测参数】
ETF池: {', '.join(result['params']['etf_pool'])}
因子: {factor_str}
调仓阈值: {result['params']['rebalance_threshold']}
调仓周期: 每{result['params'].get('rebalance_period', 1)}个交易日
空仓信号: {'开启' if result['params'].get('enable_empty_position', False) else '关闭'} (阈值: {result['params'].get('empty_threshold', -0.5)})
移动止盈: {'开启 (回撤' + str(result['params'].get('trailing_stop_pct', 0.08)*100) + '%)' if result['params'].get('enable_trailing_stop', False) else '关闭'}
账户回撤保护: {'开启 (回撤' + str(result['params'].get('max_drawdown_pct', 0.15)*100) + '%, 冷却' + str(result['params'].get('drawdown_cooldown_days', 10)) + '天)' if result['params'].get('enable_drawdown_protection', False) else '关闭'}
动量窗口: {result['params']['momentum_window']}天

【回测结果】
初始资金: {init_cash:,.2f}
最终资产: {final_value:,.2f}
总收益率: {ret:+.2f}%
年化收益: {annual_ret:+.2f}%
最大回撤: {max_dd:.2f}%
夏普比率: {f'{sharpe_ratio:.3f}' if sharpe_ratio is not None else 'N/A'}

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
