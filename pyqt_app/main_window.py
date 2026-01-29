# main_window.py - 主窗口
"""
来财主窗口
"""
import os
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLabel, QStatusBar, QMenuBar, QMenu,
    QToolBar, QGroupBox, QCheckBox, QComboBox,
    QDateEdit, QPushButton, QMessageBox, QApplication,
    QInputDialog, QDialog, QDialogButtonBox, QTabWidget,
    QProgressDialog, QProgressBar
)
from PyQt6.QtCore import Qt, QDate, QSize, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QIcon

# 本地模块
from widgets.kline_widget import KLineWidget, should_update_realtime_kline
from widgets.stock_list_widget import StockListWidget
from widgets.timeshare_widget import TimeShareWidget
from widgets.trading_simulator_widget import TradingSimulatorWidget
from widgets.stock_screener_widget import StockScreenerWidget
from widgets.ai_trading_widget import AITradingWidget
from widgets.ai_agent_widget import AIAgentWidget
from widgets.update_dialog import UpdateDialog
from widgets.notification_dialog import NotificationDialog
from widgets.scheduled_task_dialog import ScheduledTaskDialog
from widgets.watchlist_panel_widget import WatchlistPanelWidget
from widgets.etf_list_widget import ETFListWidget
from widgets.etf_grid_widget import ETFGridWidget
from widgets.watchlist_widget import WatchlistWidget
from widgets.broker_account_widget import BrokerAccountWidget
from widgets.chip_distribution_widget import ChipDistributionDialog
from widgets.sector_window import SectorWindow
from widgets.backtest_widget import BacktestWidget
from widgets.cross_sectional_backtest_widget import CrossSectionalBacktestWidget
from widgets.factor_library_widget import FactorLibraryWidget
from watchlist_manager import WatchlistManager
from data_loader import (load_stock_data, get_stock_list, load_stock_name_map, get_stock_cache,
                         load_etf_data, get_etf_list, load_etf_name_map, load_etf_categories, get_etf_cache)
from indicators import attach_all_indicators
from data_updater import DataUpdateThread, ETFUpdateThread, ETFListUpdateThread
from scheduler import ScheduledTaskManager
from services.quote_service import get_quote_service, QuoteData
from services.conditional_order_service import get_conditional_order_service


class DataPreloadThread(QThread):
    """
    后台预加载股票数据的线程
    在应用启动时将所有股票K线数据加载到内存缓存
    """
    progress_updated = pyqtSignal(int, int, str)  # current, total, code
    finished_signal = pyqtSignal(bool, int, str)  # success, loaded_count, message
    
    def __init__(self, data_dir: str, stock_codes: list):
        super().__init__()
        self.data_dir = data_dir
        self.stock_codes = stock_codes
    
    def run(self):
        try:
            cache = get_stock_cache()
            
            def progress_callback(current, total, code):
                self.progress_updated.emit(current, total, code)
            
            loaded_count = cache.preload_all(
                self.data_dir,
                self.stock_codes,
                progress_callback=progress_callback,
                max_workers=8
            )
            
            self.finished_signal.emit(
                True, 
                loaded_count, 
                f"成功预加载 {loaded_count}/{len(self.stock_codes)} 只股票数据"
            )
        except Exception as e:
            self.finished_signal.emit(False, 0, f"预加载失败: {e}")


