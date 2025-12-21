from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, 
    QTableWidget, QTableWidgetItem, QProgressBar, QLabel, QHeaderView,
    QMessageBox, QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
try:
    from strategies import get_all_strategies, get_strategy
    from data_loader import get_stock_list, load_stock_data, load_stock_name_map
    from notifier import get_notification_manager
except ImportError:
    from ..strategies import get_all_strategies, get_strategy
    from ..data_loader import get_stock_list, load_stock_data, load_stock_name_map
    from ..notifier import get_notification_manager

class ScreenerThread(QThread):
    """选股后台线程"""
    progress_updated = pyqtSignal(int, int) # current, total
    stock_found = pyqtSignal(dict) # result dict
    finished_signal = pyqtSignal(str) # message

    def __init__(self, strategy_name, data_dir, stocklist_path=None):
        super().__init__()
        self.strategy_name = strategy_name
        self.data_dir = data_dir
        self.stocklist_path = stocklist_path
        self.is_running = True

    def run(self):
        strategy = get_strategy(self.strategy_name)
        if not strategy:
            self.finished_signal.emit("策略未找到")
            return

        stock_list = get_stock_list(self.data_dir)
        # 使用传入的 stocklist_path 加载名称映射
        name_map = load_stock_name_map(self.stocklist_path) if self.stocklist_path else load_stock_name_map()
        total = len(stock_list)
        
        for i, code in enumerate(stock_list):
            if not self.is_running:
                break
                
            self.progress_updated.emit(i + 1, total)
            
            # 加载数据
            df = load_stock_data(code, self.data_dir)
            if df is None or df.empty:
                continue
                
            # 运行策略
            try:
                result = strategy.check(code, df)
                if result:
                    result['name'] = name_map.get(code, code)
                    self.stock_found.emit(result)
            except Exception as e:
                print(f"Error checking {code}: {e}")
                continue
        
        self.finished_signal.emit("选股完成")

    def stop(self):
        self.is_running = False

