import sys
import os
from pathlib import Path
import pandas as pd
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QComboBox, QSpinBox, QDoubleSpinBox, QTabWidget, 
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QGroupBox, QFormLayout, QSplitter, QDateEdit, QFrame,
    QFileDialog, QInputDialog, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QDate
from PyQt6.QtGui import QColor, QBrush, QFont

# 尝试导入 TradingSimulator
# 假设项目根目录已在 sys.path 中
try:
    from trading_simulator import TradingSimulator
except ImportError:
    # 如果直接运行此文件，可能需要添加路径
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from trading_simulator import TradingSimulator

from .kline_widget import KLineWidget
try:
    from data_loader import load_stock_data, load_stock_name_map, get_stock_list
except ImportError:
    # Fallback for relative import if run as a package
    from ..data_loader import load_stock_data, load_stock_name_map, get_stock_list
# from ..indicators import calculate_indicators # 暂时不使用，直接在 update_chart 中简单计算

class TradingSimulatorWidget(QWidget):
    """模拟交易训练组件"""
    
    def __init__(self, data_dir: str = "../data", parent=None):
        super().__init__(parent)
        self.data_dir = data_dir
        self.simulator: Optional[TradingSimulator] = None
        self.stock_map = {}
        
        self.setup_ui()
        self.load_stock_list()
        
    def setup_ui(self):
        """初始化 UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # 1. 顶部控制栏
        control_layout = QHBoxLayout()
        
        # 股票选择
        control_layout.addWidget(QLabel("股票:"))
        self.stock_combo = QComboBox()
        self.stock_combo.setMinimumWidth(150)
        control_layout.addWidget(self.stock_combo)
        
        # 初始资金
        control_layout.addWidget(QLabel("初始资金:"))
        self.capital_spin = QDoubleSpinBox()
        self.capital_spin.setRange(10000, 100000000)
        self.capital_spin.setValue(1000000)
        self.capital_spin.setSingleStep(10000)
        self.capital_spin.setPrefix("¥")
        control_layout.addWidget(self.capital_spin)
        
        # 开始日期
        control_layout.addWidget(QLabel("开始日期:"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate().addYears(-1))
        control_layout.addWidget(self.date_edit)
        
        # 按钮
        self.start_btn = QPushButton("开始训练")
        self.start_btn.clicked.connect(self.start_simulation)
        control_layout.addWidget(self.start_btn)
        
        self.load_btn = QPushButton("加载进度")
        self.load_btn.clicked.connect(self.load_progress)
        control_layout.addWidget(self.load_btn)
        
        self.save_btn = QPushButton("保存进度")
        self.save_btn.clicked.connect(self.save_progress)
        self.save_btn.setEnabled(False)
        control_layout.addWidget(self.save_btn)
        
        control_layout.addStretch()
        main_layout.addLayout(control_layout)
        
        # 2. 主体区域 (分割器)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # 左侧：K线图和日期控制
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        # 日期控制栏
        date_control_layout = QHBoxLayout()
        self.prev_day_btn = QPushButton("上一日")
        self.prev_day_btn.clicked.connect(self.prev_day)
        self.prev_day_btn.setEnabled(False)
        
        self.current_date_label = QLabel("当前日期: -")
        self.current_date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_date_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        self.next_day_btn = QPushButton("下一日")
        self.next_day_btn.clicked.connect(self.next_day)
        self.next_day_btn.setEnabled(False)
        
        date_control_layout.addWidget(self.prev_day_btn)
        date_control_layout.addWidget(self.current_date_label, 1)
        date_control_layout.addWidget(self.next_day_btn)
        
        left_layout.addLayout(date_control_layout)
        
        # K线图
        self.kline_widget = KLineWidget()
        left_layout.addWidget(self.kline_widget)
        
        splitter.addWidget(left_widget)
        
        # 右侧：账户信息和交易面板
        right_widget = QWidget()
        right_widget.setFixedWidth(350)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # 账户信息
        account_group = QGroupBox("账户信息")
        account_layout = QFormLayout(account_group)
        
        self.lbl_initial_capital = QLabel("-")
        self.lbl_available_cash = QLabel("-")
        self.lbl_market_value = QLabel("-")
        self.lbl_total_assets = QLabel("-")
        self.lbl_profit_loss = QLabel("-")
        
        account_layout.addRow("初始资金:", self.lbl_initial_capital)
        account_layout.addRow("可用资金:", self.lbl_available_cash)
        account_layout.addRow("持仓市值:", self.lbl_market_value)
        account_layout.addRow("总资产:", self.lbl_total_assets)
        account_layout.addRow("总盈亏:", self.lbl_profit_loss)
        
        right_layout.addWidget(account_group)
        
        # 交易面板
        trade_group = QGroupBox("交易操作")
        trade_layout = QVBoxLayout(trade_group)
        
        self.trade_tabs = QTabWidget()
        
        # 买入 Tab
        buy_tab = QWidget()
        buy_layout = QFormLayout(buy_tab)
        
        self.buy_price_label = QLabel("-")
        self.buy_qty_spin = QSpinBox()
        self.buy_qty_spin.setRange(0, 1000000)
        self.buy_qty_spin.setSingleStep(100)
        self.buy_qty_spin.setSuffix(" 股")
        self.buy_qty_spin.valueChanged.connect(self.update_buy_preview)
        
        # 快捷按钮
        buy_quick_layout = QHBoxLayout()
        btn_buy_all = QPushButton("全仓")
        btn_buy_half = QPushButton("1/2")
        btn_buy_third = QPushButton("1/3")
        
        btn_buy_all.clicked.connect(lambda: self.quick_set_qty(1.0, True))
        btn_buy_half.clicked.connect(lambda: self.quick_set_qty(0.5, True))
        btn_buy_third.clicked.connect(lambda: self.quick_set_qty(0.33, True))
        
        buy_quick_layout.addWidget(btn_buy_all)
        buy_quick_layout.addWidget(btn_buy_half)
        buy_quick_layout.addWidget(btn_buy_third)
        
        self.buy_total_label = QLabel("-")
        self.buy_btn = QPushButton("买入")
        self.buy_btn.setStyleSheet("background-color: #ec0000; color: white; font-weight: bold;")
        self.buy_btn.clicked.connect(self.on_buy)
        
        buy_layout.addRow("当前价格:", self.buy_price_label)
        buy_layout.addRow("买入数量:", self.buy_qty_spin)
        buy_layout.addRow("", buy_quick_layout)
        buy_layout.addRow("预计金额:", self.buy_total_label)
        buy_layout.addRow(self.buy_btn)
        
        self.trade_tabs.addTab(buy_tab, "买入")
        
        # 卖出 Tab
        sell_tab = QWidget()
        sell_layout = QFormLayout(sell_tab)
        
        self.sell_price_label = QLabel("-")
        self.sell_qty_spin = QSpinBox()
        self.sell_qty_spin.setRange(0, 1000000)
        self.sell_qty_spin.setSingleStep(100)
        self.sell_qty_spin.setSuffix(" 股")
        self.sell_qty_spin.valueChanged.connect(self.update_sell_preview)
        
        # 快捷按钮
        sell_quick_layout = QHBoxLayout()
        btn_sell_all = QPushButton("全仓")
        btn_sell_half = QPushButton("1/2")
        btn_sell_third = QPushButton("1/3")
        
        btn_sell_all.clicked.connect(lambda: self.quick_set_qty(1.0, False))
        btn_sell_half.clicked.connect(lambda: self.quick_set_qty(0.5, False))
        btn_sell_third.clicked.connect(lambda: self.quick_set_qty(0.33, False))
        
        sell_quick_layout.addWidget(btn_sell_all)
        sell_quick_layout.addWidget(btn_sell_half)
        sell_quick_layout.addWidget(btn_sell_third)
        
        self.sell_total_label = QLabel("-")
        self.sell_btn = QPushButton("卖出")
        self.sell_btn.setStyleSheet("background-color: #00da3c; color: white; font-weight: bold;")
        self.sell_btn.clicked.connect(self.on_sell)
        
        sell_layout.addRow("当前价格:", self.sell_price_label)
        sell_layout.addRow("卖出数量:", self.sell_qty_spin)
        sell_layout.addRow("", sell_quick_layout)
        sell_layout.addRow("预计金额:", self.sell_total_label)
        sell_layout.addRow(self.sell_btn)
        
        self.trade_tabs.addTab(sell_tab, "卖出")
        
        trade_layout.addWidget(self.trade_tabs)
        right_layout.addWidget(trade_group)
        
        # 持仓和记录
        info_tabs = QTabWidget()
        
        # 持仓列表
        self.position_table = QTableWidget()
        self.position_table.setColumnCount(4)
        self.position_table.setHorizontalHeaderLabels(["股票", "数量", "成本", "盈亏"])
        self.position_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        info_tabs.addTab(self.position_table, "持仓")
        
        # 交易记录
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(5)
        self.history_table.setHorizontalHeaderLabels(["时间", "操作", "价格", "数量", "金额"])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        info_tabs.addTab(self.history_table, "记录")
        
        right_layout.addWidget(info_tabs)
        
        splitter.addWidget(right_widget)
        
        # 设置分割器比例
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)

    def load_stock_list(self):
        """加载股票列表"""
        self.stock_map = load_stock_name_map()
        stock_codes = get_stock_list(self.data_dir)
        
        self.stock_combo.clear()
        for code in stock_codes:
            name = self.stock_map.get(code, code)
            self.stock_combo.addItem(f"{code} {name}", code)

    def start_simulation(self):
        """开始模拟"""
        stock_code = self.stock_combo.currentData()
        if not stock_code:
            return
            
        stock_name = self.stock_map.get(stock_code, stock_code)
        initial_capital = self.capital_spin.value()
        start_date = self.date_edit.date().toString("yyyy-MM-dd")
        
        # 加载数据
        df = load_stock_data(stock_code, self.data_dir, start_date="2000-01-01") # 加载足够多的历史数据
        if df is None or df.empty:
            QMessageBox.warning(self, "错误", "无法加载股票数据")
            return
            
        # 重命名列以匹配 TradingSimulator
        df = df.rename(columns={
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        })
        df.set_index("Date", inplace=True)
        
        # 创建模拟器
        self.simulator = TradingSimulator(
            stock_code=stock_code,
            stock_name=stock_name,
            data=df,
            initial_capital=initial_capital,
            start_date=start_date
        )
        
        self.update_ui()
        self.save_btn.setEnabled(True)
        self.prev_day_btn.setEnabled(True)
        self.next_day_btn.setEnabled(True)

    def update_ui(self):
        """更新所有 UI"""
        if not self.simulator:
            return
            
        self.update_chart()
        self.update_account_info()
        self.update_positions_table()
        self.update_trade_history_table()
        self.update_trade_panel()
        
        # 更新日期标签
        current_date = self.simulator.current_date.strftime("%Y-%m-%d")
        self.current_date_label.setText(f"当前日期: {current_date}")
        
        # 更新按钮状态
        self.prev_day_btn.setEnabled(self.simulator.can_go_prev)
        self.next_day_btn.setEnabled(self.simulator.can_go_next)

    def update_chart(self):
        """更新 K 线图"""
        if not self.simulator:
            return
            
        # 获取可见数据
        visible_df = self.simulator.visible_data.copy()
        
        # 转换回小写列名以适配 KLineWidget
        visible_df = visible_df.rename(columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume"
        })
        visible_df["date"] = visible_df.index
        
        # 计算指标（如果尚未计算）
        # 这里简单计算 MA
        for w in [5, 10, 20, 60]:
            visible_df[f"MA{w}"] = visible_df["close"].rolling(window=w).mean()
            
        self.kline_widget.set_data(visible_df, self.simulator.stock_code, self.simulator.stock_name)

    def update_account_info(self):
        """更新账户信息"""
        if not self.simulator:
            return
            
        acc = self.simulator.account
        self.lbl_initial_capital.setText(f"¥{acc.initial_capital:,.2f}")
        self.lbl_available_cash.setText(f"¥{acc.available_cash:,.2f}")
        self.lbl_market_value.setText(f"¥{acc.total_market_value:,.2f}")
        self.lbl_total_assets.setText(f"¥{acc.total_assets:,.2f}")
        
        profit = acc.total_profit_loss
        profit_pct = acc.total_profit_loss_pct
        color = "#ec0000" if profit >= 0 else "#00da3c"
        self.lbl_profit_loss.setText(f"¥{profit:,.2f} ({profit_pct:+.2f}%)")
        self.lbl_profit_loss.setStyleSheet(f"color: {color}; font-weight: bold;")

    def update_trade_panel(self):
        """更新交易面板"""
        if not self.simulator:
            return
            
        current_price = self.simulator.current_data['Close']
        self.buy_price_label.setText(f"¥{current_price:.2f}")
        self.sell_price_label.setText(f"¥{current_price:.2f}")
        
        self.update_buy_preview()
        self.update_sell_preview()

    def update_buy_preview(self):
        if not self.simulator:
            return
        qty = self.buy_qty_spin.value()
        price = self.simulator.current_data['Close']
        amount = qty * price
        commission = self.simulator.account.calculate_commission(amount)
        total = amount + commission
        self.buy_total_label.setText(f"¥{total:,.2f}")

    def update_sell_preview(self):
        if not self.simulator:
            return
        qty = self.sell_qty_spin.value()
        price = self.simulator.current_data['Close']
        amount = qty * price
        commission = self.simulator.account.calculate_commission(amount)
        total = amount - commission
        self.sell_total_label.setText(f"¥{total:,.2f}")

    def quick_set_qty(self, ratio: float, is_buy: bool):
        """快速设置数量"""
        if not self.simulator:
            return
            
        price = self.simulator.current_data['Close']
        
        if is_buy:
            max_qty = self.simulator.get_max_buy_quantity(price)
            qty = int(max_qty * ratio / 100) * 100
            self.buy_qty_spin.setValue(qty)
        else:
            max_qty = self.simulator.get_available_quantity()
            qty = int(max_qty * ratio / 100) * 100
            if qty == 0 and max_qty > 0 and max_qty < 100: # 处理不足一手的情况
                 qty = max_qty
            self.sell_qty_spin.setValue(qty)

    def on_buy(self):
        if not self.simulator:
            return
        
        qty = self.buy_qty_spin.value()
        if qty <= 0:
            return

        price = self.simulator.current_data['Close']
        
        success, msg = self.simulator.buy_stock(price, qty)
        if success:
            QMessageBox.information(self, "成功", msg)
            self.update_ui()
            self.buy_qty_spin.setValue(0)
        else:
            QMessageBox.warning(self, "失败", msg)

    def on_sell(self):
        if not self.simulator:
            return
            
        qty = self.sell_qty_spin.value()
        if qty <= 0:
            return

        price = self.simulator.current_data['Close']
        
        success, msg = self.simulator.sell_stock(price, qty)
        if success:
            QMessageBox.information(self, "成功", msg)
            self.update_ui()
            self.sell_qty_spin.setValue(0)
        else:
            QMessageBox.warning(self, "失败", msg)

    def next_day(self):
        if self.simulator and self.simulator.next_day():
            self.update_ui()

    def prev_day(self):
        if self.simulator and self.simulator.prev_day():
            self.update_ui()

    def update_positions_table(self):
        if not self.simulator:
            return
            
        self.position_table.setRowCount(0)
        for code, pos in self.simulator.account.positions.items():
            row = self.position_table.rowCount()
            self.position_table.insertRow(row)
            
            self.position_table.setItem(row, 0, QTableWidgetItem(f"{pos.stock_name}"))
            self.position_table.setItem(row, 1, QTableWidgetItem(str(pos.quantity)))
            self.position_table.setItem(row, 2, QTableWidgetItem(f"{pos.avg_cost:.2f}"))
            
            profit_item = QTableWidgetItem(f"{pos.profit_loss:.2f}")
            if pos.profit_loss >= 0:
                profit_item.setForeground(QBrush(QColor("#ec0000")))
            else:
                profit_item.setForeground(QBrush(QColor("#00da3c")))
            self.position_table.setItem(row, 3, profit_item)

    def update_trade_history_table(self):
        if not self.simulator:
            return
            
        self.history_table.setRowCount(0)
        # 倒序显示
        for trade in reversed(self.simulator.account.trade_history):
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            
            self.history_table.setItem(row, 0, QTableWidgetItem(trade.date))
            
            action_item = QTableWidgetItem(trade.action.value)
            if trade.action.value == "买入":
                action_item.setForeground(QBrush(QColor("#ec0000")))
            else:
                action_item.setForeground(QBrush(QColor("#00da3c")))
            self.history_table.setItem(row, 1, action_item)
            
            self.history_table.setItem(row, 2, QTableWidgetItem(f"{trade.price:.2f}"))
            self.history_table.setItem(row, 3, QTableWidgetItem(str(trade.quantity)))
            self.history_table.setItem(row, 4, QTableWidgetItem(f"{trade.amount:.2f}"))

    def save_progress(self):
        if not self.simulator:
            return
            
        filename, _ = QFileDialog.getSaveFileName(
            self, "保存进度", 
            f"simulator_{self.simulator.stock_code}.json", 
            "JSON Files (*.json)"
        )
        if filename:
            self.simulator.save(filename)
            QMessageBox.information(self, "成功", "进度已保存")

    def load_progress(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "加载进度", "", "JSON Files (*.json)"
        )
        if not filename:
            return
            
        # 需要先读取 JSON 获取股票代码，然后加载数据
        import json
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            stock_code = data.get("stock_code")
            
            # 加载数据
            df = load_stock_data(stock_code, self.data_dir, start_date="2000-01-01")
            if df is None or df.empty:
                QMessageBox.warning(self, "错误", f"无法加载股票 {stock_code} 的数据")
                return
                
            df = df.rename(columns={
                "date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume"
            })
            df.set_index("Date", inplace=True)
            
            self.simulator = TradingSimulator.load(filename, df)
            if self.simulator:
                # 更新 UI 控件状态
                index = self.stock_combo.findData(stock_code)
                if index >= 0:
                    self.stock_combo.setCurrentIndex(index)
                
                self.update_ui()
                self.save_btn.setEnabled(True)
                self.prev_day_btn.setEnabled(True)
                self.next_day_btn.setEnabled(True)
                QMessageBox.information(self, "成功", "进度已加载")
            else:
                QMessageBox.warning(self, "错误", "加载失败")
                
        except Exception as e:
            QMessageBox.warning(self, "错误", f"加载出错: {e}")
