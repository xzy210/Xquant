"""
Broker Account Widget - Query broker account and trading records via xtquant
"""
import os
import json
import random
import logging
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QGroupBox, QFormLayout, QLineEdit, QFrame,
    QSplitter, QFileDialog, QSizePolicy, QTextEdit, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QColor, QBrush, QFont

# Setup logging directory
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'broker_connection.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BrokerConnectThread(QThread):
    """Thread for connecting to broker"""
    connected = pyqtSignal(bool, str)  # success, message
    log_message = pyqtSignal(str)  # progress log message
    
    def __init__(self, qmt_path: str, account: str):
        super().__init__()
        self.qmt_path = qmt_path
        self.account = account
        self.xt_trader = None
        self.acc = None
        
        logger.info("="*60)
        logger.info("开始创建连接线程")
        logger.info(f"QMT路径: {qmt_path}")
        logger.info(f"资金账号: {account}")
        logger.info("="*60)
    
    def _log(self, message: str):
        """Helper method to send log message"""
        logger.info(message)
        self.log_message.emit(message)
    
    def run(self):
        try:
            self._log("【步骤1/6】尝试导入 xtquant 库...")
            from xtquant import xttrader
            from xtquant.xttype import StockAccount
            self._log("✓ xtquant 库导入成功")
            
            self._log(f"【步骤2/6】生成会话ID...")
            session_id = int(random.randint(100000, 999999))
            self._log(f"✓ 会话ID: {session_id}")
            
            self._log(f"【步骤3/6】创建 XtQuantTrader 实例...")
            self._log(f"  - 数据路径: {self.qmt_path}")
            self._log(f"  - 会话ID: {session_id}")
            self.xt_trader = xttrader.XtQuantTrader(self.qmt_path, session_id)
            self._log("✓ XtQuantTrader 实例创建成功")
            
            self._log(f"【步骤4/6】启动交易接口...")
            self.xt_trader.start()
            self._log("✓ 交易接口启动成功")
            
            self._log(f"【步骤5/6】连接 QMT 交易端...")
            self._log("  - 正在等待连接响应...")
            connect_result = self.xt_trader.connect()
            logger.info(f"连接返回值: {connect_result} (类型: {type(connect_result)})")
            
            if connect_result != 0:
                error_msg = f"连接QMT交易端失败，返回值: {connect_result}"
                self._log(f"✗ {error_msg}")
                self._log("\n可能的原因:")
                self._log("  1. QMT交易端未启动")
                self._log("  2. QMT数据路径不正确")
                self._log("  3. QMT版本与xtquant库不兼容")
                self._log("  4. 网络连接问题")
                self._log("  5. QMT未登录或登录超时")
                self.connected.emit(False, error_msg)
                return
            
            self._log("✓ QMT 交易端连接成功")
            
            self._log(f"【步骤6/6】创建并订阅账户...")
            self._log(f"  - 资金账号: {self.account}")
            self.acc = StockAccount(self.account)
            self._log("✓ StockAccount 实例创建成功")
            
            self._log("  - 正在订阅账户...")
            res = self.xt_trader.subscribe(self.acc)
            logger.info(f"订阅返回值: {res} (类型: {type(res)})")
            
            if res != 0:
                error_msg = f"订阅账户失败，返回值: {res}"
                self._log(f"✗ {error_msg}")
                self._log("\n可能的原因:")
                self._log("  1. 资金账号不正确")
                self._log("  2. 账户未在QMT中登录")
                self._log("  3. 账户权限不足")
                self.connected.emit(False, error_msg)
                return
            
            self._log("✓ 账户订阅成功")
            self._log("\n" + "="*60)
            self._log("券商账户连接成功!")
            self._log("="*60 + "\n")
            self.connected.emit(True, "连接成功")
            
        except ImportError as e:
            error_msg = "请先安装 xtquant 库: pip install xtquant"
            logger.error(f"ImportError: {e}")
            logger.error(traceback.format_exc())
            self._log(f"✗ {error_msg}")
            self._log(f"  详细错误: {e}")
            self.connected.emit(False, error_msg)
        except Exception as e:
            error_msg = f"连接失败: {str(e)}"
            logger.error(f"Exception: {e}")
            logger.error(traceback.format_exc())
            self._log(f"✗ {error_msg}")
            self._log("\n错误堆栈:")
            for line in traceback.format_exc().split('\n'):
                if line.strip():
                    self._log(f"  {line}")
            self.connected.emit(False, error_msg)

