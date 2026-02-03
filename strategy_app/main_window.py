# main_window.py - 策略研究应用主窗口
"""
策略研究应用主窗口

功能：
- 策略选股
- 时序回测
- 截面回测
- 因子库管理
- AI模型训练
- ETF网格策略
"""
import os
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLabel, QStatusBar, QMenuBar, QMenu,
    QToolBar, QPushButton, QMessageBox, QApplication,
    QTabWidget
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QKeySequence, QIcon

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 本地模块
from data_loader import get_stock_list, load_stock_name_map
from indicators import attach_all_indicators


class StrategyMainWindow(QMainWindow):
    """策略研究应用主窗口"""
    
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
        
        self.setupUI()
        self.setup_menu()
        self.setup_toolbar()
        
        # 加载数据
        self.load_stock_list()
    
    def get_data_dir(self) -> str:
        """获取数据目录路径"""
        possible_paths = [
            Path(__file__).parent.parent / "data",
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
            Path(__file__).parent.parent / "stocklist" / "stocklist.csv",
            Path("./stocklist/stocklist.csv"),
            Path("../stocklist/stocklist.csv"),
        ]
        
        for p in possible_paths:
            if p.exists():
                return str(p)
        
        return str(possible_paths[0])
    
    def setupUI(self):
        """设置界面"""
        self.setWindowTitle("策略研究 - 来财量化")
        self.setMinimumSize(1400, 900)
        
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 主标签页
        self.main_tabs = QTabWidget()
        # 样式已在全局样式表中定义
        
        # 欢迎页面
        welcome_widget = self.create_welcome_widget()
        self.main_tabs.addTab(welcome_widget, "🏠 首页")
        
        main_layout.addWidget(self.main_tabs)
        
        # 状态栏
        self.statusBar().showMessage("就绪")
    
    def create_welcome_widget(self) -> QWidget:
        """创建欢迎页面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 标题
        title = QLabel("策略研究平台")
        title.setProperty("class", "welcome-title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        # 副标题
        subtitle = QLabel("量化策略回测与优化")
        subtitle.setProperty("class", "welcome-subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        
        # 功能按钮区
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(20)
        buttons_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 选股按钮
        screener_btn = QPushButton("📊 智能选股")
        screener_btn.setMinimumSize(150, 60)
        screener_btn.setProperty("class", "welcome-btn welcome-btn-primary")
        screener_btn.clicked.connect(self.open_screener)
        buttons_layout.addWidget(screener_btn)
        
        # 回测按钮
        backtest_btn = QPushButton("📈 策略回测")
        backtest_btn.setMinimumSize(150, 60)
        backtest_btn.setProperty("class", "welcome-btn welcome-btn-success")
        backtest_btn.clicked.connect(self.open_backtest)
        buttons_layout.addWidget(backtest_btn)
        
        # 截面回测按钮
        cross_btn = QPushButton("📉 截面回测")
        cross_btn.setMinimumSize(150, 60)
        cross_btn.setProperty("class", "welcome-btn welcome-btn-purple")
        cross_btn.clicked.connect(self.open_cross_sectional_backtest)
        buttons_layout.addWidget(cross_btn)
        
        # 因子库按钮
        factor_btn = QPushButton("🔬 因子库")
        factor_btn.setMinimumSize(150, 60)
        factor_btn.setProperty("class", "welcome-btn welcome-btn-orange")
        factor_btn.clicked.connect(self.open_factor_library)
        buttons_layout.addWidget(factor_btn)
        
        # AI训练按钮
        ai_btn = QPushButton("🤖 AI训练")
        ai_btn.setMinimumSize(150, 60)
        ai_btn.setProperty("class", "welcome-btn welcome-btn-yellow")
        ai_btn.clicked.connect(self.open_ai_training)
        buttons_layout.addWidget(ai_btn)
        
        layout.addLayout(buttons_layout)
        layout.addStretch()
        
        return widget
    
    def setup_menu(self):
        """设置菜单栏"""
        menubar = self.menuBar()
        
        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        
        exit_action = QAction("退出(&X)", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 策略菜单
        strategy_menu = menubar.addMenu("策略(&S)")
        
        screener_action = QAction("智能选股(&C)", self)
        screener_action.triggered.connect(self.open_screener)
        strategy_menu.addAction(screener_action)
        
        strategy_menu.addSeparator()
        
        backtest_action = QAction("时序回测(&B)", self)
        backtest_action.triggered.connect(self.open_backtest)
        strategy_menu.addAction(backtest_action)
        
        cross_action = QAction("截面回测(&M)", self)
        cross_action.triggered.connect(self.open_cross_sectional_backtest)
        strategy_menu.addAction(cross_action)
        
        strategy_menu.addSeparator()
        
        factor_action = QAction("因子库(&F)", self)
        factor_action.triggered.connect(self.open_factor_library)
        strategy_menu.addAction(factor_action)
        
        # 工具菜单
        tools_menu = menubar.addMenu("工具(&T)")
        
        ai_action = QAction("AI模型训练(&A)", self)
        ai_action.triggered.connect(self.open_ai_training)
        tools_menu.addAction(ai_action)
        
        etf_grid_action = QAction("ETF网格策略(&G)", self)
        etf_grid_action.triggered.connect(self.open_etf_grid)
        tools_menu.addAction(etf_grid_action)
        
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
        
        # 快速功能按钮
        screener_btn = QPushButton("📊 选股")
        screener_btn.clicked.connect(self.open_screener)
        toolbar.addWidget(screener_btn)
        
        backtest_btn = QPushButton("📈 回测")
        backtest_btn.clicked.connect(self.open_backtest)
        toolbar.addWidget(backtest_btn)
        
        cross_btn = QPushButton("📉 截面")
        cross_btn.clicked.connect(self.open_cross_sectional_backtest)
        toolbar.addWidget(cross_btn)
        
        toolbar.addSeparator()
        
        factor_btn = QPushButton("🔬 因子")
        factor_btn.clicked.connect(self.open_factor_library)
        toolbar.addWidget(factor_btn)
        
        ai_btn = QPushButton("🤖 AI")
        ai_btn.clicked.connect(self.open_ai_training)
        toolbar.addWidget(ai_btn)
    
    def load_stock_list(self):
        """加载股票列表"""
        self.statusBar().showMessage("正在加载股票列表...")
        QApplication.processEvents()
        
        self.stock_list = get_stock_list(self.data_dir)
        self.name_map = load_stock_name_map(self.stocklist_path)
        
        self.statusBar().showMessage(f"已加载 {len(self.stock_list)} 只股票")
    
    def open_screener(self):
        """打开选股器"""
        try:
            from widgets.stock_screener_widget import StockScreenerWidget
            
            # 检查是否已有选股器标签页
            for i in range(self.main_tabs.count()):
                if self.main_tabs.tabText(i) == "📊 选股":
                    self.main_tabs.setCurrentIndex(i)
                    return
            
            # 创建新的选股器
            screener = StockScreenerWidget(self.data_dir, self.stocklist_path)
            screener.stockSelected.connect(self.on_stock_selected)
            self.main_tabs.addTab(screener, "📊 选股")
            self.main_tabs.setCurrentIndex(self.main_tabs.count() - 1)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开选股器: {e}")
    
    def open_backtest(self):
        """打开回测界面"""
        try:
            from widgets.backtest_widget import BacktestWidget
            
            # 检查是否已有回测标签页
            for i in range(self.main_tabs.count()):
                if self.main_tabs.tabText(i) == "📈 回测":
                    self.main_tabs.setCurrentIndex(i)
                    return
            
            # 创建新的回测界面
            backtest = BacktestWidget(self.data_dir, self.stocklist_path)
            self.main_tabs.addTab(backtest, "📈 回测")
            self.main_tabs.setCurrentIndex(self.main_tabs.count() - 1)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开回测: {e}")
    
    def open_cross_sectional_backtest(self):
        """打开截面回测界面"""
        try:
            from widgets.cross_sectional_backtest_widget import CrossSectionalBacktestWidget
            
            # 检查是否已有截面回测标签页
            for i in range(self.main_tabs.count()):
                if self.main_tabs.tabText(i) == "📉 截面回测":
                    self.main_tabs.setCurrentIndex(i)
                    return
            
            # 创建新的截面回测界面
            cross_backtest = CrossSectionalBacktestWidget(self.data_dir, self.stocklist_path)
            self.main_tabs.addTab(cross_backtest, "📉 截面回测")
            self.main_tabs.setCurrentIndex(self.main_tabs.count() - 1)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开截面回测: {e}")
    
    def open_factor_library(self):
        """打开因子库"""
        try:
            from widgets.factor_library_widget import FactorLibraryWidget
            
            # 检查是否已有因子库标签页
            for i in range(self.main_tabs.count()):
                if self.main_tabs.tabText(i) == "🔬 因子库":
                    self.main_tabs.setCurrentIndex(i)
                    return
            
            # 创建新的因子库界面
            factor_lib = FactorLibraryWidget(self.data_dir, self.stocklist_path)
            self.main_tabs.addTab(factor_lib, "🔬 因子库")
            self.main_tabs.setCurrentIndex(self.main_tabs.count() - 1)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开因子库: {e}")
    
    def open_ai_training(self):
        """打开AI训练界面"""
        try:
            from widgets.ai_trading_widget import AITradingWidget
            
            # 检查是否已有AI训练标签页
            for i in range(self.main_tabs.count()):
                if self.main_tabs.tabText(i) == "🤖 AI训练":
                    self.main_tabs.setCurrentIndex(i)
                    return
            
            # 创建新的AI训练界面
            ai_widget = AITradingWidget()
            self.main_tabs.addTab(ai_widget, "🤖 AI训练")
            self.main_tabs.setCurrentIndex(self.main_tabs.count() - 1)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开AI训练: {e}")
    
    def open_etf_grid(self):
        """打开ETF网格策略"""
        try:
            from widgets.etf_grid_widget import ETFGridWidget
            
            # 检查是否已有ETF网格标签页
            for i in range(self.main_tabs.count()):
                if self.main_tabs.tabText(i) == "📊 ETF网格":
                    self.main_tabs.setCurrentIndex(i)
                    return
            
            # 创建新的ETF网格界面
            etf_grid = ETFGridWidget(self.data_dir)
            self.main_tabs.addTab(etf_grid, "📊 ETF网格")
            self.main_tabs.setCurrentIndex(self.main_tabs.count() - 1)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开ETF网格: {e}")
    
    def on_stock_selected(self, code: str):
        """处理股票选择信号"""
        self.statusBar().showMessage(f"选中股票: {code}")
    
    def show_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于 策略研究",
            "策略研究平台\n\n"
            "基于 PyQt6 开发\n\n"
            "功能:\n"
            "• 智能选股\n"
            "• 时序回测\n"
            "• 截面回测\n"
            "• 因子库管理\n"
            "• AI模型训练\n"
            "• ETF网格策略\n"
        )
    
    def closeEvent(self, event):
        """窗口关闭时清理资源"""
        # 清理所有标签页资源
        for i in range(self.main_tabs.count()):
            widget = self.main_tabs.widget(i)
            if hasattr(widget, 'close'):
                try:
                    widget.close()
                except:
                    pass
        
        event.accept()