class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        
        # 设置窗口图标
        icon_path = Path(__file__).resolve().parent.parent / "icon.jpeg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        
        # 数据目录配置
        self.data_dir = self.get_data_dir()
        self.stocklist_path = self.get_stocklist_path()
        
        # 数据
        self.stock_list = []
        self.name_map = {}
        self.current_code = ""
        self.current_name = ""
        
        # 自选股管理
        self.watchlist_manager = WatchlistManager()
        
        # ETF 数据
        self.etf_list = []
        self.etf_name_map = {}
        self.etf_categories = []
        self.current_etf_code = ""
        self.current_etf_name = ""
        
        # 指数数据
        self.current_index_code = ""
        self.current_index_name = ""
        
        self.current_view = "stock"  # "stock", "etf", or "index"
        
        # 设置
        self.ma_windows = [5, 10, 20]
        
        self.setupUI()
        self.setup_menu()
        self.setup_toolbar()
        self.setup_shortcuts()
        
        # 加载数据
        self.load_stock_list()
        
        self.update_thread = None
        self.update_dialog = None
        self.preload_thread = None
        self._updating_codes = []  # 记录正在更新的股票代码
        
        # 启动数据预加载
        self.start_data_preload()

        # 初始化定时任务管理器
        self.scheduler_manager = ScheduledTaskManager(self.data_dir, self.stocklist_path)
        self.scheduler_manager.task_finished.connect(self.on_scheduled_task_finished)
        
        # 热门板块窗口（独立窗口）
        self.sector_window: Optional[SectorWindow] = None
    
    def get_data_dir(self) -> str:
        """获取数据目录路径"""
        # 支持多种路径
        possible_paths = [
            Path(__file__).parent.parent / "data",  # 相对于pyqt_app目录
            Path("./data"),
            Path("../data"),
        ]
        
        for p in possible_paths:
            if p.exists():
                return str(p)
        
        return str(possible_paths[0])
    
    def get_stocklist_path(self) -> str:
        """获取股票列表文件路径"""
        possible_paths = [
            Path(__file__).parent.parent / "stocklist.csv",
            Path("./stocklist.csv"),
            Path("../stocklist.csv"),
        ]
        
        for p in possible_paths:
            if p.exists():
                return str(p)
        
        return str(possible_paths[0])
    
    def setupUI(self):
        """设置界面"""
        self.setWindowTitle("来财")
        self.setMinimumSize(1200, 800)
        
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 创建分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # 左侧面板（股票列表 + 设置）
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(5)
        
        # 股票/ETF 切换 Tab
        self.left_tabs = QTabWidget()
        self.left_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                background-color: #2d2d2d;
            }
            QTabBar::tab {
                background-color: #2d2d2d;
                color: #ffffff;
                padding: 8px 16px;
                border: 1px solid #3c3c3c;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #0078d4;
            }
            QTabBar::tab:hover:!selected {
                background-color: #3c3c3c;
            }
        """)
        self.left_tabs.currentChanged.connect(self.on_left_tab_changed)
        
        # Tab 1: 股票列表
        self.stock_list_widget = StockListWidget()
        self.stock_list_widget.stockSelected.connect(self.on_stock_selected)
        self.stock_list_widget.refreshRequested.connect(self.on_refresh_strategy)
        self.stock_list_widget.set_watchlist_manager(self.watchlist_manager)
        # 添加右键菜单
        self.stock_list_widget.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.stock_list_widget.list_widget.customContextMenuRequested.connect(self.show_stock_list_context_menu)
        self.left_tabs.addTab(self.stock_list_widget, "📈 股票")
        
        # Tab 2: ETF列表
        self.etf_list_widget = ETFListWidget()
        self.etf_list_widget.etfSelected.connect(self.on_etf_selected)
        self.etf_list_widget.set_watchlist_manager(self.watchlist_manager)
        # 添加右键菜单
        self.etf_list_widget.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.etf_list_widget.list_widget.customContextMenuRequested.connect(self.show_etf_list_context_menu)
        self.left_tabs.addTab(self.etf_list_widget, "📊 ETF")
        
        # Tab 3: 自选列表（股票+ETF混合）
        self.watchlist_widget = WatchlistWidget()
        self.watchlist_widget.itemSelected.connect(self.on_watchlist_item_selected)
        self.watchlist_widget.set_watchlist_manager(self.watchlist_manager)
        # 添加右键菜单
        self.watchlist_widget.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.watchlist_widget.list_widget.customContextMenuRequested.connect(self.show_watchlist_context_menu)
        self.left_tabs.addTab(self.watchlist_widget, "⭐ 自选")
        
        # Tab 4: 指数列表
        from widgets.index_list_widget import IndexListWidget
        self.index_list_widget = IndexListWidget()
        self.index_list_widget.indexSelected.connect(self.on_index_selected)
        self.left_tabs.addTab(self.index_list_widget, "📊 指数")
        
        left_layout.addWidget(self.left_tabs, stretch=1)
        
        # 指标设置
        indicator_group = QGroupBox("指标设置")
        indicator_layout = QVBoxLayout(indicator_group)
        
        self.volume_checkbox = QCheckBox("成交量")
        self.volume_checkbox.setChecked(True)
        self.volume_checkbox.stateChanged.connect(self.on_indicator_changed)
        indicator_layout.addWidget(self.volume_checkbox)
        
        self.macd_checkbox = QCheckBox("MACD")
        self.macd_checkbox.setChecked(True)
        self.macd_checkbox.stateChanged.connect(self.on_indicator_changed)
        indicator_layout.addWidget(self.macd_checkbox)
        
        self.kdj_checkbox = QCheckBox("KDJ")
        self.kdj_checkbox.setChecked(False)
        self.kdj_checkbox.stateChanged.connect(self.on_indicator_changed)
        indicator_layout.addWidget(self.kdj_checkbox)
        
        left_layout.addWidget(indicator_group)
        
        # 日期范围
        date_group = QGroupBox("日期范围")
        date_layout = QVBoxLayout(date_group)
        
        start_layout = QHBoxLayout()
        start_layout.addWidget(QLabel("起始:"))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate(2016, 1, 1))
        start_layout.addWidget(self.start_date_edit)
        date_layout.addLayout(start_layout)
        
        end_layout = QHBoxLayout()
        end_layout.addWidget(QLabel("结束:"))
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        end_layout.addWidget(self.end_date_edit)
        date_layout.addLayout(end_layout)
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_chart)
        date_layout.addWidget(refresh_btn)
        
        left_layout.addWidget(date_group)
        
        splitter.addWidget(left_panel)
        
        # 右侧面板（K线图 + 面板）
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        self.right_tabs = QTabWidget()
        right_layout.addWidget(self.right_tabs)
        
        # Tab 1: K线图
        self.kline_widget = KLineWidget()
        self.right_tabs.addTab(self.kline_widget, "📈 K线图")
        
        # Tab 2: 分时图
        self.timeshare_widget = TimeShareWidget()
        self.timeshare_widget.refreshStatusChanged.connect(self.on_timeshare_refresh_status_changed)
        self.right_tabs.addTab(self.timeshare_widget, "📊 分时图")
        
        # Tab 3: 面板
        self.watchlist_panel = WatchlistPanelWidget(
            self.watchlist_manager, 
            self.name_map, 
            self.data_dir
        )
        self.watchlist_panel.stockSelected.connect(self.on_panel_stock_selected)
        self.right_tabs.addTab(self.watchlist_panel, "📋 面板")

        # 连接列表变化信号到面板
        self.stock_list_widget.displayListChanged.connect(self.on_display_list_changed)
        self.stock_list_widget.groupChanged.connect(self.on_group_changed_for_panel)
        
        # 连接自选Tab的信号到面板
        self.watchlist_widget.groupChanged.connect(self.on_watchlist_group_changed_for_panel)
        self.watchlist_widget.displayListChanged.connect(self.on_watchlist_display_list_changed)
        
        # 连接Tab切换信号
        self.right_tabs.currentChanged.connect(self.on_right_tab_changed)

        splitter.addWidget(right_panel)
        
        # 智能体面板
        self.agent_widget = AIAgentWidget()
        self.agent_widget.setVisible(False)
        self.agent_widget.screenshotRequested.connect(self.capture_kline_screenshot)
        self.agent_widget.klineDataRequested.connect(self.attach_kline_data_to_agent)
        self.agent_widget.stockAnalysisRequested.connect(self.start_stock_analysis)
        splitter.addWidget(self.agent_widget)
        
        # 保存 splitter 引用
        self.splitter = splitter
        
        # 设置分割比例
        splitter.setSizes([150, 1050, 0])
        
        # 状态栏
        self.statusBar().showMessage("就绪")
        
        # 预加载进度条 (放在状态栏右侧)
        self.preload_status_label = QLabel("")
        self.preload_status_label.setStyleSheet("color: #888; font-size: 11px;")
        self.preload_progress_bar = QProgressBar()
        self.preload_progress_bar.setMaximumHeight(12)
        self.preload_progress_bar.setMaximumWidth(150)
        self.preload_progress_bar.setTextVisible(False)
        self.preload_progress_bar.setVisible(False)
        
        self.statusBar().addPermanentWidget(self.preload_status_label)
        self.statusBar().addPermanentWidget(self.preload_progress_bar)
        
        # 模拟器窗口列表，防止被垃圾回收
        self.simulator_windows = []
        self.screener_windows = []
        self.ai_windows = []
        self.etf_grid_windows = []
        self.backtest_windows = []
        self.cross_sectional_windows = []
    
    def setup_menu(self):
        """设置菜单栏"""
        menubar = self.menuBar()
        
        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        
        refresh_action = QAction("刷新图表(&R)", self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.triggered.connect(self.refresh_chart)
        file_menu.addAction(refresh_action)
        
        # 更新数据子菜单
        update_menu = file_menu.addMenu("更新数据(&U)")
        
        refresh_today_action = QAction("刷新今日当前时刻K线", self)
        refresh_today_action.setShortcut("Ctrl+F5")
        refresh_today_action.triggered.connect(self.refresh_all_today_kline)
        update_menu.addAction(refresh_today_action)
        
        show_update_dialog_action = QAction("批量更新历史数据...", self)
        show_update_dialog_action.triggered.connect(self.show_update_dialog)
        update_menu.addAction(show_update_dialog_action)
        
        update_menu.addSeparator()
        
        update_etf_action = QAction("更新ETF数据...", self)
        update_etf_action.triggered.connect(self.show_etf_update_dialog)
        update_menu.addAction(update_etf_action)
        
        update_etf_list_action = QAction("更新ETF列表（从xtquant获取完整列表）...", self)
        update_etf_list_action.triggered.connect(self.show_etf_list_update_dialog)
        update_menu.addAction(update_etf_list_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("退出(&X)", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 视图菜单
        view_menu = menubar.addMenu("视图(&V)")
        
        self.volume_action = QAction("成交量", self, checkable=True, checked=True)
        self.volume_action.triggered.connect(lambda checked: self.volume_checkbox.setChecked(checked))
        view_menu.addAction(self.volume_action)
        
        self.macd_action = QAction("MACD", self, checkable=True, checked=True)
        self.macd_action.triggered.connect(lambda checked: self.macd_checkbox.setChecked(checked))
        view_menu.addAction(self.macd_action)
        
        self.kdj_action = QAction("KDJ", self, checkable=True, checked=False)
        self.kdj_action.triggered.connect(lambda checked: self.kdj_checkbox.setChecked(checked))
        view_menu.addAction(self.kdj_action)
        
        # 工具菜单
        tools_menu = menubar.addMenu("工具(&T)")
        
        simulator_action = QAction("模拟训练(&S)", self)
        simulator_action.triggered.connect(self.open_simulator)
        tools_menu.addAction(simulator_action)
        
        screener_action = QAction("智能选股(&C)", self)
        screener_action.triggered.connect(self.open_screener)
        tools_menu.addAction(screener_action)
        
        ai_action = QAction("AI 智能交易训练(&I)", self)
        ai_action.triggered.connect(self.open_ai_tool)
        tools_menu.addAction(ai_action)
        
        backtest_action = QAction("策略回测(&B)", self)
        backtest_action.triggered.connect(self.open_backtest_window)
        tools_menu.addAction(backtest_action)
        
        cross_backtest_action = QAction("截面策略回测(&M)", self)
        cross_backtest_action.triggered.connect(self.open_cross_sectional_backtest_window)
        tools_menu.addAction(cross_backtest_action)
        
        factor_library_action = QAction("因子库(&F)", self)
        factor_library_action.triggered.connect(self.open_factor_library_window)
        tools_menu.addAction(factor_library_action)
        
        tools_menu.addSeparator()
        
        etf_grid_action = QAction("ETF网格交易(&G)", self)
        etf_grid_action.triggered.connect(self.open_etf_grid_strategy)
        tools_menu.addAction(etf_grid_action)
        
        sector_action = QAction("🔥 热门板块(&B)", self)
        sector_action.setShortcut("Ctrl+B")
        sector_action.triggered.connect(self.open_sector_window)
        tools_menu.addAction(sector_action)

        tools_menu.addSeparator()

        broker_action = QAction("交易(&T)", self)
        broker_action.triggered.connect(self.open_broker_account)
        tools_menu.addAction(broker_action)

        tools_menu.addSeparator()

        notification_action = QAction("消息推送(&N)", self)
        notification_action.triggered.connect(self.open_notification_dialog)
        tools_menu.addAction(notification_action)
        
        scheduled_task_action = QAction("定时任务(&L)", self)
        scheduled_task_action.triggered.connect(self.open_scheduled_task_dialog)
        tools_menu.addAction(scheduled_task_action)
        
        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")
        
        about_action = QAction("关于(&A)", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
    
    def setup_toolbar(self):
        """设置工具栏"""
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # 均线选择
        toolbar.addWidget(QLabel(" 均线: "))
        
        self.ma_combo = QComboBox()
        self.ma_combo.addItems([
            "MA5/10/20",
            "MA5/10/20/60",
            "MA10/20/60",
            "MA5/10/30/60",
        ])
        self.ma_combo.currentIndexChanged.connect(self.on_ma_changed)
        toolbar.addWidget(self.ma_combo)
        
        toolbar.addSeparator()
        
        # 上一只/下一只按钮
        prev_btn = QPushButton("◀ 上一只")
        prev_btn.clicked.connect(self._select_previous_item)
        toolbar.addWidget(prev_btn)
        
        next_btn = QPushButton("下一只 ▶")
        next_btn.clicked.connect(self._select_next_item)
        toolbar.addWidget(next_btn)
        
        toolbar.addSeparator()
        
        # 交易按钮
        trade_btn = QPushButton("💰 交易")
        trade_btn.clicked.connect(self.open_broker_account)
        trade_btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; padding: 6px 12px;")
        toolbar.addWidget(trade_btn)
        
        toolbar.addSeparator()
        
        # 智能体按钮
        agent_btn = QPushButton("🤖 智能体")
        agent_btn.clicked.connect(self.open_ai_agent)
        toolbar.addWidget(agent_btn)
        
        toolbar.addSeparator()
        
        # 热门板块按钮
        sector_btn = QPushButton("🔥 板块")
        sector_btn.setToolTip("热门板块 (Ctrl+B)")
        sector_btn.clicked.connect(self.open_sector_window)
        sector_btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 6px 12px;")
        toolbar.addWidget(sector_btn)
    
    def setup_shortcuts(self):
        """设置快捷键"""
        # 上下键切换股票/ETF/自选
        QShortcut(Qt.Key.Key_Up, self, self._select_previous_item)
        QShortcut(Qt.Key.Key_Down, self, self._select_next_item)
        
        # F5 刷新
        QShortcut(Qt.Key.Key_F5, self, self.refresh_chart)
    
    def _select_previous_item(self):
        """根据当前Tab选中上一个项目"""
        current_tab = self.left_tabs.currentIndex()
        if current_tab == 0:  # 股票Tab
            self.stock_list_widget.select_previous()
        elif current_tab == 1:  # ETF Tab
            self.etf_list_widget.select_previous()
        elif current_tab == 2:  # 自选Tab
            self.watchlist_widget.select_previous()
        elif current_tab == 3:  # 指数Tab
            self.index_list_widget.select_previous()
    
    def _select_next_item(self):
        """根据当前Tab选中下一个项目"""
        current_tab = self.left_tabs.currentIndex()
        if current_tab == 0:  # 股票Tab
            self.stock_list_widget.select_next()
        elif current_tab == 1:  # ETF Tab
            self.etf_list_widget.select_next()
        elif current_tab == 2:  # 自选Tab
            self.watchlist_widget.select_next()
        elif current_tab == 3:  # 指数Tab
            self.index_list_widget.select_next()
    
    def load_stock_list(self):
        """加载股票列表"""
        self.statusBar().showMessage("正在加载股票列表...")
        QApplication.processEvents()
        
        # 加载股票代码列表
        self.stock_list = get_stock_list(self.data_dir)
        
        # 更新名称映射
        self.name_map = load_stock_name_map(self.stocklist_path)
        
        # 更新组件
        self.stock_list_widget.set_stock_list(self.stock_list, self.name_map)
        self.watchlist_panel.update_name_map(self.name_map)
        
        # 更新自选股分组下拉框
        self.stock_list_widget.update_group_combo()
        
        self.statusBar().showMessage(f"已加载 {len(self.stock_list)} 只股票")
        
        # 加载ETF列表
        self.load_etf_list()
        
        # 默认选中第一只
        if self.stock_list:
            first_code = self.stock_list[0]
            self.stock_list_widget.select_stock(first_code)
            self.on_stock_selected(first_code, self.name_map.get(first_code, ""))
        
        # 启动实时行情服务
        self._start_quote_service()
    
    def load_etf_list(self):
        """加载ETF列表"""
        # 加载ETF代码列表
        self.etf_list = get_etf_list(self.data_dir)
        
        # 加载ETF名称映射和分类
        self.etf_name_map = load_etf_name_map()
        self.etf_categories = load_etf_categories()
        
        # 更新ETF列表组件
        self.etf_list_widget.set_etf_data(
            self.etf_list, 
            self.etf_name_map, 
            self.etf_categories
        )
        
        # 更新自选分组下拉框
        self.etf_list_widget.update_group_combo()
        
        # 更新自选列表组件的名称映射
        self.watchlist_widget.set_name_maps(self.name_map, self.etf_name_map, self.etf_list)
        self.watchlist_widget.update_group_combo()
        
        # 更新面板的ETF名称映射
        self.watchlist_panel.update_etf_name_map(self.etf_name_map, self.etf_list)
        
        if self.etf_list:
            self.statusBar().showMessage(
                f"已加载 {len(self.stock_list)} 只股票, {len(self.etf_list)} 只ETF"
            )
    
    def start_data_preload(self):
        """启动数据预加载"""
        if not self.stock_list:
            return
        
        # 设置状态栏进度条
        self.preload_progress_bar.setRange(0, len(self.stock_list))
        self.preload_progress_bar.setValue(0)
        self.preload_progress_bar.setVisible(True)
        self.preload_status_label.setText("正在预加载数据...")
        self.preload_status_label.setVisible(True)
        
        # 创建并启动预加载线程
        self.preload_thread = DataPreloadThread(self.data_dir, self.stock_list)
        self.preload_thread.progress_updated.connect(self.on_preload_progress)
        self.preload_thread.finished_signal.connect(self.on_preload_finished)
        self.preload_thread.start()
    
    def on_preload_progress(self, current: int, total: int, code: str):
        """更新预加载进度"""
        self.preload_progress_bar.setValue(current)
        self.preload_status_label.setText(f"预加载中: {code} ({current}/{total}) ")
    
    def on_preload_finished(self, success: bool, loaded_count: int, message: str):
        """预加载完成回调"""
        self.preload_progress_bar.setVisible(False)
        
        if success:
            self.preload_status_label.setText(f"✓ 数据预加载完成 ({loaded_count}只) ")
            # 5秒后隐藏完成提示
            QTimer.singleShot(5000, lambda: self.preload_status_label.setVisible(False))
        else:
            self.preload_status_label.setText(f"⚠ 预加载失败 ")
            self.statusBar().showMessage(f"⚠ {message}")
        
        # 刷新当前图表（使用缓存数据）
        if success and self.current_code:
            self.load_and_display_chart()
    
    def on_stock_selected(self, code: str, name: str):
        """处理股票选择"""
        self.current_code = code
        self.current_name = name
        self.current_view = "stock"
        
        self.load_and_display_chart()
        
        # Update timeshare widget if it's visible
        current_tab_index = self.right_tabs.currentIndex()
        if current_tab_index == 1:  # Timeshare tab
            self.load_timeshare_data()
    
    def on_etf_selected(self, code: str, name: str):
        """处理ETF选择"""
        self.current_etf_code = code
        self.current_etf_name = name
        self.current_view = "etf"
        
        # Load K-line chart first
        self.load_and_display_etf_chart()
        
        # If currently on timeshare tab, also load timeshare data
        if self.right_tabs.currentIndex() == 1:
            self.load_etf_timeshare_data()
    
    def on_index_selected(self, code: str, name: str):
        """处理指数选择"""
        self.current_index_code = code
        self.current_index_name = name
        self.current_view = "index"
        
        # Load K-line chart
        self.load_and_display_index_chart()
    
    def on_left_tab_changed(self, index: int):
        """处理左侧股票/ETF/自选/指数Tab切换"""
        # 确保watchlist_panel已初始化
        if not hasattr(self, 'watchlist_panel'):
            return
            
        if index == 0:  # 股票Tab
            self.current_view = "stock"
            # 更新面板显示股票分组
            current_group = self.stock_list_widget.current_group
            is_group = bool(current_group) and not current_group.startswith("全部")
            self.watchlist_panel.set_group_mode(is_group)
            if is_group:
                stocks = self.stock_list_widget.filtered_list
                self.watchlist_panel.set_stocks(stocks)
            # 如果有选中的股票，刷新显示
            if self.current_code:
                self.load_and_display_chart()
                # If currently on timeshare tab, also load timeshare data
                if self.right_tabs.currentIndex() == 1:
                    self.load_timeshare_data()
        elif index == 1:  # ETF Tab
            self.current_view = "etf"
            # ETF Tab时关闭面板的分组模式（因为ETF分组是类别，不是自选）
            self.watchlist_panel.set_group_mode(False)
            # 如果有选中的ETF，刷新显示
            if self.current_etf_code:
                self.load_and_display_etf_chart()
                # If currently on timeshare tab, also load timeshare data
                if self.right_tabs.currentIndex() == 1:
                    self.load_etf_timeshare_data()
            elif self.etf_list:
                # 选中第一只ETF
                first_etf = self.etf_list[0]
                self.etf_list_widget.select_etf(first_etf)
                self.on_etf_selected(first_etf, self.etf_name_map.get(first_etf, ""))
        elif index == 2:  # 自选Tab
            # 自选Tab: 更新面板显示当前分组
            current_group = self.watchlist_widget.get_current_group()
            is_group = bool(current_group)
            self.watchlist_panel.set_group_mode(is_group)
            if is_group:
                stocks = self.watchlist_widget.filtered_list
                self.watchlist_panel.set_stocks(stocks)
        elif index == 3:  # 指数Tab
            self.current_view = "index"
            self.watchlist_panel.set_group_mode(False)
            # 如果有选中的指数，刷新显示
            if hasattr(self, 'current_index_code') and self.current_index_code:
                self.load_and_display_index_chart()
            elif hasattr(self, 'index_list_widget'):
                # 选中第一个指数
                from services.index_service import get_index_list
                indices = get_index_list()
                if indices:
                    first_index = indices[0]
                    self.index_list_widget.select_index(first_index['code'])
                    self.on_index_selected(first_index['code'], first_index['name'])

    def on_watchlist_item_selected(self, code: str, name: str, is_etf: bool):
        """处理自选列表项目选中（支持股票和ETF混合）"""
        if is_etf:
            # ETF处理
            self.current_etf_code = code
            self.current_etf_name = name
            self.current_view = "etf"
            self.load_and_display_etf_chart()
            # If currently on timeshare tab, also load timeshare data
            if self.right_tabs.currentIndex() == 1:
                self.load_etf_timeshare_data()
        else:
            # 股票处理
            self.current_code = code
            self.current_name = name
            self.current_view = "stock"
            self.load_and_display_chart()
            # If currently on timeshare tab, also load timeshare data
            if self.right_tabs.currentIndex() == 1:
                self.load_timeshare_data()

    def on_sector_stock_selected(self, code: str, name: str):
        """处理热门板块中成分股被选中"""
        # 切换到股票 Tab
        self.left_tabs.setCurrentIndex(0)  # 股票 Tab
        
        # 选中股票
        self.stock_list_widget.select_stock(code)
        
        # 获取股票名称
        if not name:
            name = self.name_map.get(code, "")
        
        # 触发股票选中逻辑
        self.on_stock_selected(code, name)
        
        self.statusBar().showMessage(f"📈 已跳转至 {code} {name}")

    def on_sector_selected(self, sector_name: str):
        """处理板块被选中"""
        # 可以在这里添加板块选中后的逻辑
        # 例如：显示板块相关信息、记录等
        display_name = sector_name.replace("申万", "")
        self.statusBar().showMessage(f"🔥 已选中板块: {display_name}")

    def on_panel_stock_selected(self, code: str, name: str, is_etf: bool = False):
        """处理面板中的股票/ETF选择"""
        # 切换到 K线图 Tab
        self.right_tabs.setCurrentIndex(0)
        
        # 检查当前是否在自选Tab，如果是则保持在自选Tab
        current_left_tab = self.left_tabs.currentIndex()
        is_watchlist_tab = (current_left_tab == 2)
        
        if is_etf:
            if not is_watchlist_tab:
                # 非自选Tab: 切换到ETF Tab并选择
                self.left_tabs.setCurrentIndex(1)  # ETF Tab
                self.etf_list_widget.select_etf(code)
            self.on_etf_selected(code, name)
        else:
            if not is_watchlist_tab:
                # 非自选Tab: 切换到股票Tab并选择
                self.left_tabs.setCurrentIndex(0)  # Stock Tab
                self.stock_list_widget.select_stock(code)
            self.on_stock_selected(code, name)

    def on_right_tab_changed(self, index: int):
        """Handle right panel tab changes"""
        if index == 1:  # Timeshare tab
            # Load timeshare data based on current view (stock or ETF)
            if self.current_view == "etf" and self.current_etf_code:
                self.load_etf_timeshare_data()
            else:
                self.load_timeshare_data()
        elif index == 0:  # K-line tab
            # Stop timeshare auto-refresh when switching away
            self.timeshare_widget.stop_auto_refresh()

    def load_timeshare_data(self):
        """Load timeshare data for current stock"""
        if not self.current_code:
            return
        
        # Get today's date
        import datetime
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        today_date = datetime.date.today()
        
        # Get previous close from K-line data
        prev_close = None
        if self.kline_widget.data is not None and not self.kline_widget.data.empty:
            df = self.kline_widget.data
            
            # Get the last record's date
            last_date = df.iloc[-1]['date']
            if hasattr(last_date, 'date'):
                last_date = last_date.date()
            elif isinstance(last_date, str):
                last_date = datetime.datetime.strptime(last_date[:10], "%Y-%m-%d").date()
            
            # If the last K-line data is today, use second last as prev_close
            # Otherwise, use the last one as prev_close (it's yesterday or earlier)
            if last_date == today_date:
                if len(df) >= 2:
                    prev_close = df.iloc[-2]['close']
                else:
                    prev_close = df.iloc[-1]['open']
            else:
                prev_close = df.iloc[-1]['close']
        
        self.statusBar().showMessage(f"正在加载 {self.current_code} {self.current_name} 分时图...")
        
        self.timeshare_widget.load_data(
            code=self.current_code,
            date_str=today_str,
            data_dir=self.data_dir,
            prev_close=prev_close
        )
    
    def load_etf_timeshare_data(self):
        """Load timeshare data for current ETF"""
        if not self.current_etf_code:
            return
        
        # Get today's date
        import datetime
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        today_date = datetime.date.today()
        
        # Get previous close from K-line data
        prev_close = None
        if self.kline_widget.data is not None and not self.kline_widget.data.empty:
            df = self.kline_widget.data
            
            # Get the last record's date
            last_date = df.iloc[-1]['date']
            if hasattr(last_date, 'date'):
                last_date = last_date.date()
            elif isinstance(last_date, str):
                last_date = datetime.datetime.strptime(last_date[:10], "%Y-%m-%d").date()
            
            # If the last K-line data is today, use second last as prev_close
            # Otherwise, use the last one as prev_close
            if last_date == today_date:
                if len(df) >= 2:
                    prev_close = df.iloc[-2]['close']
                else:
                    prev_close = df.iloc[-1]['open']
            else:
                prev_close = df.iloc[-1]['close']
        
        self.statusBar().showMessage(f"正在加载 {self.current_etf_code} {self.current_etf_name} 分时图...")
        
        self.timeshare_widget.load_data(
            code=self.current_etf_code,
            date_str=today_str,
            data_dir=self.data_dir,
            prev_close=prev_close
        )

    def on_timeshare_refresh_status_changed(self, is_refreshing: bool, message: str):
        """Handle timeshare refresh status changes"""
        if is_refreshing:
            self.statusBar().showMessage(f"分时图: {message}")

    def on_group_changed_for_panel(self, group_name):
        """同步分组状态到面板（实时行情由面板 checkbox 控制）"""
        is_group = bool(group_name)  # 非空字符串表示选中了某个分组
        self.watchlist_panel.set_group_mode(is_group)
        
        # 如果是分组模式，立即更新一次数据
        if is_group:
            stocks = self.stock_list_widget.filtered_list
            self.watchlist_panel.set_stocks(stocks)
            # 注意：实时行情订阅不再自动开启，由用户点击面板上的 "📡 实时行情" checkbox 控制

    def on_display_list_changed(self, stocks):
        """当左侧列表过滤或搜索变化时，同步到面板"""
        self.watchlist_panel.set_stocks(stocks)
    
    def on_watchlist_group_changed_for_panel(self, group_name):
        """处理自选Tab的分组切换，同步到面板"""
        # Only respond when the watchlist tab is active
        if self.left_tabs.currentIndex() != 2:
            return
        
        is_group = bool(group_name)
        self.watchlist_panel.set_group_mode(is_group)
        
        if is_group:
            stocks = self.watchlist_widget.filtered_list
            self.watchlist_panel.set_stocks(stocks)
    
    def on_watchlist_display_list_changed(self, stocks):
        """处理自选Tab的列表变化，同步到面板"""
        # Only respond when the watchlist tab is active
        if self.left_tabs.currentIndex() != 2:
            return
        self.watchlist_panel.set_stocks(stocks)
    
    # ========== 实时行情服务集成 ==========
    
    def _start_quote_service(self):
        """
        初始化实时行情服务（默认不启动，不订阅）
        
        只在用户切换到自选分组时才启动并订阅该分组的股票
        """
        try:
            self.quote_service = get_quote_service()
            
            if not self.quote_service.is_available:
                # 静默处理，不显示消息（用户可能没有安装 xtquant）
                return
            
            # 连接信号（但不启动服务）
            self.quote_service.quote_updated.connect(self._on_quote_updated)
            self.quote_service.connection_status_changed.connect(self._on_quote_status_changed)
            
            # 默认不启动，不订阅任何股票
            # 只在切换到自选分组时才启动
                
        except Exception as e:
            pass  # 静默处理
    
    def _update_quote_subscription(self, group_name: str, stocks: list):
        """
        更新实时行情订阅（仅针对自选分组）
        
        Args:
            group_name: 分组名称，空字符串表示非分组模式
            stocks: 要订阅的股票列表
        """
        if not hasattr(self, 'quote_service') or self.quote_service is None:
            return
        
        if not self.quote_service.is_available:
            return
        
        try:
            if group_name and stocks:
                # 切换到自选分组，订阅该分组的股票
                # 先取消所有订阅
                self.quote_service.unsubscribe_all()
                
                # 订阅新的分组股票
                self.quote_service.subscribe(stocks)
                self.statusBar().showMessage(f"📡 已订阅 {group_name} 分组 ({len(stocks)} 只)", 3000)
            else:
                # 切换到非分组模式，停止订阅
                if self.quote_service.is_running:
                    self.quote_service.unsubscribe_all()
                    self.quote_service.stop()
                    self.statusBar().showMessage("📡 实时行情已停止", 2000)
                    
        except Exception as e:
            pass  # 静默处理
    
    def _on_quote_updated(self, quote_data: QuoteData):
        """
        处理实时行情推送
        
        将行情数据分发给：
        1. 分时图组件（更新盘口和最新价）
        2. 面板组件（更新股票卡片）
        3. 条件单服务（检查触发条件）
        """
        try:
            # 更新分时图（如果当前显示的是这只股票/ETF）
            if self.right_tabs.currentIndex() == 1:  # 分时图Tab
                self.timeshare_widget.update_realtime_quote(quote_data)
            
            # 更新面板中的股票卡片（如果面板可见）
            if self.right_tabs.currentIndex() == 2:  # 面板Tab
                simple_code = quote_data.simple_code
                card = self.watchlist_panel.cards_map.get(simple_code)
                if card:
                    card.update_realtime(quote_data)
            
            # 更新条件单服务（检查是否触发止盈止损）
            if quote_data.last_price > 0:
                conditional_service = get_conditional_order_service()
                if conditional_service.is_monitoring:
                    conditional_service.check_single_quote(
                        quote_data.simple_code, 
                        quote_data.last_price
                    )
                    
        except Exception as e:
            pass  # 静默处理，避免高频错误日志
    
    def _on_quote_status_changed(self, connected: bool, message: str):
        """处理实时行情服务状态变化"""
        if connected:
            self.statusBar().showMessage(f"📡 {message}", 3000)
        else:
            self.statusBar().showMessage(f"📡 {message}", 5000)
    
    def closeEvent(self, event):
        """窗口关闭时清理资源"""
        # 1. 停止定时任务检查
        if hasattr(self, 'scheduler_manager'):
            self.scheduler_manager.stop()
        
        # 2. 停止分时图自动刷新
        if hasattr(self, 'timeshare_widget'):
            self.timeshare_widget.stop_auto_refresh()
        
        # 3. 停止K线图实时行情订阅
        if hasattr(self, 'kline_widget'):
            self.kline_widget.stop_realtime()
        
        # 4. 停止实时行情服务
        try:
            from services.quote_service import get_quote_service
            quote_service = get_quote_service()
            if quote_service.is_running:
                quote_service.stop()
        except Exception:
            pass
        
        # 5. 关闭独立板块窗口
        try:
            if self.sector_window:
                self.sector_window.close()
                self.sector_window = None
        except Exception:
            pass
        
        # 6. 停止数据更新线程
        if self.update_thread and self.update_thread.isRunning():
            self.update_thread.stop()
            self.update_thread.wait(2000) # 最多等待2秒
            
        # 7. 停止数据预加载线程
        if self.preload_thread and self.preload_thread.isRunning():
            # 预加载线程通常没那么紧急，但也应该停止
            pass
            
        # 8. 停止所有模拟器和选股窗口
        for window in self.simulator_windows + self.screener_windows + self.ai_windows:
            try:
                window.close()
            except:
                pass
                
        event.accept()

    def load_and_display_chart(self):
        """加载并显示K线图"""
        if not self.current_code:
            return
        
        self.statusBar().showMessage(f"正在加载 {self.current_code} {self.current_name}...")
        QApplication.processEvents()
        
        # 获取日期范围
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        
        # 加载数据
        df = load_stock_data(
            self.current_code,
            self.data_dir,
            adj="qfq",
            start_date=start_date,
            end_date=end_date
        )
        
        if df is None or df.empty:
            self.statusBar().showMessage(f"未找到 {self.current_code} 的数据")
            QMessageBox.warning(self, "数据错误", f"未找到 {self.current_code} 的数据")
            return
        
        # 添加技术指标
        df = attach_all_indicators(
            df,
            ma_windows=self.ma_windows,
            include_macd=self.macd_checkbox.isChecked(),
            include_kdj=self.kdj_checkbox.isChecked(),
            include_bbi=False,
            vol_ma_window=5
        )
        
        # 切换股票时停止实时行情
        if self.kline_widget.is_realtime_enabled:
            self.kline_widget.stop_realtime()
        
        # 更新K线图
        self.kline_widget.set_indicators(
            show_volume=self.volume_checkbox.isChecked(),
            show_macd=self.macd_checkbox.isChecked(),
            show_kdj=self.kdj_checkbox.isChecked()
        )
        self.kline_widget.set_ma_windows(self.ma_windows)
        self.kline_widget.set_data(df, self.current_code, self.current_name)
        
        # 更新窗口标题
        self.setWindowTitle(f"来财 - {self.current_code} {self.current_name}")
        
        self.statusBar().showMessage(
            f"{self.current_code} {self.current_name} | "
            f"数据范围: {df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')} | "
            f"共 {len(df)} 根K线"
        )
        
        # 根据交易时间自动开启实时行情
        last_data_date = df['date'].max().date()
        if should_update_realtime_kline(last_data_date):
            if self.kline_widget.start_realtime():
                self.statusBar().showMessage(f"📡 已开启 {self.current_code} 实时行情", 3000)
    
    def load_and_display_etf_chart(self):
        """加载并显示ETF K线图"""
        if not self.current_etf_code:
            return
        
        self.statusBar().showMessage(f"正在加载 {self.current_etf_code} {self.current_etf_name}...")
        QApplication.processEvents()
        
        # 获取日期范围
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        
        # 加载ETF数据
        df = load_etf_data(
            self.current_etf_code,
            self.data_dir,
            start_date=start_date,
            end_date=end_date
        )
        
        if df is None or df.empty:
            self.statusBar().showMessage(f"未找到 {self.current_etf_code} 的数据")
            QMessageBox.warning(self, "数据错误", f"未找到 {self.current_etf_code} 的ETF数据，请先更新ETF数据")
            return
        
        # 添加技术指标
        df = attach_all_indicators(
            df,
            ma_windows=self.ma_windows,
            include_macd=self.macd_checkbox.isChecked(),
            include_kdj=self.kdj_checkbox.isChecked(),
            include_bbi=False,
            vol_ma_window=5
        )
        
        # 切换股票时停止实时行情
        if self.kline_widget.is_realtime_enabled:
            self.kline_widget.stop_realtime()
        
        # 更新K线图
        self.kline_widget.set_indicators(
            show_volume=self.volume_checkbox.isChecked(),
            show_macd=self.macd_checkbox.isChecked(),
            show_kdj=self.kdj_checkbox.isChecked()
        )
        self.kline_widget.set_ma_windows(self.ma_windows)
        self.kline_widget.set_data(df, self.current_etf_code, self.current_etf_name)
        
        # 更新窗口标题
        self.setWindowTitle(f"来财 - ETF {self.current_etf_code} {self.current_etf_name}")
        
        self.statusBar().showMessage(
            f"ETF {self.current_etf_code} {self.current_etf_name} | "
            f"数据范围: {df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')} | "
            f"共 {len(df)} 根K线"
        )
        
        # 根据交易时间自动开启实时行情
        last_data_date = df['date'].max().date()
        if should_update_realtime_kline(last_data_date):
            if self.kline_widget.start_realtime():
                self.statusBar().showMessage(f"📡 已开启 {self.current_etf_code} 实时行情", 3000)
    
    def load_and_display_index_chart(self):
        """加载并显示指数K线图"""
        if not hasattr(self, 'current_index_code') or not self.current_index_code:
            return
        
        self.statusBar().showMessage(f"正在加载 {self.current_index_code} {self.current_index_name}...")
        QApplication.processEvents()
        
        # 获取日期范围
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        
        # 加载指数数据
        from services.index_service import load_index_data
        df = load_index_data(
            self.current_index_code,
            self.data_dir,
            start_date=start_date,
            end_date=end_date
        )
        
        if df is None or df.empty:
            self.statusBar().showMessage(f"未找到 {self.current_index_code} 的数据，请先更新指数数据")
            QMessageBox.warning(self, "数据错误", 
                f"未找到 {self.current_index_code} ({self.current_index_name}) 的数据\n"
                "请在指数列表中点击'更新'按钮下载指数数据")
            return
        
        # 添加技术指标
        df = attach_all_indicators(
            df,
            ma_windows=self.ma_windows,
            include_macd=self.macd_checkbox.isChecked(),
            include_kdj=self.kdj_checkbox.isChecked(),
            include_bbi=False,
            vol_ma_window=5
        )
        
        # 切换时停止实时行情
        if self.kline_widget.is_realtime_enabled:
            self.kline_widget.stop_realtime()
        
        # 更新K线图
        self.kline_widget.set_indicators(
            show_volume=self.volume_checkbox.isChecked(),
            show_macd=self.macd_checkbox.isChecked(),
            show_kdj=self.kdj_checkbox.isChecked()
        )
        self.kline_widget.set_ma_windows(self.ma_windows)
        self.kline_widget.set_data(df, self.current_index_code, self.current_index_name, is_index=True)
        
        # 更新窗口标题
        self.setWindowTitle(f"来财 - 指数 {self.current_index_code} {self.current_index_name}")
        
        self.statusBar().showMessage(
            f"指数 {self.current_index_code} {self.current_index_name} | "
            f"数据范围: {df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')} | "
            f"共 {len(df)} 根K线"
        )
        
        # 根据交易时间自动开启实时行情
        last_data_date = df['date'].max().date()
        if should_update_realtime_kline(last_data_date):
            if self.kline_widget.start_realtime():
                self.statusBar().showMessage(f"📡 已开启 {self.current_index_code} {self.current_index_name} 实时指数行情", 3000)
    
    def on_indicator_changed(self, state):
        """处理指标复选框变化"""
        # 同步菜单状态
        self.volume_action.setChecked(self.volume_checkbox.isChecked())
        self.macd_action.setChecked(self.macd_checkbox.isChecked())
        self.kdj_action.setChecked(self.kdj_checkbox.isChecked())
        
        # 重新加载图表（根据当前视图）
        if self.current_view == "etf":
            self.load_and_display_etf_chart()
        elif self.current_view == "index":
            self.load_and_display_index_chart()
        else:
            self.load_and_display_chart()
    
    def on_ma_changed(self, index):
        """处理均线选择变化"""
        ma_options = [
            [5, 10, 20],
            [5, 10, 20, 60],
            [10, 20, 60],
            [5, 10, 30, 60],
        ]
        
        if 0 <= index < len(ma_options):
            self.ma_windows = ma_options[index]
            # 根据当前视图刷新
            if self.current_view == "etf":
                self.load_and_display_etf_chart()
            else:
                self.load_and_display_chart()
    
    def refresh_chart(self):
        """刷新图表"""
        if self.current_view == "etf":
            self.load_and_display_etf_chart()
        elif self.current_view == "index":
            self.load_and_display_index_chart()
        else:
            self.load_and_display_chart()
    
    def show_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于",
            "来财\n\n"
            "基于 PyQt6 和 pyqtgraph 开发\n\n"
            "功能特性:\n"
            "• K线图显示\n"
            "• 均线指标 (MA5/MA10/MA20等)\n"
            "• MACD 指标\n"
            "• KDJ 指标\n"
            "• 成交量显示\n"
            "• 十字光标\n"
            "• 股票搜索\n"
        )

    def refresh_all_today_kline(self):
        """一键刷新今日所有股票的实时日线"""
        # 获取配置
        config = self.scheduler_manager.config
        data_source = config.get("data_source", "xtquant")
        token = config.get("tushare_token", os.environ.get("TUSHARE_TOKEN", ""))
        
        # 显示更新对话框并自动开始
        self.update_dialog = UpdateDialog(self, token)
        self.update_dialog.setWindowTitle("同步今日实时数据")
        self.update_dialog.start_update.connect(self.start_data_update)
        self.update_dialog.stop_update.connect(self.stop_data_update)
        
        # 切换到正确的数据源
        index = self.update_dialog.source_combo.findData(data_source)
        if index >= 0:
            self.update_dialog.source_combo.setCurrentIndex(index)
            
        # 设置为增量更新
        self.update_dialog.full_update_cb.setChecked(False)
        
        # 显示对话框
        self.update_dialog.show()
        
        # 自动开始
        self.update_dialog.on_start_clicked()

    def show_update_dialog(self):
        """显示数据更新对话框"""
        default_token = os.environ.get("TUSHARE_TOKEN", "")
        self.update_dialog = UpdateDialog(self, default_token)
        self.update_dialog.start_update.connect(self.start_data_update)
        self.update_dialog.stop_update.connect(self.stop_data_update)
        self.update_dialog.exec()

    def show_etf_update_dialog(self):
        """显示ETF数据更新对话框"""
        # 使用简化的对话框，只需要选择增量/全量更新
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout as HBox, QCheckBox, QPushButton, QLabel, QProgressBar, QTextEdit
        
        dialog = QDialog(self)
        dialog.setWindowTitle("更新ETF数据")
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(400)
        
        layout = QVBoxLayout(dialog)
        
        # 说明
        info_label = QLabel("使用 xtquant/miniQMT 获取ETF日线数据")
        info_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(info_label)
        
        # 全量更新选项
        full_update_cb = QCheckBox("全量更新（从2019年开始）")
        full_update_cb.setToolTip("勾选后将重新拉取所有历史数据，否则只增量更新")
        layout.addWidget(full_update_cb)
        
        # 进度条
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        layout.addWidget(progress_bar)
        
        # 日志
        log_text = QTextEdit()
        log_text.setReadOnly(True)
        log_text.setMaximumHeight(200)
        layout.addWidget(log_text)
        
        # 按钮
        start_btn = QPushButton("开始更新")
        stop_btn = QPushButton("停止")
        stop_btn.setEnabled(False)
        
        btn_layout = HBox()
        btn_layout.addWidget(start_btn)
        btn_layout.addWidget(stop_btn)
        layout.addLayout(btn_layout)
        
        # ETF配置文件路径
        etf_config_path = Path(__file__).parent / "config" / "etf_list.json"
        
        # 更新线程引用
        self.etf_update_thread = None
        
        def on_start():
            nonlocal self
            if self.etf_update_thread and self.etf_update_thread.isRunning():
                return
            
            start_btn.setEnabled(False)
            stop_btn.setEnabled(True)
            log_text.clear()
            log_text.append("正在启动ETF数据更新...")
            
            self.etf_update_thread = ETFUpdateThread(
                self.data_dir,
                str(etf_config_path),
                full_update=full_update_cb.isChecked(),
                max_workers=4
            )
            
            def update_progress(current, total, msg):
                progress_bar.setRange(0, total)
                progress_bar.setValue(current)
            
            def append_log(msg):
                log_text.append(msg)
            
            def on_finished(success, msg):
                start_btn.setEnabled(True)
                stop_btn.setEnabled(False)
                log_text.append(f"\n{'✓ 成功' if success else '✗ 失败'}: {msg}")
                if success:
                    # 刷新ETF列表
                    self.load_etf_list()
            
            self.etf_update_thread.progress_updated.connect(update_progress)
            self.etf_update_thread.log_message.connect(append_log)
            self.etf_update_thread.finished_signal.connect(on_finished)
            self.etf_update_thread.start()
        
        def on_stop():
            if self.etf_update_thread and self.etf_update_thread.isRunning():
                self.etf_update_thread.stop()
                log_text.append("正在停止...")
        
        start_btn.clicked.connect(on_start)
        stop_btn.clicked.connect(on_stop)
        
        dialog.exec()

    def show_etf_list_update_dialog(self):
        """显示更新ETF列表对话框（从xtquant获取完整ETF列表）"""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout as HBox, 
            QPushButton, QLabel, QProgressBar, QTextEdit
        )
        
        dialog = QDialog(self)
        dialog.setWindowTitle("更新ETF列表")
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(500)
        
        layout = QVBoxLayout(dialog)
        
        # 说明
        info_label = QLabel(
            "从 xtquant/miniQMT 获取完整的ETF列表并更新配置文件。\n"
            "这将自动获取所有可交易的ETF，并按类别分类。"
        )
        info_label.setStyleSheet("color: #888; font-size: 12px; margin-bottom: 10px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # 当前状态
        etf_config_path = Path(__file__).parent / "config" / "etf_list.json"
        current_count = 0
        if etf_config_path.exists():
            try:
                import json
                with open(etf_config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                for cat in config.get("categories", []):
                    current_count += len(cat.get("etfs", []))
            except:
                pass
        
        status_label = QLabel(f"当前配置中有 {current_count} 只ETF")
        status_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(status_label)
        
        # 进度条
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        layout.addWidget(progress_bar)
        
        # 日志
        log_text = QTextEdit()
        log_text.setReadOnly(True)
        layout.addWidget(log_text)
        
        # 按钮
        start_btn = QPushButton("开始更新ETF列表")
        stop_btn = QPushButton("停止")
        stop_btn.setEnabled(False)
        close_btn = QPushButton("关闭")
        
        btn_layout = HBox()
        btn_layout.addWidget(start_btn)
        btn_layout.addWidget(stop_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
        # 更新线程引用
        self.etf_list_update_thread = None
        
        def on_start():
            nonlocal self
            if self.etf_list_update_thread and self.etf_list_update_thread.isRunning():
                return
            
            start_btn.setEnabled(False)
            stop_btn.setEnabled(True)
            close_btn.setEnabled(False)
            log_text.clear()
            log_text.append("正在启动ETF列表更新...")
            
            self.etf_list_update_thread = ETFListUpdateThread(str(etf_config_path))
            
            def update_progress(current, total, msg):
                progress_bar.setRange(0, total)
                progress_bar.setValue(current)
            
            def append_log(msg):
                log_text.append(msg)
            
            def on_finished(success, msg, stats):
                start_btn.setEnabled(True)
                stop_btn.setEnabled(False)
                close_btn.setEnabled(True)
                
                if success:
                    log_text.append(f"\n{'='*50}")
                    log_text.append(f"✓ {msg}")
                    # 更新状态标签
                    status_label.setText(f"当前配置中有 {stats.get('total', 0)} 只ETF")
                    # 刷新ETF列表显示
                    self.load_etf_list()
                    QMessageBox.information(dialog, "成功", msg)
                else:
                    log_text.append(f"\n✗ 失败: {msg}")
                    QMessageBox.warning(dialog, "失败", msg)
            
            self.etf_list_update_thread.progress_updated.connect(update_progress)
            self.etf_list_update_thread.log_message.connect(append_log)
            self.etf_list_update_thread.finished_signal.connect(on_finished)
            self.etf_list_update_thread.start()
        
        def on_stop():
            if self.etf_list_update_thread and self.etf_list_update_thread.isRunning():
                self.etf_list_update_thread.stop()
                log_text.append("正在停止...")
        
        start_btn.clicked.connect(on_start)
        stop_btn.clicked.connect(on_stop)
        close_btn.clicked.connect(dialog.close)
        
        dialog.exec()

    def start_data_update(self, token, full_update, exclude_boards, start_date="", 
                          data_source="tushare", period="1d"):
        """开始数据更新
        
        Args:
            token: Tushare token（仅 tushare 数据源需要）
            full_update: 是否全量更新
            exclude_boards: 要排除的板块列表
            start_date: 起始日期
            data_source: 数据源 ("tushare" 或 "xtquant")
            period: K线周期 ("1d", "1m", "5m", "15m", "30m", "60m")
        """
        if self.update_thread and self.update_thread.isRunning():
            return

        self._updating_codes = []  # 批量更新时清空，后续需要完全重新预加载
        
        self.update_thread = DataUpdateThread(
            self.data_dir,
            self.stocklist_path,
            token,
            full_update,
            exclude_boards,
            start_date=start_date if start_date else None,
            data_source=data_source,
            period=period
        )
        self.update_thread.progress_updated.connect(self.update_dialog.update_progress)
        self.update_thread.log_message.connect(self.update_dialog.append_log)
        self.update_thread.finished_signal.connect(self.on_update_finished)
        self.update_thread.start()

    def stop_data_update(self):
        """停止数据更新"""
        if self.update_thread and self.update_thread.isRunning():
            self.update_thread.stop()

    def on_update_finished(self, success, message):
        """数据更新完成"""
        if self.update_dialog:
            self.update_dialog.on_finished(success, message)
        
        if success:
            # 重新加载股票列表
            self.load_stock_list()
            
            # 刷新缓存
            cache = get_stock_cache()
            if cache.is_loaded():
                if self._updating_codes:
                    # 只更新单只或少量股票，只刷新这些股票的缓存
                    for code in self._updating_codes:
                        cache.reload_stock(code, self.data_dir)
                    self.statusBar().showMessage(f"✓ 已更新 {len(self._updating_codes)} 只股票的缓存")
                    self.load_and_display_chart()
                else:
                    # 批量更新，重新预加载所有数据
                    self.start_data_preload()
            else:
                self.load_and_display_chart()
            
            self._updating_codes = []  # 清空更新记录

    def show_stock_list_context_menu(self, position):
        """显示股票列表右键菜单"""
        item = self.stock_list_widget.list_widget.itemAt(position)
        if not item:
            return
            
        # 确保选中了该项
        self.stock_list_widget.list_widget.setCurrentItem(item)
        code = item.data(Qt.ItemDataRole.UserRole)
        name = self.name_map.get(code, "")
        
        menu = QMenu(self)
        
        # 交易下单
        trade_action = menu.addAction(f"💰 去交易 {name}({code})")
        trade_action.triggered.connect(lambda checked, c=code: self.open_broker_account(c))
        
        menu.addSeparator()
        
        # 筹码分布
        chip_action = menu.addAction("📊 筹码分布")
        chip_action.triggered.connect(lambda checked, c=code, n=name: self.show_chip_distribution(c, n))

        # 模拟训练
        simulate_action = menu.addAction("模拟训练")
        simulate_action.triggered.connect(self.open_simulator)
        menu.addSeparator()
        
        current_group = self.stock_list_widget.get_current_group()
        
        if current_group:
            # In a watchlist group - show remove option
            remove_action = menu.addAction("从当前分组移除")
            remove_action.triggered.connect(self.remove_current_from_group)
            menu.addSeparator()
        
        # 更新数据菜单
        update_menu = menu.addMenu("更新数据")
        
        refresh_today_action = update_menu.addAction("刷新今日当前时刻K线")
        refresh_today_action.triggered.connect(self.refresh_all_today_kline)
        
        update_menu.addSeparator()
        
        inc_update_action = update_menu.addAction("增量更新 (补齐历史数据)")
        inc_update_action.triggered.connect(self.update_current_stock_incremental)
        
        full_update_action = update_menu.addAction("重新拉取 (指定日期)...")
        full_update_action.triggered.connect(self.update_current_stock_full)
        
        menu.addSeparator()

        # Add to watchlist submenu
        add_to_fav_menu = menu.addMenu("添加到自选股")
        
        groups = self.watchlist_manager.get_all_groups()
        if not groups:
            add_to_fav_menu.addAction("无分组").setEnabled(False)
        else:
            for group in groups:
                action = add_to_fav_menu.addAction(group)
                action.triggered.connect(lambda checked, g=group: self.add_current_to_watchlist(g))
                
        new_group_action = add_to_fav_menu.addAction("新建分组...")
        new_group_action.triggered.connect(self.create_group_and_add)
        
        menu.exec(self.stock_list_widget.list_widget.mapToGlobal(position))

    def show_etf_list_context_menu(self, position):
        """显示ETF列表右键菜单"""
        item = self.etf_list_widget.list_widget.itemAt(position)
        if not item:
            return
            
        # 确保选中了该项
        self.etf_list_widget.list_widget.setCurrentItem(item)
        code = item.data(Qt.ItemDataRole.UserRole)
        name = self.etf_name_map.get(code, "")
        
        menu = QMenu(self)
        
        # 交易下单
        trade_action = menu.addAction(f"💰 去交易 {name}({code})")
        trade_action.triggered.connect(lambda checked, c=code: self.open_broker_account(c))
        
        menu.addSeparator()
        
        # 筹码分布
        chip_action = menu.addAction("📊 筹码分布")
        chip_action.triggered.connect(lambda checked, c=code, n=name: self.show_chip_distribution(c, n))
        
        menu.addSeparator()
        
        # 如果当前在自选分组模式，显示移除选项
        current_group = self.etf_list_widget.get_current_group()
        if current_group:
            remove_action = menu.addAction(f"从 '{current_group}' 移除")
            remove_action.triggered.connect(self.remove_current_etf_from_group)
            menu.addSeparator()
        
        # 添加到自选分组子菜单
        add_to_fav_menu = menu.addMenu("添加到自选分组")
        
        groups = self.watchlist_manager.get_all_groups()
        if not groups:
            no_group_action = add_to_fav_menu.addAction("无分组")
            no_group_action.setEnabled(False)
        else:
            for group in groups:
                action = add_to_fav_menu.addAction(f"⭐ {group}")
                action.triggered.connect(lambda checked, g=group: self.add_current_etf_to_watchlist(g))
                
        add_to_fav_menu.addSeparator()
        new_group_action = add_to_fav_menu.addAction("✚ 新建分组...")
        new_group_action.triggered.connect(self.create_group_and_add_etf)
        
        menu.exec(self.etf_list_widget.list_widget.mapToGlobal(position))

    def show_watchlist_context_menu(self, position):
        """显示自选列表右键菜单"""
        item = self.watchlist_widget.list_widget.itemAt(position)
        if not item:
            return
            
        # 确保选中了该项
        self.watchlist_widget.list_widget.setCurrentItem(item)
        code = item.data(Qt.ItemDataRole.UserRole)
        is_etf = item.data(Qt.ItemDataRole.UserRole + 1)
        name = self.watchlist_widget._get_name(code)
        
        menu = QMenu(self)
        
        # 交易下单
        trade_action = menu.addAction(f"💰 去交易 {name}({code})")
        trade_action.triggered.connect(lambda checked, c=code: self.open_broker_account(c))
        
        menu.addSeparator()
        
        # 筹码分布
        chip_action = menu.addAction("📊 筹码分布")
        chip_action.triggered.connect(lambda checked, c=code, n=name: self.show_chip_distribution(c, n))
        
        menu.addSeparator()
        
        # 如果当前选中了分组，显示移除选项
        current_group = self.watchlist_widget.get_current_group()
        if current_group:
            # Check if protected
            if not self.watchlist_manager.is_protected_group(current_group):
                remove_action = menu.addAction(f"从 '{current_group}' 移除")
                remove_action.triggered.connect(lambda checked, c=code: self.remove_from_watchlist_group(c))
                menu.addSeparator()
        
        # 添加到自选分组子菜单
        add_to_fav_menu = menu.addMenu("添加到其他分组")
        
        groups = self.watchlist_manager.get_all_groups()
        if not groups:
            no_group_action = add_to_fav_menu.addAction("无分组")
            no_group_action.setEnabled(False)
        else:
            for group in groups:
                if group != current_group:  # 排除当前分组
                    action = add_to_fav_menu.addAction(f"⭐ {group}")
                    action.triggered.connect(lambda checked, g=group, c=code: self.add_to_watchlist_group(c, g))
                    
        add_to_fav_menu.addSeparator()
        new_group_action = add_to_fav_menu.addAction("✚ 新建分组...")
        new_group_action.triggered.connect(lambda checked, c=code: self.create_group_and_add_from_watchlist(c))
        
        menu.exec(self.watchlist_widget.list_widget.mapToGlobal(position))

    def add_to_watchlist_group(self, code: str, group_name: str):
        """添加代码到指定自选分组"""
        success, msg = self.watchlist_manager.add_to_group(group_name, code)
        if success:
            self.statusBar().showMessage(msg)
            # Refresh watchlist widget
            self.watchlist_widget.update_group_combo()
        else:
            QMessageBox.warning(self, "提示", msg)

    def remove_from_watchlist_group(self, code: str):
        """从当前自选分组移除"""
        current_group = self.watchlist_widget.get_current_group()
        if not current_group:
            return
            
        reply = QMessageBox.question(
            self, "确认移除", 
            f"确定要从 '{current_group}' 移除 {code} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            success, msg = self.watchlist_widget.remove_from_current_group(code)
            if success:
                self.statusBar().showMessage(msg)
                # Also update stock_list_widget and etf_list_widget combos
                self.stock_list_widget.update_group_combo()
                self.etf_list_widget.update_group_combo()
            else:
                QMessageBox.warning(self, "错误", msg)

    def create_group_and_add_from_watchlist(self, code: str):
        """创建新分组并添加代码"""
        name, ok = QInputDialog.getText(self, "新建分组", "请输入分组名称:")
        if ok and name:
            success, msg = self.watchlist_manager.create_group(name)
            if success:
                # Add code to new group
                self.watchlist_manager.add_to_group(name, code)
                self.statusBar().showMessage(f"已创建分组 '{name}' 并添加 {code}")
                # Update all group combos
                self.stock_list_widget.update_group_combo()
                self.etf_list_widget.update_group_combo()
                self.watchlist_widget.update_group_combo()
            else:
                QMessageBox.warning(self, "错误", msg)

    def add_current_to_watchlist(self, group_name):
        """添加当前选中的股票到自选股"""
        code = self.stock_list_widget.get_selected_stock()
        if not code:
            return
            
        success, msg = self.watchlist_manager.add_to_group(group_name, code)
        if success:
            self.statusBar().showMessage(msg)
            # If currently showing this group, refresh it
            if self.stock_list_widget.get_current_group() == group_name:
                self.stock_list_widget.on_group_combo_changed(
                    self.stock_list_widget.group_combo.currentIndex()
                )
            # 同步更新自选列表组件
            if self.watchlist_widget.get_current_group() == group_name:
                self.watchlist_widget.on_group_combo_changed(
                    self.watchlist_widget.group_combo.currentIndex()
                )
        else:
            QMessageBox.warning(self, "提示", msg)
    def remove_current_from_group(self):
        """从当前分组移除选中的股票"""
        code = self.stock_list_widget.get_selected_stock()
        if not code:
            return
            
        group_name = self.stock_list_widget.get_current_group()
        if not group_name:
            return
            
        reply = QMessageBox.question(
            self, "确认移除", 
            f"确定要从 '{group_name}' 移除 {code} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            success, msg = self.stock_list_widget.remove_stock_from_current_group(code)
            if success:
                self.statusBar().showMessage(msg)
                # 同步更新自选列表组件
                if self.watchlist_widget.get_current_group() == group_name:
                    self.watchlist_widget.on_group_combo_changed(
                        self.watchlist_widget.group_combo.currentIndex()
                    )
            else:
                QMessageBox.warning(self, "错误", msg)
    def create_group_and_add(self):
        """新建分组并添加当前股票"""
        name, ok = QInputDialog.getText(self, "新建分组", "请输入分组名称:")
        if ok and name:
            success, msg = self.watchlist_manager.create_group(name)
            if success:
                self.stock_list_widget.update_group_combo()
                self.etf_list_widget.update_group_combo()  # 同步更新ETF分组列表
                self.watchlist_widget.update_group_combo()  # 同步更新自选列表
                self.add_current_to_watchlist(name)
            else:
                QMessageBox.warning(self, "错误", msg)
    # ==================== ETF 自选分组相关方法 ====================
    
    def add_current_etf_to_watchlist(self, group_name: str):
        """添加当前选中的ETF到自选分组"""
        code = self.etf_list_widget.get_selected_etf()
        if not code:
            return
            
        success, msg = self.watchlist_manager.add_to_group(group_name, code)
        if success:
            self.statusBar().showMessage(msg)
            # 如果当前正在显示这个分组，刷新显示
            if self.etf_list_widget.get_current_group() == group_name:
                self.etf_list_widget.on_group_combo_changed(
                    self.etf_list_widget.group_combo.currentIndex()
                )
            # 同时更新ETF列表的分组下拉框
            self.etf_list_widget.update_group_combo()
            # 同步更新自选列表组件
            if self.watchlist_widget.get_current_group() == group_name:
                self.watchlist_widget.on_group_combo_changed(
                    self.watchlist_widget.group_combo.currentIndex()
                )
        else:
            QMessageBox.warning(self, "提示", msg)
    
    def remove_current_etf_from_group(self):
        """从当前自选分组移除选中的ETF"""
        code = self.etf_list_widget.get_selected_etf()
        if not code:
            return
            
        group_name = self.etf_list_widget.get_current_group()
        if not group_name:
            return
        
        name = self.etf_name_map.get(code, code)
        reply = QMessageBox.question(
            self, "确认移除", 
            f"确定要从 '{group_name}' 移除 {name}({code}) 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            success, msg = self.etf_list_widget.remove_etf_from_current_group(code)
            if success:
                self.statusBar().showMessage(msg)
                # 同步更新自选列表组件
                if self.watchlist_widget.get_current_group() == group_name:
                    self.watchlist_widget.on_group_combo_changed(
                        self.watchlist_widget.group_combo.currentIndex()
                    )
            else:
                QMessageBox.warning(self, "错误", msg)
    
    def create_group_and_add_etf(self):
        """新建分组并添加当前ETF"""
        name, ok = QInputDialog.getText(self, "新建自选分组", "请输入分组名称:")
        if ok and name:
            success, msg = self.watchlist_manager.create_group(name)
            if success:
                self.stock_list_widget.update_group_combo()  # 同步更新股票列表的分组
                self.etf_list_widget.update_group_combo()
                self.watchlist_widget.update_group_combo()  # 同步更新自选列表
                self.add_current_etf_to_watchlist(name)
            else:
                QMessageBox.warning(self, "错误", msg)
    def update_current_stock_incremental(self):
        """增量更新当前股票"""
        code = self.stock_list_widget.get_selected_stock()
        if not code:
            return
        self.start_single_stock_update(code, full_update=False)

    def update_current_stock_full(self):
        """全量更新当前股票（指定日期）"""
        code = self.stock_list_widget.get_selected_stock()
        if not code:
            return
            
        # Ask for start date
        dialog = QDialog(self)
        dialog.setWindowTitle("选择起始日期")
        layout = QVBoxLayout(dialog)
        
        date_edit = QDateEdit()
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate().addYears(-1)) # Default 1 year ago
        date_edit.setDisplayFormat("yyyy-MM-dd")
        layout.addWidget(QLabel("起始日期:"))
        layout.addWidget(date_edit)
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            start_date = date_edit.date().toString("yyyyMMdd")
            self.start_single_stock_update(code, full_update=True, start_date=start_date)

    def open_simulator(self):
        """打开模拟训练窗口"""
        code = self.stock_list_widget.get_selected_stock()
        if not code:
            return
            
        # 创建独立窗口
        simulator_window = QMainWindow(self)
        simulator_window.setWindowTitle(f"模拟交易 - {code}")
        simulator_window.resize(1200, 800)
        simulator_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        # 实例化模拟器组件
        simulator_widget = TradingSimulatorWidget(self.data_dir)
        simulator_window.setCentralWidget(simulator_widget)
        
        # 预选股票
        index = simulator_widget.stock_combo.findData(code)
        if index >= 0:
            simulator_widget.stock_combo.setCurrentIndex(index)
            
        simulator_window.show()
        
        # 保存引用并处理关闭事件
        self.simulator_windows.append(simulator_window)
        # 当窗口关闭时从列表中移除引用
        simulator_window.destroyed.connect(lambda: self.simulator_windows.remove(simulator_window) if simulator_window in self.simulator_windows else None)

    def open_screener(self):
        """打开智能选股窗口"""
        screener_window = QMainWindow(self)
        screener_window.setWindowTitle("智能选股")
        screener_window.resize(1000, 600)
        screener_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        # 传递 stocklist_path 以确保正确加载股票名称
        screener_widget = StockScreenerWidget(self.data_dir, self.stocklist_path)
        screener_widget.stockSelected.connect(self.on_screener_stock_selected)
        screener_widget.strategyFinished.connect(self.on_strategy_finished)
        screener_window.setCentralWidget(screener_widget)
        
        screener_window.show()
        
        self.screener_windows.append(screener_window)
        screener_window.destroyed.connect(lambda: self.screener_windows.remove(screener_window) if screener_window in self.screener_windows else None)
    
    def open_ai_tool(self):
        """打开 AI 智能交易工具"""
        ai_window = QMainWindow(self)
        ai_window.setWindowTitle("AI 智能交易训练中心")
        ai_window.resize(1000, 700)
        ai_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        ai_widget = AITradingWidget(self.data_dir)
        ai_window.setCentralWidget(ai_widget)
        
        ai_window.show()
        
        self.ai_windows.append(ai_window)
        ai_window.destroyed.connect(lambda: self.ai_windows.remove(ai_window) if ai_window in self.ai_windows else None)

    def open_etf_grid_strategy(self):
        """打开 ETF 网格交易策略窗口"""
        etf_grid_window = QMainWindow(self)
        etf_grid_window.setWindowTitle("ETF网格交易策略")
        etf_grid_window.resize(1300, 850)
        etf_grid_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        etf_grid_widget = ETFGridWidget(self.data_dir)
        etf_grid_window.setCentralWidget(etf_grid_widget)
        
        etf_grid_window.show()
        
        self.etf_grid_windows.append(etf_grid_window)
        etf_grid_window.destroyed.connect(lambda: self.etf_grid_windows.remove(etf_grid_window) if etf_grid_window in self.etf_grid_windows else None)

    def open_backtest_window(self):
        """打开策略回测窗口"""
        backtest_window = QMainWindow(self)
        backtest_window.setWindowTitle("策略回测")
        backtest_window.resize(1200, 800)
        backtest_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        backtest_widget = BacktestWidget(self.data_dir, self.stocklist_path)
        backtest_window.setCentralWidget(backtest_widget)
        
        # 预选当前股票
        if self.current_code:
            index = backtest_widget.stock_combo.findData(self.current_code)
            if index >= 0:
                backtest_widget.stock_combo.setCurrentIndex(index)
        
        backtest_window.show()
        
        self.backtest_windows.append(backtest_window)
        backtest_window.destroyed.connect(lambda: self.backtest_windows.remove(backtest_window) if backtest_window in self.backtest_windows else None)

    def open_cross_sectional_backtest_window(self):
        """打开截面策略回测窗口"""
        window = QMainWindow(self)
        window.setWindowTitle("截面策略回测 (多因子)")
        window.resize(1200, 800)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        widget = CrossSectionalBacktestWidget(self.data_dir)
        window.setCentralWidget(widget)
        
        window.show()
        
        self.cross_sectional_windows.append(window)
        window.destroyed.connect(lambda: self.cross_sectional_windows.remove(window) if window in self.cross_sectional_windows else None)

    def open_factor_library_window(self):
        """打开因子库窗口"""
        window = QMainWindow(self)
        window.setWindowTitle("因子库")
        window.resize(1400, 900)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        widget = FactorLibraryWidget(self.data_dir, self.stocklist_path)
        window.setCentralWidget(widget)
        
        # 预选当前股票
        if self.current_code:
            index = widget.stock_combo.findData(self.current_code)
            if index >= 0:
                widget.stock_combo.setCurrentIndex(index)
        
        window.show()

    def open_sector_window(self):
        """打开热门板块独立窗口"""
        # 检查是否已经打开了板块窗口
        if self.sector_window and self.sector_window.isVisible():
            self.sector_window.activateWindow()
            self.sector_window.raise_()
            return
        
        # 创建新窗口
        self.sector_window = SectorWindow(self)
        self.sector_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        # 连接股票选择信号
        self.sector_window.stockSelected.connect(self._on_sector_stock_selected)
        
        # 窗口关闭时清理引用
        self.sector_window.destroyed.connect(lambda: setattr(self, 'sector_window', None))
        
        self.sector_window.show()
        
        # 自动启动服务
        self.sector_window.start_service()
    
    def _on_sector_stock_selected(self, code: str, name: str):
        """板块窗口中选择股票时的处理"""
        # 切换到股票视图
        self.left_tabs.setCurrentIndex(0)  # 切换到股票列表Tab
        
        # 查找并选中该股票
        stock_code_with_suffix = None
        for stock in self.stock_list:
            if stock.startswith(code):
                stock_code_with_suffix = stock
                break
        
        if stock_code_with_suffix:
            self.on_stock_selected(stock_code_with_suffix)
            # 在股票列表中定位
            self.stock_list_widget.select_stock(stock_code_with_suffix)
        else:
            # 股票不在列表中，直接尝试加载
            self.current_code = code
            self.current_name = name or code
            self.current_view = "stock"
            self.load_and_display_chart()

    def open_broker_account(self, stock_code: str = None):
        """打开交易窗口"""
        # 检查是否已经打开了交易窗口
        if hasattr(self, 'broker_window') and self.broker_window and self.broker_window.isVisible():
            self.broker_window.activateWindow()
            self.broker_window.raise_()
            if stock_code:
                broker_widget = self.broker_window.centralWidget()
                if isinstance(broker_widget, BrokerAccountWidget):
                    broker_widget.set_stock_code(stock_code)
            return

        self.broker_window = QMainWindow(self)
        self.broker_window.setWindowTitle("交易")
        self.broker_window.resize(1200, 800)
        self.broker_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        # 将主窗口的名称映射传递给交易组件（合并股票和ETF名称映射）
        combined_name_map = {**self.name_map, **self.etf_name_map}
        broker_widget = BrokerAccountWidget(name_map=combined_name_map)
        if stock_code:
            broker_widget.set_stock_code(stock_code)
        
        # 连接持仓更新信号
        broker_widget.positionsUpdated.connect(self.on_broker_positions_updated)
            
        self.broker_window.setCentralWidget(broker_widget)
        
        # 窗口关闭时清除引用
        self.broker_window.destroyed.connect(self._on_broker_window_destroyed)
        
        self.broker_window.show()

    def _on_broker_window_destroyed(self):
        self.broker_window = None
    
    def on_broker_positions_updated(self, position_codes: list):
        """处理券商持仓数据更新
        
        Args:
            position_codes: 持仓股票代码列表
        """
        # 更新中金持仓分组
        success, msg = self.watchlist_manager.update_broker_positions(position_codes)
        
        if success:
            # 更新股票列表和ETF列表的分组下拉框
            self.stock_list_widget.update_group_combo()
            self.etf_list_widget.update_group_combo()
            
            # 如果当前正在显示中金持仓分组，刷新显示
            if self.stock_list_widget.get_current_group() == "中金持仓":
                self.stock_list_widget.on_group_combo_changed(
                    self.stock_list_widget.group_combo.currentIndex()
                )
            
            self.statusBar().showMessage(msg)

    def open_ai_agent(self):
        """打开/关闭嵌入式智能体面板"""
        if self.agent_widget.isVisible():
            self.agent_widget.setVisible(False)
            # 恢复之前的比例，或者设为默认
            self.splitter.setSizes([150, 1050, 0])
            self.statusBar().showMessage("智能体面板已隐藏")
        else:
            self.agent_widget.setVisible(True)
            # 设置显示比例，左:中:右 = 150 : 700 : 350
            self.splitter.setSizes([150, 700, 350])
            self.statusBar().showMessage("智能体面板已显示")
            # 自动滚动到底部
            if hasattr(self.agent_widget, 'scroll_area'):
                self.agent_widget.scroll_area.verticalScrollBar().setValue(
                    self.agent_widget.scroll_area.verticalScrollBar().maximum()
                )
            # 聚焦输入框
            if hasattr(self.agent_widget, 'message_input'):
                self.agent_widget.message_input.setFocus()

    def capture_kline_screenshot(self):
        """截取当前K线图并发送给智能体"""
        if not self.current_code:
            self.statusBar().showMessage("❌ 请先选择一只股票")
            return
            
        # 确保智能体面板可见
        if not self.agent_widget.isVisible():
            self.open_ai_agent()
            
        # 给予一点点时间让界面渲染完成（如果刚打开）
        QApplication.processEvents()
        
        # 截取 KLineWidget
        pixmap = self.kline_widget.grab()
        
        # 发送给智能体
        if hasattr(self.agent_widget, 'handle_image_pasted'):
            self.agent_widget.handle_image_pasted(pixmap)
            self.statusBar().showMessage(f"📸 已截取 {self.current_code} K线图并添加至智能体附件")
        else:
            self.statusBar().showMessage("❌ 智能体组件不支持图片接收")

    def attach_kline_data_to_agent(self):
        """Generate and attach current stock K-line data file to agent"""
        if not self.current_code:
            self.statusBar().showMessage("❌ 请先选择一只股票")
            return
            
        # Ensure agent panel is visible
        if not self.agent_widget.isVisible():
            self.open_ai_agent()
            
        QApplication.processEvents()
        
        # Directly use the data already loaded and calculated in kline_widget
        # This avoids redundant data loading and indicator calculation
        df = self.kline_widget.data
        
        if df is None or df.empty:
            self.statusBar().showMessage(f"❌ 未找到 {self.current_code} 的数据")
            return
        
        # Make a copy to avoid modifying original data
        df = df.copy()
        
        # Prepare export columns
        export_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        
        # Add MA columns if exist
        for ma in self.ma_windows:
            col = f'MA{ma}'
            if col in df.columns:
                export_cols.append(col)
        
        # Add MACD columns if exist
        for col in ['DIF', 'DEA', 'MACD']:
            if col in df.columns:
                export_cols.append(col)
        
        # Add KDJ columns if exist
        for col in ['K', 'D', 'J']:
            if col in df.columns:
                export_cols.append(col)
        
        # Filter available columns
        available_cols = [c for c in export_cols if c in df.columns]
        export_df = df[available_cols].copy()
        
        # Format date column
        if 'date' in export_df.columns:
            export_df['date'] = export_df['date'].dt.strftime('%Y-%m-%d')
        
        # Create temp file
        import tempfile
        import time
        temp_dir = tempfile.gettempdir()
        file_name = f"{self.current_code}_{self.current_name}_kline_{int(time.time())}.csv"
        file_path = Path(temp_dir) / file_name
        
        # Save to CSV
        export_df.to_csv(file_path, index=False, encoding='utf-8-sig')
        
        # Add as attachment
        if hasattr(self.agent_widget, 'add_attachments'):
            self.agent_widget.add_attachments([str(file_path)])
            self.statusBar().showMessage(
                f"📊 已生成 {self.current_code} K线数据文件并添加至智能体附件 "
                f"({len(export_df)}条记录，含MACD/KDJ指标)"
            )
        else:
            self.statusBar().showMessage("❌ 智能体组件不支持附件添加")

    def start_stock_analysis(self, max_days: int = 750):
        """Start AI stock analysis for current stock
        
        Args:
            max_days: Maximum days of K-line data to analyze (0 means all data)
        """
        if not self.current_code:
            self.statusBar().showMessage("❌ 请先选择一只股票")
            return
        
        # Ensure agent panel is visible
        if not self.agent_widget.isVisible():
            self.open_ai_agent()
        
        QApplication.processEvents()
        
        # Get K-line data from kline_widget
        df = self.kline_widget.data
        
        if df is None or df.empty:
            self.statusBar().showMessage(f"❌ 未找到 {self.current_code} 的数据")
            return
        
        # Make a copy to avoid modifying original data
        df = df.copy()
        
        # Start the analysis
        if hasattr(self.agent_widget, 'start_stock_analysis'):
            range_text = "全部数据" if max_days == 0 else f"最近{max_days}天"
            self.agent_widget.start_stock_analysis(
                df=df,
                stock_code=self.current_code,
                stock_name=self.current_name,
                max_days=max_days
            )
            self.statusBar().showMessage(f"📈 开始分析 {self.current_name}({self.current_code}) - {range_text}...")
        else:
            self.statusBar().showMessage("❌ 智能体组件不支持股票分析功能")

    def open_notification_dialog(self, stocks_data=None):
        """打开消息推送对话框
        
        Args:
            stocks_data: 可选的选股数据列表
        """
        dialog = NotificationDialog(self, stocks_data=stocks_data)
        dialog.exec()

    def open_scheduled_task_dialog(self):
        """打开定时任务配置对话框"""
        dialog = ScheduledTaskDialog(self.scheduler_manager, self)
        dialog.set_dark_style()
        dialog.exec()

    def on_scheduled_task_finished(self, success, message):
        """定时任务完成回调"""
        if success:
            self.statusBar().showMessage(f"✓ 定时任务执行成功: {message}", 5000)
            # 任务执行完可能更新了数据，如果是手动执行的可以考虑刷新，
            # 但定时任务通常在后台，这里只提示一下
            # self.load_stock_list() 
        else:
            self.statusBar().showMessage(f"⚠ 定时任务执行失败: {message}", 10000)

    def on_screener_stock_selected(self, code):
        """处理选股结果点击"""
        # 在主窗口选中该股票
        self.stock_list_widget.select_stock(code)
        
        # 获取股票名称
        name = self.name_map.get(code, "")
        
        # 立即触发选中逻辑，更新 K 线图
        self.on_stock_selected(code, name)
        
        # 激活主窗口
        self.activateWindow()
        self.raise_()

    def on_strategy_finished(self, strategy_name, codes):
        """处理选股策略完成，同步到自选股分组"""
        group_name = f"策略: {strategy_name}"
        
        # 更新或创建分组
        self.watchlist_manager.update_group_stocks(group_name, codes)
        
        # 更新 UI
        self.stock_list_widget.update_group_combo()
        
        # 如果当前正在显示这个分组，触发刷新显示
        if self.stock_list_widget.get_current_group() == group_name:
            self.stock_list_widget.on_group_combo_changed(
                self.stock_list_widget.group_combo.findData(group_name)
            )
            
        self.statusBar().showMessage(f"已同步 {len(codes)} 只股票到分组 '{group_name}'")

    def show_chip_distribution(self, code: str, name: str = ""):
        """显示筹码分布图弹窗"""
        # 获取当前价格
        current_price = 0.0
        if self.kline_widget.data is not None and not self.kline_widget.data.empty:
            current_price = self.kline_widget.data.iloc[-1]['close']
        
        dialog = ChipDistributionDialog(code, name, current_price, self)
        dialog.show()
    
    def on_refresh_strategy(self, strategy_name):
        """处理从股票列表触发的策略刷新"""
        # 打开选股窗口并运行特定策略
        self.open_screener()
        
        # 获取最新打开的选股窗口
        if self.screener_windows:
            window = self.screener_windows[-1]
            screener_widget = window.centralWidget()
            
            # 在下拉框中选中该策略
            index = screener_widget.strategy_combo.findText(strategy_name)
            if index >= 0:
                screener_widget.strategy_combo.setCurrentIndex(index)
                # 自动开始选股
                screener_widget.toggle_screener()

    def start_single_stock_update(self, code, full_update=False, start_date=None):
        """启动单只股票更新"""
        default_token = os.environ.get("TUSHARE_TOKEN", "")
        self.update_dialog = UpdateDialog(self, default_token)
        self.update_dialog.setWindowTitle(f"更新股票 {code}")
        
        # Hide options that don't apply for single stock update
        self.update_dialog.full_update_cb.setVisible(False)
        self.update_dialog.exclude_gem_cb.setVisible(False)
        self.update_dialog.exclude_star_cb.setVisible(False)
        self.update_dialog.exclude_bj_cb.setVisible(False)
        
        # Connect dialog signals to a custom handler
        try:
            self.update_dialog.start_update.disconnect()
        except:
            pass
            
        # 新的信号有 6 个参数: token, full_update, exclude_boards, start_date, data_source, period
        self.update_dialog.start_update.connect(
            lambda t, f, e, sd, ds, p: self.run_single_stock_thread(
                code, t, full_update, start_date, ds, p
            )
        )
        
        self.update_dialog.exec()

    def run_single_stock_thread(self, code, token, full_update, start_date, 
                                 data_source="tushare", period="1d"):
        """运行单只股票更新线程
        
        Args:
            code: 股票代码
            token: Tushare token
            full_update: 是否全量更新
            start_date: 起始日期
            data_source: 数据源
            period: K线周期
        """
        if self.update_thread and self.update_thread.isRunning():
            return

        self._updating_codes = [code]  # 记录正在更新的股票代码
        
        self.update_thread = DataUpdateThread(
            self.data_dir,
            self.stocklist_path,
            token,
            full_update=full_update,
            codes=[code],
            start_date=start_date,
            data_source=data_source,
            period=period
        )
        self.update_thread.progress_updated.connect(self.update_dialog.update_progress)
        self.update_thread.log_message.connect(self.update_dialog.append_log)
        self.update_thread.finished_signal.connect(self.on_update_finished)
        self.update_thread.start()
