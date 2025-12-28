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

# 检查 xtquant 是否可用
try:
    import xtquant
    HAS_XTQUANT = True
except ImportError:
    HAS_XTQUANT = False

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QGroupBox, QFormLayout, QLineEdit, QFrame,
    QSplitter, QFileDialog, QSizePolicy, QTextEdit, QScrollArea,
    QSpinBox, QDoubleSpinBox, QComboBox
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
            error_msg = f"导入 xtquant 库失败: {str(e)}"
            logger.error(f"ImportError: {e}")
            logger.error(traceback.format_exc())
            self._log(f"✗ {error_msg}")
            self._log("\n解决方法:")
            self._log("  1. 确认已安装 xtquant 库")
            self._log("  2. 运行命令: pip install xtquant")
            self._log("  3. 如果已安装，请检查 Python 环境是否正确")
            self._log(f"  4. 详细错误信息: {e}")
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


class TradeThread(QThread):
    """Thread for placing orders"""
    finished = pyqtSignal(bool, str, int)  # success, message, order_id
    log_message = pyqtSignal(str)
    
    def __init__(self, xt_trader, acc, stock_code: str, order_type: int, 
                 order_volume: int, price_type: int, price: float):
        super().__init__()
        self.xt_trader = xt_trader
        self.acc = acc
        self.stock_code = stock_code
        self.order_type = order_type  # 23=买入, 24=卖出
        self.order_volume = order_volume
        self.price_type = price_type  # 0=限价, 1=市价
        self.price = price
    
    def _log(self, message: str):
        """Helper method to send log message"""
        logger.info(message)
        self.log_message.emit(message)
    
    def run(self):
        try:
            # 尝试导入 xtconstant
            try:
                from xtquant import xtconstant
                USE_XTCONSTANT = True
            except ImportError:
                USE_XTCONSTANT = False
            
            # 使用常量或硬编码值
            if USE_XTCONSTANT:
                STOCK_BUY = xtconstant.STOCK_BUY
                STOCK_SELL = xtconstant.STOCK_SELL
                FIX_PRICE = xtconstant.FIX_PRICE
                # 尝试多种市价常量名称
                if hasattr(xtconstant, 'LATEST_PRICE'):
                    MARKET_PRICE = xtconstant.LATEST_PRICE
                elif hasattr(xtconstant, 'MARKET_PRICE'):
                    MARKET_PRICE = xtconstant.MARKET_PRICE
                else:
                    MARKET_PRICE = 1
            else:
                STOCK_BUY = 23
                STOCK_SELL = 24
                FIX_PRICE = 0
                MARKET_PRICE = 1
            
            direction = "买入" if self.order_type == STOCK_BUY else "卖出"
            
            # 检查并格式化股票代码（xtquant可能需要市场后缀）
            stock_code = self.stock_code.strip()
            if not stock_code:
                error_msg = "股票代码不能为空"
                self._log(f"✗ {error_msg}")
                self.finished.emit(False, error_msg, -1)
                return
            
            # 如果股票代码没有市场后缀，尝试添加（6开头是上海，0/3开头是深圳）
            if '.' not in stock_code:
                if stock_code.startswith(('6', '9')):
                    stock_code = f"{stock_code}.SH"
                elif stock_code.startswith(('0', '1', '2', '3')):
                    stock_code = f"{stock_code}.SZ"
            
            # 市价单时价格设为-1，限价单使用实际价格
            is_limit_order = (self.price_type == FIX_PRICE)
            if not is_limit_order and self.price_type != MARKET_PRICE:
                is_limit_order = (self.price_type == 0)
            
            order_price = self.price if is_limit_order else -1
            price_desc = f"限价 {self.price:.3f}" if is_limit_order else "市价"
            actual_price_type = FIX_PRICE if is_limit_order else MARKET_PRICE
            
            self._log(f"准备{direction} {stock_code} {self.order_volume}股 ({price_desc})...")
            
            # 检查账户资金（如果是买入限价单）
            if self.order_type == STOCK_BUY and is_limit_order:
                try:
                    assets = self.xt_trader.query_stock_asset(self.acc)
                    if assets:
                        required_cash = order_price * self.order_volume
                        if required_cash > assets.cash:
                            error_msg = f"账户资金不足，可用: ¥{assets.cash:,.2f}，需要: ¥{required_cash:,.2f}"
                            self._log(f"✗ {error_msg}")
                            self.finished.emit(False, error_msg, -1)
                            return
                except Exception:
                    pass  # 资金检查失败不影响下单
            
            # 调用xtquant下单接口
            order_id = self.xt_trader.order_stock(
                self.acc,
                stock_code,
                self.order_type,
                self.order_volume,
                actual_price_type,
                order_price,
                '',
                ''
            )
            
            if order_id == -1:
                error_msg = f"{direction}委托失败"
                self._log(f"✗ {error_msg}")
                
                # 尝试不带市场后缀的股票代码
                if '.' in stock_code:
                    base_code = stock_code.split('.')[0]
                    try:
                        order_id2 = self.xt_trader.order_stock(
                            self.acc, base_code, self.order_type, 
                            self.order_volume, actual_price_type, order_price, '', ''
                        )
                        if order_id2 != -1:
                            success_msg = f"{direction}委托成功，订单ID: {order_id2}"
                            self._log(f"✓ {success_msg}")
                            self.finished.emit(True, success_msg, order_id2)
                            return
                    except Exception:
                        pass
                
                self.finished.emit(False, error_msg, -1)
            elif order_id is None:
                error_msg = f"{direction}委托失败，返回None"
                self._log(f"✗ {error_msg}")
                self.finished.emit(False, error_msg, -1)
            else:
                success_msg = f"{direction}委托成功，订单ID: {order_id}"
                self._log(f"✓ {success_msg}")
                self.finished.emit(True, success_msg, order_id)
                
        except Exception as e:
            error_msg = f"下单失败: {str(e)}"
            logger.error(f"下单失败: {e}")
            logger.error(traceback.format_exc())
            self._log(f"✗ {error_msg}")
            self.finished.emit(False, error_msg, -1)