class StockScreenerWidget(QWidget):
    """选股模块主界面"""
    stockSelected = pyqtSignal(str) # code
    # 信号：选股完成，参数：(策略名称, 股票代码列表)
    strategyFinished = pyqtSignal(str, list)

    def __init__(self, data_dir="../data", stocklist_path=None):
        super().__init__()
        self.data_dir = data_dir
        self.stocklist_path = stocklist_path
        self.screener_thread = None
        self.setupUI()

    def setupUI(self):
        layout = QVBoxLayout(self)
        
        # Top controls
        top_layout = QHBoxLayout()
        
        top_layout.addWidget(QLabel("选择策略:"))
        self.strategy_combo = QComboBox()
        strategies = get_all_strategies()
        for sid, name in strategies.items():
            self.strategy_combo.addItem(name, sid)
        top_layout.addWidget(self.strategy_combo)
        
        self.start_btn = QPushButton("开始选股")
        self.start_btn.clicked.connect(self.toggle_screener)
        top_layout.addWidget(self.start_btn)
        
        self.notify_btn = QPushButton("📤 发送通知")
        self.notify_btn.setToolTip("将选股结果发送到企业微信")
        self.notify_btn.clicked.connect(self.send_notification)
        self.notify_btn.setEnabled(False)
        top_layout.addWidget(self.notify_btn)
        
        # 自动保存复选框
        self.auto_save_cb = QCheckBox("自动同步到自选股分组")
        self.auto_save_cb.setChecked(True)
        self.auto_save_cb.setToolTip("选股完成后，自动将结果更新到以策略命名的自选股分组中")
        top_layout.addWidget(self.auto_save_cb)
        
        top_layout.addStretch()
        layout.addLayout(top_layout)
        
        # Description
        self.desc_label = QLabel("策略说明...")
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color: #888888; font-style: italic; margin: 5px 0;")
        layout.addWidget(self.desc_label)
        self.strategy_combo.currentIndexChanged.connect(self.update_description)
        self.update_description() # Init
        
        # Results table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["代码", "名称", "日期", "收盘价", "说明"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self.on_table_double_click)
        layout.addWidget(self.table)
        
        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("就绪")
        layout.addWidget(self.status_label)

    def update_description(self):
        sid = self.strategy_combo.currentData()
        strategy = get_strategy(sid)
        if strategy:
            self.desc_label.setText(strategy.description)

    def toggle_screener(self):
        if self.screener_thread and self.screener_thread.isRunning():
            self.screener_thread.stop()
            self.start_btn.setText("开始选股")
            self.status_label.setText("已停止")
            return

        self.table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.start_btn.setText("停止")
        self.status_label.setText("正在选股...")
        
        sid = self.strategy_combo.currentData()
        self.screener_thread = ScreenerThread(sid, self.data_dir, self.stocklist_path)
        self.screener_thread.progress_updated.connect(self.on_progress)
        self.screener_thread.stock_found.connect(self.on_stock_found)
        self.screener_thread.finished_signal.connect(self.on_finished)
        self.screener_thread.start()

    def on_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"正在扫描: {current}/{total}")

    def on_stock_found(self, result):
        row = self.table.rowCount()
        self.table.insertRow(row)
        
        self.table.setItem(row, 0, QTableWidgetItem(result['code']))
        self.table.setItem(row, 1, QTableWidgetItem(result['name']))
        self.table.setItem(row, 2, QTableWidgetItem(str(result['date'])))
        self.table.setItem(row, 3, QTableWidgetItem(f"{result['close']:.2f}"))
        self.table.setItem(row, 4, QTableWidgetItem(result.get('info', '')))

    def on_finished(self, msg):
        self.start_btn.setText("开始选股")
        self.progress_bar.setVisible(False)
        count = self.table.rowCount()
        self.status_label.setText(f"{msg} - 共找到 {count} 只股票")
        # 启用发送通知按钮
        self.notify_btn.setEnabled(count > 0)
        
        # 如果开启了自动保存，发出信号
        if self.auto_save_cb.isChecked() and count > 0:
            strategy_name = self.strategy_combo.currentText()
            codes = [self.table.item(i, 0).text() for i in range(count)]
            self.strategyFinished.emit(strategy_name, codes)

    def on_table_double_click(self, item):
        row = item.row()
        code = self.table.item(row, 0).text()
        self.stockSelected.emit(code)
    
    def get_screened_stocks(self):
        """获取选股结果数据列表"""
        stocks = []
        for row in range(self.table.rowCount()):
            stock = {
                "code": self.table.item(row, 0).text(),
                "name": self.table.item(row, 1).text(),
                "date": self.table.item(row, 2).text(),
                "price": float(self.table.item(row, 3).text()),
                "info": self.table.item(row, 4).text() if self.table.item(row, 4) else ""
            }
            stocks.append(stock)
        return stocks
    
    def send_notification(self):
        """发送选股结果通知"""
        raw_stocks = self.get_screened_stocks()
        if not raw_stocks:
            QMessageBox.warning(self, "提示", "没有选股结果可发送")
            return
        
        # 精简数据，只发送代码和名称
        stocks = [{"code": s["code"], "name": s["name"]} for s in raw_stocks]
        
        nm = get_notification_manager()
        
        if not nm.is_enabled():
            QMessageBox.warning(
                self, "提示", 
                "消息推送未启用，请先在「工具 → 消息推送」中配置"
            )
            return
        
        # 获取当前策略名称
        strategy_name = self.strategy_combo.currentText()
        title = f"选股结果 - {strategy_name}"
        
        success, msg = nm.send_stock_alert(title, stocks)
        
        if success:
            QMessageBox.information(self, "成功", f"已发送 {len(stocks)} 只股票到企业微信！")
        else:
            QMessageBox.warning(self, "发送失败", f"发送失败：{msg}")
