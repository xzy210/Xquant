# main_window.py - 策略研究应用主窗口
"""
策略研究应用主窗口

功能：
- 策略选股
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
        self.main_tabs.setTabsClosable(True)
        self.main_tabs.tabCloseRequested.connect(self.close_module_tab)
        
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
        subtitle = QLabel("量化策略研究与选股")
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
        
        # 第二行按钮
        buttons_layout2 = QHBoxLayout()
        buttons_layout2.setSpacing(20)
        buttons_layout2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # ETF网格按钮
        etf_grid_btn = QPushButton("📊 ETF网格")
        etf_grid_btn.setMinimumSize(150, 60)
        etf_grid_btn.setProperty("class", "welcome-btn welcome-btn-primary")
        etf_grid_btn.clicked.connect(self.open_etf_grid)
        buttons_layout2.addWidget(etf_grid_btn)
        
        layout.addLayout(buttons_layout)
        layout.addLayout(buttons_layout2)
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
        
        # 开发菜单（热重载功能）
        dev_menu = menubar.addMenu("开发(&D)")
        
        reload_all_action = QAction("重新加载所有模块(&R)", self)
        reload_all_action.setShortcut(QKeySequence("F5"))
        reload_all_action.triggered.connect(self.reload_all_modules)
        dev_menu.addAction(reload_all_action)
        
        reload_specific_action = QAction("重新加载指定模块(&S)...", self)
        reload_specific_action.triggered.connect(self.reload_specific_module)
        dev_menu.addAction(reload_specific_action)
        
        dev_menu.addSeparator()
        
        list_modules_action = QAction("查看已加载模块(&L)", self)
        list_modules_action.triggered.connect(self.show_loaded_modules)
        dev_menu.addAction(list_modules_action)
        
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
        
        toolbar.setVisible(False)
    
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
            cross_backtest = CrossSectionalBacktestWidget(self.data_dir)
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
            ai_widget = AITradingWidget(self.data_dir, self.stocklist_path)
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
    
    def reload_all_modules(self):
        """重新加载所有策略模块（热重载）"""
        try:
            from utils.module_reloader import ModuleReloader
            success = ModuleReloader.reload_strategy_modules(self)
            if success:
                self.statusBar().showMessage("模块热重载完成")
        except Exception as e:
            QMessageBox.critical(self, "热重载失败", f"重新加载模块时出错:\n{str(e)}")
    
    def reload_specific_module(self):
        """重新加载指定模块"""
        try:
            from utils.module_reloader import ModuleReloader
            from PyQt6.QtWidgets import QInputDialog
            
            modules = ModuleReloader.get_loaded_modules()
            
            module_name, ok = QInputDialog.getItem(
                self,
                "重新加载模块",
                "选择要重新加载的模块:",
                modules,
                editable=True
            )
            
            if ok and module_name:
                ModuleReloader.reload_specific_module(module_name, self)
                self.statusBar().showMessage(f"已重新加载: {module_name}")
        except Exception as e:
            QMessageBox.critical(self, "热重载失败", f"重新加载模块时出错:\n{str(e)}")
    
    def show_loaded_modules(self):
        """显示已加载的模块列表"""
        try:
            from utils.module_reloader import ModuleReloader
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
            
            dialog = QDialog(self)
            dialog.setWindowTitle("已加载模块")
            dialog.resize(600, 400)
            
            layout = QVBoxLayout(dialog)
            
            text = QTextEdit()
            text.setReadOnly(True)
            
            # 按类别分组显示
            modules = ModuleReloader.get_loaded_modules()
            
            content = f"共加载 {len(modules)} 个模块\n\n"
            
            categories = {
                'strategies.': "策略模块",
                'factors.': "因子模块", 
                'backtest.': "回测模块",
                'widgets.': "界面组件",
                'utils.': "工具模块",
            }
            
            for prefix, name in categories.items():
                category_modules = [m for m in modules if m.startswith(prefix)]
                if category_modules:
                    content += f"=== {name} ({len(category_modules)}) ===\n"
                    for m in category_modules:
                        content += f"  {m}\n"
                    content += "\n"
            
            text.setText(content)
            layout.addWidget(text)
            
            btn = QPushButton("关闭")
            btn.clicked.connect(dialog.accept)
            layout.addWidget(btn)
            
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"显示模块列表时出错:\n{str(e)}")
    
    def close_module_tab(self, index: int):
        """关闭模块标签页"""
        if index == 0:
            self.main_tabs.setCurrentIndex(0)
            return

        widget = self.main_tabs.widget(index)
        if widget and hasattr(widget, 'close'):
            try:
                widget.close()
            except Exception:
                pass

        self.main_tabs.removeTab(index)
    
    def show_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于 策略研究",
            "策略研究平台\n\n"
            "基于 PyQt6 开发\n\n"
            "功能:\n"
            "• 智能选股\n"
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