class CancelOrderThread(QThread):
    """Thread for canceling orders"""
    finished = pyqtSignal(bool, str)  # success, message
    log_message = pyqtSignal(str)
    
    def __init__(self, xt_trader, acc, order_id: int):
        super().__init__()
        self.xt_trader = xt_trader
        self.acc = acc
        self.order_id = order_id
    
    def _log(self, message: str):
        """Helper method to send log message"""
        logger.info(message)
        self.log_message.emit(message)
    
    def run(self):
        try:
            self._log(f"准备撤单，订单ID: {self.order_id}...")
            
            result = self.xt_trader.cancel_order_stock(self.acc, self.order_id)
            
            if result != 0:
                error_msg = f"撤单失败"
                self._log(f"✗ {error_msg}")
                self.finished.emit(False, error_msg)
            else:
                success_msg = f"撤单成功，订单ID: {self.order_id}"
                self._log(f"✓ {success_msg}")
                self.finished.emit(True, success_msg)
                
        except Exception as e:
            error_msg = f"撤单失败: {str(e)}"
            logger.error(f"撤单失败: {e}")
            logger.error(traceback.format_exc())
            self._log(f"✗ {error_msg}")
            self.finished.emit(False, error_msg)


class BrokerAccountWidget(QWidget):
    """交易窗口 - 券商账户查询和交易"""
    
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
        
        # Trade threads
        self.trade_thread = None
        self.cancel_order_thread = None
        
        logger.info("="*60)
        logger.info("初始化交易窗口")
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
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # Main horizontal splitter for left and right columns
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(main_splitter)
        
        # ========== LEFT COLUMN ==========
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        
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
        
        left_layout.addWidget(settings_group)
        
        # Log display area
        log_group = QGroupBox("连接日志")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(5, 5, 5, 5)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(200)
        self.log_text.setMaximumHeight(300)
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
        
        left_layout.addWidget(log_group)
        left_layout.addStretch()
        
        main_splitter.addWidget(left_widget)
        
        # ========== RIGHT COLUMN ==========
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        
        # Trading panel
        trading_group = QGroupBox("交易下单")
        trading_layout = QFormLayout(trading_group)
        
        # Stock code
        self.trade_stock_code_edit = QLineEdit()
        self.trade_stock_code_edit.setPlaceholderText("输入股票代码，如：000001")
        trading_layout.addRow("股票代码:", self.trade_stock_code_edit)
        
        # Price type
        price_type_layout = QHBoxLayout()
        self.price_type_combo = QComboBox()
        self.price_type_combo.addItems(["限价", "市价"])
        self.price_type_combo.currentIndexChanged.connect(self.on_price_type_changed)
        price_type_layout.addWidget(self.price_type_combo)
        price_type_layout.addStretch()
        trading_layout.addRow("价格类型:", price_type_layout)
        
        # Price (for limit order)
        price_layout = QHBoxLayout()
        self.trade_price_spin = QDoubleSpinBox()
        self.trade_price_spin.setDecimals(3)
        self.trade_price_spin.setMinimum(0.001)
        self.trade_price_spin.setMaximum(9999.999)
        self.trade_price_spin.setSingleStep(0.01)
        self.trade_price_spin.setValue(0.0)
        price_layout.addWidget(self.trade_price_spin)
        price_layout.addStretch()
        trading_layout.addRow("委托价格:", price_layout)
        
        # Volume
        volume_layout = QHBoxLayout()
        self.trade_volume_spin = QSpinBox()
        self.trade_volume_spin.setMinimum(100)
        self.trade_volume_spin.setMaximum(1000000)
        self.trade_volume_spin.setSingleStep(100)
        self.trade_volume_spin.setValue(100)
        volume_layout.addWidget(self.trade_volume_spin)
        volume_layout.addStretch()
        trading_layout.addRow("委托数量(股):", volume_layout)
        
        # Trade buttons
        trade_btn_layout = QHBoxLayout()
        self.buy_btn = QPushButton("买入")
        self.buy_btn.clicked.connect(self.on_buy_order)
        self.buy_btn.setStyleSheet("background-color: #ec0000; color: white; font-weight: bold; padding: 8px 16px;")
        self.buy_btn.setEnabled(False)
        trade_btn_layout.addWidget(self.buy_btn)
        
        self.sell_btn = QPushButton("卖出")
        self.sell_btn.clicked.connect(self.on_sell_order)
        self.sell_btn.setStyleSheet("background-color: #00da3c; color: white; font-weight: bold; padding: 8px 16px;")
        self.sell_btn.setEnabled(False)
        trade_btn_layout.addWidget(self.sell_btn)
        
        trade_btn_layout.addStretch()
        trading_layout.addRow("", trade_btn_layout)
        
        right_layout.addWidget(trading_group)
        
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
        self.orders_table.setColumnCount(11)
        self.orders_table.setHorizontalHeaderLabels([
            "委托编号", "证券代码", "证券名称", "委托方向", "委托价格",
            "委托数量", "成交数量", "委托状态", "委托时间", "备注", "操作"
        ])
        self.orders_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # 设置操作列的最小宽度，确保按钮文字完整显示
        self.orders_table.horizontalHeader().setMinimumSectionSize(70)
        # 操作列使用固定宽度
        self.orders_table.setColumnWidth(10, 70)
        self.orders_table.setAlternatingRowColors(True)
        self.orders_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.orders_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
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
        
        # Account info tab
        self.account_table = QTableWidget()
        self.account_table.setStyleSheet(table_style)
        self.account_table.setColumnCount(2)
        self.account_table.setHorizontalHeaderLabels(["项目", "数值"])
        self.account_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.account_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.account_table.setAlternatingRowColors(True)
        self.account_table.setRowCount(5)
        self.account_table.verticalHeader().setVisible(False)
        
        # Initialize table items
        self.account_table.setItem(0, 0, QTableWidgetItem("资金账号"))
        self.account_table.setItem(1, 0, QTableWidgetItem("总资产"))
        self.account_table.setItem(2, 0, QTableWidgetItem("可用资金"))
        self.account_table.setItem(3, 0, QTableWidgetItem("持仓市值"))
        self.account_table.setItem(4, 0, QTableWidgetItem("浮动盈亏"))
        
        # Create label items for values (we'll update these)
        self.lbl_account_id_item = QTableWidgetItem("-")
        self.lbl_total_assets_item = QTableWidgetItem("-")
        self.lbl_available_cash_item = QTableWidgetItem("-")
        self.lbl_market_value_item = QTableWidgetItem("-")
        self.lbl_profit_loss_item = QTableWidgetItem("-")
        
        self.account_table.setItem(0, 1, self.lbl_account_id_item)
        self.account_table.setItem(1, 1, self.lbl_total_assets_item)
        self.account_table.setItem(2, 1, self.lbl_available_cash_item)
        self.account_table.setItem(3, 1, self.lbl_market_value_item)
        self.account_table.setItem(4, 1, self.lbl_profit_loss_item)
        
        # Store references for easy access
        self.lbl_account_id = self.lbl_account_id_item
        self.lbl_total_assets = self.lbl_total_assets_item
        self.lbl_available_cash = self.lbl_available_cash_item
        self.lbl_market_value = self.lbl_market_value_item
        self.lbl_profit_loss = self.lbl_profit_loss_item
        
        self.data_tabs.addTab(self.account_table, "💰 账户资产")
        
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
        
        right_layout.addWidget(data_widget)
        
        main_splitter.addWidget(right_widget)
        
        # Set splitter sizes (left: 400, right: 600)
        main_splitter.setSizes([400, 600])
        
        # Initial log message
        self.append_log("交易界面已加载")
        if not HAS_XTQUANT:
            self.append_log("⚠ 警告: xtquant 库未安装或不可用")
            self.append_log("  请运行命令安装: pip install xtquant")
            self.append_log("  安装后请重启应用程序")
        else:
            self.append_log("✓ xtquant 库已就绪")
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
            
            # Enable trading buttons
            self.buy_btn.setEnabled(True)
            self.sell_btn.setEnabled(True)
            
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
            ("成交查询线程", self.trades_query_thread),
            ("交易线程", self.trade_thread),
            ("撤单线程", self.cancel_order_thread)
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
        self.trade_thread = None
        self.cancel_order_thread = None
        
        # Disable trading buttons
        self.buy_btn.setEnabled(False)
        self.sell_btn.setEnabled(False)
        
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
        self.lbl_profit_loss.setForeground(QBrush(QColor("#d4d4d4")))
    
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
                    color = QColor("#ec0000") if profit >= 0 else QColor("#00da3c")
                    self.lbl_profit_loss.setText(f"¥{profit:,.2f}")
                    self.lbl_profit_loss.setForeground(QBrush(color))
                    self.append_log(f"  浮动盈亏: ¥{profit:,.2f}")
                else:
                    self.lbl_profit_loss.setText("-")
                    self.lbl_profit_loss.setForeground(QBrush(QColor("#d4d4d4")))
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
                
                # Cancel button (only for cancellable orders)
                # Cancellable statuses: 50(已报), 51(已报待撤), 52(部成待撤), 55(部成)
                cancellable_statuses = [50, 51, 52, 55]
                cancel_btn = QPushButton("撤销")
                cancel_btn.setMinimumWidth(60)
                cancel_btn.setMinimumHeight(28)
                cancel_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #f0ad4e;
                        color: white;
                        border: none;
                        padding: 4px 12px;
                        border-radius: 3px;
                        font-size: 10pt;
                        min-width: 60px;
                    }
                    QPushButton:hover {
                        background-color: #ec971f;
                    }
                    QPushButton:pressed {
                        background-color: #d58512;
                    }
                """)
                
                if order.order_status in cancellable_statuses:
                    cancel_btn.clicked.connect(lambda checked, order_id=order.order_id: self.cancel_order_by_id(order_id))
                    cancel_btn.setEnabled(True)
                else:
                    cancel_btn.setEnabled(False)
                    cancel_btn.setStyleSheet("""
                        QPushButton {
                            background-color: #555;
                            color: #888;
                            border: none;
                            padding: 4px 12px;
                            border-radius: 3px;
                            font-size: 10pt;
                            min-width: 60px;
                        }
                    """)
                
                self.orders_table.setCellWidget(row, 10, cancel_btn)
                
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
                # Skip widget columns (like buttons)
                widget = table.cellWidget(row, col)
                if widget is not None:
                    continue
                item = table.item(row, col)
                row_data[headers[col]] = item.text() if item else ""
            data.append(row_data)
        
        return data
    
    def on_price_type_changed(self, index: int):
        """Handle price type change"""
        # 0=限价, 1=市价
        if index == 1:  # 市价
            self.trade_price_spin.setEnabled(False)
            self.trade_price_spin.setValue(0.0)
        else:  # 限价
            self.trade_price_spin.setEnabled(True)
    
    def on_buy_order(self):
        """Place buy order"""
        if not self.is_connected or not self.xt_trader or not self.acc:
            QMessageBox.warning(self, "错误", "请先连接券商")
            return
        
        stock_code = self.trade_stock_code_edit.text().strip()
        if not stock_code:
            QMessageBox.warning(self, "错误", "请输入股票代码")
            return
        
        order_volume = self.trade_volume_spin.value()
        if order_volume <= 0:
            QMessageBox.warning(self, "错误", "委托数量必须大于0")
            return
        
        price_type = self.price_type_combo.currentIndex()  # 0=限价, 1=市价
        price = self.trade_price_spin.value()
        
        if price_type == 0 and price <= 0:  # 限价单必须输入价格
            QMessageBox.warning(self, "错误", "限价单必须输入委托价格")
            return
        
        # 禁用交易按钮
        self.buy_btn.setEnabled(False)
        self.sell_btn.setEnabled(False)
        
        # 创建交易线程
        self.trade_thread = TradeThread(
            self.xt_trader,
            self.acc,
            stock_code,
            23,  # 23=买入
            order_volume,
            price_type,
            price
        )
        self.trade_thread.finished.connect(self.on_trade_finished)
        self.trade_thread.log_message.connect(self.append_log)
        self.trade_thread.start()
    
    def on_sell_order(self):
        """Place sell order"""
        if not self.is_connected or not self.xt_trader or not self.acc:
            QMessageBox.warning(self, "错误", "请先连接券商")
            return
        
        stock_code = self.trade_stock_code_edit.text().strip()
        if not stock_code:
            QMessageBox.warning(self, "错误", "请输入股票代码")
            return
        
        order_volume = self.trade_volume_spin.value()
        if order_volume <= 0:
            QMessageBox.warning(self, "错误", "委托数量必须大于0")
            return
        
        price_type = self.price_type_combo.currentIndex()  # 0=限价, 1=市价
        price = self.trade_price_spin.value()
        
        if price_type == 0 and price <= 0:  # 限价单必须输入价格
            QMessageBox.warning(self, "错误", "限价单必须输入委托价格")
            return
        
        # 禁用交易按钮
        self.buy_btn.setEnabled(False)
        self.sell_btn.setEnabled(False)
        
        # 创建交易线程
        self.trade_thread = TradeThread(
            self.xt_trader,
            self.acc,
            stock_code,
            24,  # 24=卖出
            order_volume,
            price_type,
            price
        )
        self.trade_thread.finished.connect(self.on_trade_finished)
        self.trade_thread.log_message.connect(self.append_log)
        self.trade_thread.start()
    
    def on_trade_finished(self, success: bool, message: str, order_id: int):
        """Handle trade finished"""
        # 重新启用交易按钮
        if self.is_connected:
            self.buy_btn.setEnabled(True)
            self.sell_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "成功", message)
            # 刷新委托列表
            self.refresh_orders()
        else:
            QMessageBox.warning(self, "失败", message)
    
    def cancel_order_by_id(self, order_id: int):
        """Cancel order by order ID (called from table button)"""
        if not self.is_connected or not self.xt_trader or not self.acc:
            QMessageBox.warning(self, "错误", "请先连接券商")
            return
        
        # 确认撤单
        reply = QMessageBox.question(
            self,
            "确认撤单",
            f"确定要撤销订单 {order_id} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 创建撤单线程
        self.cancel_order_thread = CancelOrderThread(
            self.xt_trader,
            self.acc,
            order_id
        )
        self.cancel_order_thread.finished.connect(self.on_cancel_order_finished)
        self.cancel_order_thread.log_message.connect(self.append_log)
        self.cancel_order_thread.start()
    
    def on_cancel_order_finished(self, success: bool, message: str):
        """Handle cancel order finished"""
        if success:
            # 立即刷新委托列表
            self.append_log("正在刷新委托列表...")
            self.refresh_orders()
            # 延迟显示消息框，确保刷新操作已启动
            QTimer.singleShot(300, lambda: QMessageBox.information(self, "成功", message))
        else:
            QMessageBox.warning(self, "失败", message)
    
    def closeEvent(self, event):
        """Handle close event"""
        logger.info("关闭交易窗口")
        self.append_log("窗口关闭")
        self.disconnect_broker()
        super().closeEvent(event)