class QueryThread(QThread):
    """Thread for querying broker data"""
    finished = pyqtSignal(str, object, str)  # query_type, data, error_message
    log_message = pyqtSignal(str)
    
    def __init__(self, query_type: str, xt_trader, acc):
        super().__init__()
        self.query_type = query_type
        self.xt_trader = xt_trader
        self.acc = acc
    
    def _log(self, message: str):
        """Helper method to send log message"""
        logger.info(message)
        self.log_message.emit(message)
    
    def run(self):
        try:
            self._log(f"[{self.query_type}] 开始查询...")
            start_time = datetime.now()
            
            if self.query_type == "positions":
                data = self.xt_trader.query_stock_positions(self.acc)
            elif self.query_type == "orders":
                data = self.xt_trader.query_stock_orders(self.acc)
            elif self.query_type == "trades":
                self._log(f"[{self.query_type}] 发送查询请求...")
                data = self.xt_trader.query_stock_trades(self.acc)
            else:
                self.finished.emit(self.query_type, [], "未知的查询类型")
                return
            
            elapsed = (datetime.now() - start_time).total_seconds()
            self._log(f"[{self.query_type}] 查询完成，返回 {len(data)} 条记录，耗时 {elapsed:.2f} 秒")
            
            self.finished.emit(self.query_type, data, "")
            
        except Exception as e:
            error_msg = f"{str(e)}"
            logger.error(f"[{self.query_type}] 查询失败: {e}")
            logger.error(traceback.format_exc())
            self._log(f"[{self.query_type}] 查询失败: {error_msg}")
            self.finished.emit(self.query_type, [], error_msg)


class BrokerAccountWidget(QWidget):
    """Broker Account Query Widget"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.config_path = Path(__file__).parent.parent / "config" / "broker_config.json"
        self.xt_trader = None
        self.acc = None
        self.is_connected = False
        
        # Query threads for different data types
        self.positions_query_thread = None
        self.orders_query_thread = None
        self.trades_query_thread = None
        
        logger.info("="*60)
        logger.info("初始化 BrokerAccountWidget")
        logger.info("="*60)
        
        self.load_config()
        self.setup_ui()
    
    def load_config(self):
        """Load broker configuration"""
        self.qmt_path = ""
        self.account = ""
        
        logger.info(f"尝试加载配置文件: {self.config_path}")
        
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.qmt_path = config.get("qmt_path", "")
                    self.account = config.get("account", "")
                    logger.info(f"✓ 配置加载成功: qmt_path={self.qmt_path}, account={self.account}")
            except Exception as e:
                logger.error(f"✗ 加载配置失败: {e}")
                print(f"Failed to load broker config: {e}")
        else:
            logger.info("配置文件不存在")
    
    def save_config(self):
        """Save broker configuration"""
        config = {
            "qmt_path": self.qmt_path,
            "account": self.account
        }
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            logger.info(f"✓ 配置已保存: {self.config_path}")
        except Exception as e:
            logger.error(f"✗ 保存配置失败: {e}")
            print(f"Failed to save broker config: {e}")
    
    def setup_ui(self):
        """Setup UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # Connection settings group
        settings_group = QGroupBox("券商连接设置")
        settings_layout = QFormLayout(settings_group)
        
        # QMT path
        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit(self.qmt_path)
        self.path_edit.setPlaceholderText("D:\\中金财富QMT个人版交易端\\userdata_mini")
        path_layout.addWidget(self.path_edit)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self.browse_qmt_path)
        path_layout.addWidget(browse_btn)
        settings_layout.addRow("QMT数据路径:", path_layout)
        
        # Account
        self.account_edit = QLineEdit(self.account)
        self.account_edit.setPlaceholderText("输入资金账号")
        settings_layout.addRow("资金账号:", self.account_edit)
        
        # Connection buttons
        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self.connect_broker)
        self.connect_btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; padding: 8px 16px;")
        btn_layout.addWidget(self.connect_btn)
        
        self.disconnect_btn = QPushButton("断开")
        self.disconnect_btn.clicked.connect(self.disconnect_broker)
        self.disconnect_btn.setEnabled(False)
        btn_layout.addWidget(self.disconnect_btn)
        
        self.save_config_btn = QPushButton("保存配置")
        self.save_config_btn.clicked.connect(self.on_save_config)
        btn_layout.addWidget(self.save_config_btn)
        
        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.clicked.connect(self.clear_log)
        btn_layout.addWidget(self.clear_log_btn)
        
        btn_layout.addStretch()
        settings_layout.addRow("", btn_layout)
        
        # Connection status
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("color: #888; font-weight: bold;")
        settings_layout.addRow("连接状态:", self.status_label)
        
        main_layout.addWidget(settings_group)
        
        # Log display area
        log_group = QGroupBox("连接日志")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(5, 5, 5, 5)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 10pt;
                border: 1px solid #555;
                border-radius: 3px;
            }
        """)
        log_layout.addWidget(self.log_text)
        
        main_layout.addWidget(log_group)
        
        # Splitter for account info and data tables
        splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(splitter)
        
        # Account info group
        account_group = QGroupBox("账户资产")
        account_layout = QFormLayout(account_group)
        
        self.lbl_account_id = QLabel("-")
        self.lbl_total_assets = QLabel("-")
        self.lbl_available_cash = QLabel("-")
        self.lbl_market_value = QLabel("-")
        self.lbl_profit_loss = QLabel("-")
        
        account_layout.addRow("资金账号:", self.lbl_account_id)
        account_layout.addRow("总资产:", self.lbl_total_assets)
        account_layout.addRow("可用资金:", self.lbl_available_cash)
        account_layout.addRow("持仓市值:", self.lbl_market_value)
        account_layout.addRow("浮动盈亏:", self.lbl_profit_loss)
        
        # Refresh button
        refresh_account_btn = QPushButton("刷新账户信息")
        refresh_account_btn.clicked.connect(self.refresh_account_info)
        account_layout.addRow("", refresh_account_btn)
        
        splitter.addWidget(account_group)
        
        # Tabs for positions and orders
        self.data_tabs = QTabWidget()
        
        # Common table style
        table_style = """
            QTableWidget {
                background-color: #1e1e1e;
                color: #d4d4d4;
                gridline-color: #333;
                border: none;
                selection-background-color: #264f78;
                selection-color: #ffffff;
                alternate-background-color: #252526;
            }
            QHeaderView::section {
                background-color: #2d2d2d;
                color: #d4d4d4;
                padding: 4px;
                border: 1px solid #333;
            }
            QTableCornerButton::section {
                background-color: #2d2d2d;
                border: 1px solid #333;
            }
        """
        
        # Positions tab
        self.positions_table = QTableWidget()
        self.positions_table.setStyleSheet(table_style)
        self.positions_table.setColumnCount(10)
        self.positions_table.setHorizontalHeaderLabels([
            "资金账号", "证券代码", "证券名称", "持仓数量", "可用数量",
            "开仓价", "市值", "冻结数量", "在途股份", "昨日持仓"
        ])
        self.positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.positions_table.setAlternatingRowColors(True)
        self.data_tabs.addTab(self.positions_table, "📊 持仓信息")
        
        # Orders tab
        self.orders_table = QTableWidget()
        self.orders_table.setStyleSheet(table_style)
        self.orders_table.setColumnCount(10)
        self.orders_table.setHorizontalHeaderLabels([
            "委托编号", "证券代码", "证券名称", "委托方向", "委托价格",
            "委托数量", "成交数量", "委托状态", "委托时间", "备注"
        ])
        self.orders_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.orders_table.setAlternatingRowColors(True)
        self.data_tabs.addTab(self.orders_table, "📝 当日委托")
        
        # Trades tab
        self.trades_table = QTableWidget()
        self.trades_table.setStyleSheet(table_style)
        self.trades_table.setColumnCount(9)
        self.trades_table.setHorizontalHeaderLabels([
            "成交编号", "委托编号", "证券代码", "证券名称", "成交方向",
            "成交价格", "成交数量", "成交金额", "成交时间"
        ])
        self.trades_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.trades_table.setAlternatingRowColors(True)
        self.data_tabs.addTab(self.trades_table, "💰 当日成交")
        
        # Refresh buttons
        refresh_widget = QWidget()
        refresh_layout = QHBoxLayout(refresh_widget)
        refresh_layout.setContentsMargins(5, 5, 5, 5)
        
        refresh_positions_btn = QPushButton("刷新持仓")
        refresh_positions_btn.clicked.connect(self.refresh_positions)
        refresh_layout.addWidget(refresh_positions_btn)
        
        refresh_orders_btn = QPushButton("刷新委托")
        refresh_orders_btn.clicked.connect(self.refresh_orders)
        refresh_layout.addWidget(refresh_orders_btn)
        
        refresh_trades_btn = QPushButton("刷新成交")
        refresh_trades_btn.clicked.connect(self.refresh_trades)
        refresh_layout.addWidget(refresh_trades_btn)
        
        refresh_all_btn = QPushButton("刷新全部")
        refresh_all_btn.clicked.connect(self.refresh_all)
        refresh_all_btn.setStyleSheet("background-color: #0078d4; color: white;")
        refresh_layout.addWidget(refresh_all_btn)
        
        export_btn = QPushButton("导出数据")
        export_btn.clicked.connect(self.export_data)
        refresh_layout.addWidget(export_btn)
        
        refresh_layout.addStretch()
        
        data_widget = QWidget()
        data_layout = QVBoxLayout(data_widget)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.addWidget(self.data_tabs)
        data_layout.addWidget(refresh_widget)
        
        splitter.addWidget(data_widget)
        
        # Set splitter sizes
        splitter.setSizes([150, 400])
        
        # Initial log message
        self.append_log("券商账户查询界面已加载")
        self.append_log("请配置QMT路径和资金账号后点击连接")
        if self.qmt_path:
            self.append_log(f"已加载配置 - QMT路径: {self.qmt_path}")
        if self.account:
            self.append_log(f"已加载配置 - 资金账号: {self.account}")
    
    def browse_qmt_path(self):
        """Browse for QMT data path"""
        path = QFileDialog.getExistingDirectory(self, "选择QMT数据目录")
        if path:
            self.path_edit.setText(path)
            logger.info(f"用户选择QMT路径: {path}")
    
    def append_log(self, message: str, color: str = None):
        """Append message to log display"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {message}"
        self.log_text.append(formatted_msg)
        
        # Auto scroll to bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        logger.info(message)
    
    def clear_log(self):
        """Clear log display"""
        self.log_text.clear()
        self.append_log("日志已清空")
    
    def on_save_config(self):
        """Save configuration"""
        self.qmt_path = self.path_edit.text().strip()
        self.account = self.account_edit.text().strip()
        self.save_config()
        self.append_log("配置已保存")
        QMessageBox.information(self, "成功", "配置已保存")
    
    def connect_broker(self):
        """Connect to broker"""
        self.qmt_path = self.path_edit.text().strip()
        self.account = self.account_edit.text().strip()
        
        self.append_log("="*60)
        self.append_log("用户点击连接按钮")
        self.append_log("="*60)
        
        if not self.qmt_path:
            self.append_log("✗ 错误: 请输入QMT数据路径")
            QMessageBox.warning(self, "错误", "请输入QMT数据路径")
            return
        
        if not self.account:
            self.append_log("✗ 错误: 请输入资金账号")
            QMessageBox.warning(self, "错误", "请输入资金账号")
            return
        
        # Check if QMT path exists
        if not os.path.exists(self.qmt_path):
            self.append_log(f"✗ 警告: QMT路径不存在 - {self.qmt_path}")
            self.append_log("  请确认:")
            self.append_log("  1. 路径输入是否正确")
            self.append_log("  2. QMT交易端是否已安装")
        
        self.append_log(f"准备连接...")
        self.append_log(f"  QMT路径: {self.qmt_path}")
        self.append_log(f"  资金账号: {self.account}")
        
        self.connect_btn.setEnabled(False)
        self.status_label.setText("连接中...")
        self.status_label.setStyleSheet("color: #f0ad4e;")
        
        # Start connection thread
        self.connect_thread = BrokerConnectThread(self.qmt_path, self.account)
        self.connect_thread.connected.connect(self.on_connected)
        self.connect_thread.log_message.connect(self.append_log)
        self.connect_thread.start()
    
    def on_connected(self, success: bool, message: str):
        """Handle connection result"""
        if success:
            self.xt_trader = self.connect_thread.xt_trader
            self.acc = self.connect_thread.acc
            self.is_connected = True
            
            self.append_log("\n✓ 连接成功!")
            self.append_log(f"  XtQuantTrader实例: {id(self.xt_trader)}")
            self.append_log(f"  StockAccount实例: {id(self.acc)}")
            
            self.status_label.setText("已连接")
            self.status_label.setStyleSheet("color: #5cb85c; font-weight: bold;")
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            
            # Auto refresh
            self.append_log("\n开始刷新账户数据...")
            self.refresh_all()
        else:
            self.append_log(f"\n✗ 连接失败: {message}")
            self.status_label.setText(f"连接失败: {message}")
            self.status_label.setStyleSheet("color: #d9534f; font-weight: bold;")
            self.connect_btn.setEnabled(True)
            QMessageBox.warning(self, "连接失败", message)
    
    def disconnect_broker(self):
        """Disconnect from broker"""
        self.append_log("\n" + "="*60)
        self.append_log("用户点击断开按钮")
        self.append_log("="*60)
        
        # Stop query threads
        for thread_name, thread in [
            ("持仓查询线程", self.positions_query_thread),
            ("委托查询线程", self.orders_query_thread),
            ("成交查询线程", self.trades_query_thread)
        ]:
            if thread and thread.isRunning():
                self.append_log(f"正在停止{thread_name}...")
                thread.quit()
                thread.wait()
                self.append_log(f"✓ {thread_name}已停止")
        
        try:
            if self.xt_trader:
                self.append_log("正在停止交易接口...")
                self.xt_trader.stop()
                self.append_log("✓ 交易接口已停止")
        except Exception as e:
            self.append_log(f"✗ 停止交易接口时出错: {e}")
            logger.error(f"停止交易接口时出错: {e}")
        
        self.xt_trader = None
        self.acc = None
        self.is_connected = False
        
        self.positions_query_thread = None
        self.orders_query_thread = None
        self.trades_query_thread = None
        
        self.status_label.setText("未连接")
        self.status_label.setStyleSheet("color: #888;")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        
        self.append_log("✓ 已断开连接")
        
        # Clear tables
        self.clear_all_tables()
    
    def clear_all_tables(self):
        """Clear all data tables"""
        self.positions_table.setRowCount(0)
        self.orders_table.setRowCount(0)
        self.trades_table.setRowCount(0)
        
        self.lbl_account_id.setText("-")
        self.lbl_total_assets.setText("-")
        self.lbl_available_cash.setText("-")
        self.lbl_market_value.setText("-")
        self.lbl_profit_loss.setText("-")
    
    def refresh_account_info(self):
        """Refresh account info"""
        self.append_log("正在查询账户资产...")
        if not self.is_connected or not self.xt_trader or not self.acc:
            self.append_log("✗ 错误: 请先连接券商")
            QMessageBox.warning(self, "错误", "请先连接券商")
            return
        
        try:
            # Query account assets
            assets = self.xt_trader.query_stock_asset(self.acc)
            self.append_log(f"✓ 查询到账户资产数据")
            
            if assets:
                self.lbl_account_id.setText(str(assets.account_id))
                self.lbl_total_assets.setText(f"¥{assets.total_asset:,.2f}")
                self.lbl_available_cash.setText(f"¥{assets.cash:,.2f}")
                self.lbl_market_value.setText(f"¥{assets.market_value:,.2f}")
                
                self.append_log(f"  账户ID: {assets.account_id}")
                self.append_log(f"  总资产: ¥{assets.total_asset:,.2f}")
                self.append_log(f"  可用资金: ¥{assets.cash:,.2f}")
                self.append_log(f"  持仓市值: ¥{assets.market_value:,.2f}")
                
                # Calculate profit/loss if available
                if hasattr(assets, 'frozen_cash'):
                    profit = assets.total_asset - assets.cash - assets.market_value
                    color = "#ec0000" if profit >= 0 else "#00da3c"
                    self.lbl_profit_loss.setText(f"¥{profit:,.2f}")
                    self.lbl_profit_loss.setStyleSheet(f"color: {color}; font-weight: bold;")
                    self.append_log(f"  浮动盈亏: ¥{profit:,.2f}")
                else:
                    self.lbl_profit_loss.setText("-")
        except Exception as e:
            self.append_log(f"✗ 查询账户信息失败: {e}")
            QMessageBox.warning(self, "错误", f"查询账户信息失败: {e}")
            logger.error(f"查询账户信息失败: {e}")
    
    def refresh_positions(self):
        """Refresh positions"""
        self.append_log("正在查询持仓信息...")
        if not self.is_connected or not self.xt_trader or not self.acc:
            self.append_log("✗ 错误: 请先连接券商")
            QMessageBox.warning(self, "错误", "请先连接券商")
            return
        
        self.positions_table.setEnabled(False)
        self.positions_query_thread = QueryThread("positions", self.xt_trader, self.acc)
        self.positions_query_thread.finished.connect(self.on_positions_query_finished)
        self.positions_query_thread.log_message.connect(self.append_log)
        self.positions_query_thread.start()
    
    def on_positions_query_finished(self, query_type: str, data, error_msg: str):
        """Handle positions query result"""
        if error_msg:
            self.append_log(f"✗ 查询持仓失败: {error_msg}")
            QMessageBox.warning(self, "错误", f"查询持仓失败: {error_msg}")
            self.positions_table.setEnabled(True)
            return
        
        try:
            self.positions_table.setRowCount(0)
            
            for i, pos in enumerate(data, 1):
                row = self.positions_table.rowCount()
                self.positions_table.insertRow(row)
                
                self.positions_table.setItem(row, 0, QTableWidgetItem(str(pos.account_id)))
                self.positions_table.setItem(row, 1, QTableWidgetItem(str(pos.stock_code)))
                
                # Get stock name (if available)
                stock_name = getattr(pos, 'stock_name', '-')
                self.positions_table.setItem(row, 2, QTableWidgetItem(str(stock_name)))
                
                self.positions_table.setItem(row, 3, QTableWidgetItem(str(pos.volume)))
                self.positions_table.setItem(row, 4, QTableWidgetItem(str(pos.can_use_volume)))
                self.positions_table.setItem(row, 5, QTableWidgetItem(f"{pos.open_price:.3f}"))
                self.positions_table.setItem(row, 6, QTableWidgetItem(f"{pos.market_value:,.2f}"))
                self.positions_table.setItem(row, 7, QTableWidgetItem(str(pos.frozen_volume)))
                self.positions_table.setItem(row, 8, QTableWidgetItem(str(pos.on_road_volume)))
                self.positions_table.setItem(row, 9, QTableWidgetItem(str(pos.yesterday_volume)))
                
                if i <= 3:
                    self.append_log(f"  [{i}] {pos.stock_code} {stock_name}: {pos.volume}股")
            
            self.statusBar_message(f"持仓信息已刷新，共 {len(data)} 条记录")
            self.positions_table.setEnabled(True)
        except Exception as e:
            self.append_log(f"✗ 处理持仓数据失败: {e}")
            logger.error(f"处理持仓数据失败: {e}")
            self.positions_table.setEnabled(True)
    
    def refresh_orders(self):
        """Refresh orders"""
        self.append_log("正在查询委托信息...")
        if not self.is_connected or not self.xt_trader or not self.acc:
            self.append_log("✗ 错误: 请先连接券商")
            QMessageBox.warning(self, "错误", "请先连接券商")
            return
        
        self.orders_table.setEnabled(False)
        self.orders_query_thread = QueryThread("orders", self.xt_trader, self.acc)
        self.orders_query_thread.finished.connect(self.on_orders_query_finished)
        self.orders_query_thread.log_message.connect(self.append_log)
        self.orders_query_thread.start()
    
    def on_orders_query_finished(self, query_type: str, data, error_msg: str):
        """Handle orders query result"""
        if error_msg:
            self.append_log(f"✗ 查询委托失败: {error_msg}")
            QMessageBox.warning(self, "错误", f"查询委托失败: {error_msg}")
            self.orders_table.setEnabled(True)
            return
        
        try:
            self.orders_table.setRowCount(0)
            
            for i, order in enumerate(data, 1):
                row = self.orders_table.rowCount()
                self.orders_table.insertRow(row)
                
                self.orders_table.setItem(row, 0, QTableWidgetItem(str(order.order_id)))
                self.orders_table.setItem(row, 1, QTableWidgetItem(str(order.stock_code)))
                
                # Get stock name if available
                stock_name = getattr(order, 'stock_name', '-')
                self.orders_table.setItem(row, 2, QTableWidgetItem(str(stock_name)))
                
                # Order direction
                direction = "买入" if order.order_type == 23 else "卖出" if order.order_type == 24 else str(order.order_type)
                direction_item = QTableWidgetItem(direction)
                if "买" in direction:
                    direction_item.setForeground(QBrush(QColor("#ec0000")))
                else:
                    direction_item.setForeground(QBrush(QColor("#00da3c")))
                self.orders_table.setItem(row, 3, direction_item)
                
                self.orders_table.setItem(row, 4, QTableWidgetItem(f"{order.price:.3f}"))
                self.orders_table.setItem(row, 5, QTableWidgetItem(str(order.order_volume)))
                self.orders_table.setItem(row, 6, QTableWidgetItem(str(order.traded_volume)))
                
                # Order status
                status_map = {
                    48: "未报",
                    49: "待报",
                    50: "已报",
                    51: "已报待撤",
                    52: "部成待撤",
                    53: "部撤",
                    54: "已撤",
                    55: "部成",
                    56: "已成",
                    57: "废单"
                }
                status = status_map.get(order.order_status, str(order.order_status))
                self.orders_table.setItem(row, 7, QTableWidgetItem(status))
                
                # Order time
                order_time = getattr(order, 'order_time', '-')
                self.orders_table.setItem(row, 8, QTableWidgetItem(str(order_time)))
                
                # Remarks
                remarks = getattr(order, 'status_msg', '-')
                self.orders_table.setItem(row, 9, QTableWidgetItem(str(remarks)))
                
                if i <= 3:
                    self.append_log(f"  [{i}] {order.stock_code} {direction} {order.order_volume}股 @ {order.price:.3f} - {status}")
            
            self.statusBar_message(f"委托信息已刷新，共 {len(data)} 条记录")
            self.orders_table.setEnabled(True)
        except Exception as e:
            self.append_log(f"✗ 处理委托数据失败: {e}")
            logger.error(f"处理委托数据失败: {e}")
            self.orders_table.setEnabled(True)
    
    def refresh_trades(self):
        """Refresh trades"""
        self.append_log("正在查询成交信息...")
        if not self.is_connected or not self.xt_trader or not self.acc:
            self.append_log("✗ 错误: 请先连接券商")
            QMessageBox.warning(self, "错误", "请先连接券商")
            return
        
        self.trades_table.setEnabled(False)
        self.trades_query_thread = QueryThread("trades", self.xt_trader, self.acc)
        self.trades_query_thread.finished.connect(self.on_trades_query_finished)
        self.trades_query_thread.log_message.connect(self.append_log)
        self.trades_query_thread.start()
    
    def on_trades_query_finished(self, query_type: str, data, error_msg: str):
        """Handle trades query result"""
        if error_msg:
            self.append_log(f"✗ 查询成交失败: {error_msg}")
            QMessageBox.warning(self, "错误", f"查询成交失败: {error_msg}")
            self.trades_table.setEnabled(True)
            return
        
        try:
            self.append_log("  - 开始处理数据...")
            process_start = datetime.now()
            
            self.trades_table.setRowCount(0)
            
            if len(data) > 0:
                for i, trade in enumerate(data, 1):
                    row = self.trades_table.rowCount()
                    self.trades_table.insertRow(row)
                    
                    self.trades_table.setItem(row, 0, QTableWidgetItem(str(trade.traded_id)))
                    self.trades_table.setItem(row, 1, QTableWidgetItem(str(trade.order_id)))
                    self.trades_table.setItem(row, 2, QTableWidgetItem(str(trade.stock_code)))
                    
                    # Get stock name if available
                    stock_name = getattr(trade, 'stock_name', '-')
                    self.trades_table.setItem(row, 3, QTableWidgetItem(str(stock_name)))
                    
                    # Trade direction
                    direction = "买入" if trade.order_type == 23 else "卖出" if trade.order_type == 24 else str(trade.order_type)
                    direction_item = QTableWidgetItem(direction)
                    if "买" in direction:
                        direction_item.setForeground(QBrush(QColor("#ec0000")))
                    else:
                        direction_item.setForeground(QBrush(QColor("#00da3c")))
                    self.trades_table.setItem(row, 4, direction_item)
                    
                    self.trades_table.setItem(row, 5, QTableWidgetItem(f"{trade.traded_price:.3f}"))
                    self.trades_table.setItem(row, 6, QTableWidgetItem(str(trade.traded_volume)))
                    self.trades_table.setItem(row, 7, QTableWidgetItem(f"{trade.traded_amount:,.2f}"))
                    
                    # Trade time
                    trade_time = getattr(trade, 'traded_time', '-')
                    self.trades_table.setItem(row, 8, QTableWidgetItem(str(trade_time)))
                    
                    # 显示进度
                    if i % 100 == 0 or i == len(data):
                        self.append_log(f"  - 处理进度: {i}/{len(data)}")
                        # 让UI有机会刷新
                        self.trades_table.viewport().update()
                
                process_elapsed = (datetime.now() - process_start).total_seconds()
                self.append_log(f"  - 数据处理完成，耗时 {process_elapsed:.2f} 秒")
                
                # 显示前3条
                for i in range(min(3, len(data))):
                    trade = data[i]
                    direction = "买入" if trade.order_type == 23 else "卖出" if trade.order_type == 24 else str(trade.order_type)
                    self.append_log(f"  [{i+1}] {trade.stock_code} {direction} {trade.traded_volume}股 @ {trade.traded_price:.3f}")
            else:
                self.append_log("  - 无成交记录")
            
            self.trades_table.setEnabled(True)
            self.statusBar_message(f"成交信息已刷新，共 {len(data)} 条记录")
            self.append_log(f"✓ 查询成交成功，共 {len(data)} 条记录")
            
        except Exception as e:
            self.append_log(f"✗ 处理成交数据失败: {e}")
            logger.error(f"处理成交数据失败: {e}")
            logger.error(traceback.format_exc())
            self.trades_table.setEnabled(True)
    
    def refresh_all(self):
        """Refresh all data"""
        self.append_log("\n开始刷新所有数据...")
        self.refresh_account_info()
        self.append_log("-"*60)
        self.refresh_positions()
        self.append_log("-"*60)
        self.refresh_orders()
        self.append_log("-"*60)
        self.refresh_trades()
        self.append_log("\n✓ 数据刷新完成")
        self.append_log("="*60)
    
    def statusBar_message(self, msg: str):
        """Show status bar message"""
        main_window = self.window()
        if hasattr(main_window, 'statusBar'):
            main_window.statusBar().showMessage(msg, 5000)
    
    def export_data(self):
        """Export data to CSV"""
        if not self.is_connected:
            QMessageBox.warning(self, "错误", "请先连接券商")
            return
        
        try:
            import pandas as pd
            
            folder = QFileDialog.getExistingDirectory(self, "选择导出目录")
            if not folder:
                return
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Export positions
            if self.positions_table.rowCount() > 0:
                positions_data = self._table_to_list(self.positions_table)
                df = pd.DataFrame(positions_data)
                df.to_csv(os.path.join(folder, f"positions_{timestamp}.csv"), index=False, encoding='utf-8-sig')
            
            # Export orders
            if self.orders_table.rowCount() > 0:
                orders_data = self._table_to_list(self.orders_table)
                df = pd.DataFrame(orders_data)
                df.to_csv(os.path.join(folder, f"orders_{timestamp}.csv"), index=False, encoding='utf-8-sig')
            
            # Export trades
            if self.trades_table.rowCount() > 0:
                trades_data = self._table_to_list(self.trades_table)
                df = pd.DataFrame(trades_data)
                df.to_csv(os.path.join(folder, f"trades_{timestamp}.csv"), index=False, encoding='utf-8-sig')
            
            QMessageBox.information(self, "成功", f"数据已导出到 {folder}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"导出失败: {e}")
    
    def _table_to_list(self, table: QTableWidget) -> list:
        """Convert table to list of dicts"""
        headers = []
        for col in range(table.columnCount()):
            header_item = table.horizontalHeaderItem(col)
            headers.append(header_item.text() if header_item else f"Column{col}")
        
        data = []
        for row in range(table.rowCount()):
            row_data = {}
            for col in range(table.columnCount()):
                item = table.item(row, col)
                row_data[headers[col]] = item.text() if item else ""
            data.append(row_data)
        
        return data
    
    def closeEvent(self, event):
        """Handle close event"""
        logger.info("关闭券商账户窗口")
        self.append_log("窗口关闭")
        self.disconnect_broker()
        super().closeEvent(event)
