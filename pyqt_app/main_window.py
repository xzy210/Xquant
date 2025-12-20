# main_window.py - 主窗口
"""
股票K线图查看器主窗口
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
    QProgressDialog
)
from PyQt6.QtCore import Qt, QDate, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QShortcut

# 本地模块
from widgets.kline_widget import KLineWidget
from widgets.stock_list_widget import StockListWidget
from widgets.trading_simulator_widget import TradingSimulatorWidget
from widgets.stock_screener_widget import StockScreenerWidget
from widgets.ai_trading_widget import AITradingWidget
from widgets.update_dialog import UpdateDialog
from watchlist_manager import WatchlistManager
from data_loader import load_stock_data, get_stock_list, load_stock_name_map, get_stock_cache
from indicators import attach_all_indicators
from data_updater import DataUpdateThread


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
        self.preload_dialog = None
        self._updating_codes = []  # 记录正在更新的股票代码
        
        # 启动数据预加载
        self.start_data_preload()
    
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
        self.setWindowTitle("股票K线图查看器")
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
        
        # 股票列表（集成自选股分组功能）
        self.stock_list_widget = StockListWidget()
        self.stock_list_widget.stockSelected.connect(self.on_stock_selected)
        self.stock_list_widget.set_watchlist_manager(self.watchlist_manager)
        # 添加右键菜单
        self.stock_list_widget.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.stock_list_widget.list_widget.customContextMenuRequested.connect(self.show_stock_list_context_menu)
        
        left_layout.addWidget(self.stock_list_widget, stretch=1)
        
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
        
        # 右侧面板（K线图）
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        self.kline_widget = KLineWidget()
        right_layout.addWidget(self.kline_widget)
        
        splitter.addWidget(right_panel)
        
        # 设置分割比例
        splitter.setSizes([150, 1050])
        
        # 状态栏
        self.statusBar().showMessage("就绪")
        
        # 模拟器窗口列表，防止被垃圾回收
        self.simulator_windows = []
        self.screener_windows = []
        self.ai_windows = []
    
    def setup_menu(self):
        """设置菜单栏"""
        menubar = self.menuBar()
        
        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        
        refresh_action = QAction("刷新(&R)", self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.triggered.connect(self.refresh_chart)
        file_menu.addAction(refresh_action)
        
        update_action = QAction("更新数据(&U)", self)
        update_action.triggered.connect(self.show_update_dialog)
        file_menu.addAction(update_action)
        
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
        prev_btn.clicked.connect(self.stock_list_widget.select_previous)
        toolbar.addWidget(prev_btn)
        
        next_btn = QPushButton("下一只 ▶")
        next_btn.clicked.connect(self.stock_list_widget.select_next)
        toolbar.addWidget(next_btn)
    
    def setup_shortcuts(self):
        """设置快捷键"""
        # 上下键切换股票
        QShortcut(Qt.Key.Key_Up, self, self.stock_list_widget.select_previous)
        QShortcut(Qt.Key.Key_Down, self, self.stock_list_widget.select_next)
        
        # F5 刷新
        QShortcut(Qt.Key.Key_F5, self, self.refresh_chart)
    
    def load_stock_list(self):
        """加载股票列表"""
        self.statusBar().showMessage("正在加载股票列表...")
        QApplication.processEvents()
        
        # 加载股票代码列表
        self.stock_list = get_stock_list(self.data_dir)
        
        # 加载名称映射
        self.name_map = load_stock_name_map(self.stocklist_path)
        
        # 更新列表组件
        self.stock_list_widget.set_stock_list(self.stock_list, self.name_map)
        
        # 更新自选股分组下拉框
        self.stock_list_widget.update_group_combo()
        
        self.statusBar().showMessage(f"已加载 {len(self.stock_list)} 只股票")
        
        # 默认选中第一只
        if self.stock_list:
            first_code = self.stock_list[0]
            self.stock_list_widget.select_stock(first_code)
            self.on_stock_selected(first_code, self.name_map.get(first_code, ""))
    
    def start_data_preload(self):
        """启动数据预加载"""
        if not self.stock_list:
            return
        
        # 创建进度对话框
        self.preload_dialog = QProgressDialog(
            "正在预加载股票数据，请稍候...",
            "后台运行",
            0,
            len(self.stock_list),
            self
        )
        self.preload_dialog.setWindowTitle("数据预加载")
        self.preload_dialog.setWindowModality(Qt.WindowModality.NonModal)  # 非模态，允许用户操作
        self.preload_dialog.setMinimumDuration(0)  # 立即显示
        self.preload_dialog.setAutoClose(True)
        self.preload_dialog.setAutoReset(False)
        
        # "后台运行"按钮点击时隐藏对话框但继续加载
        self.preload_dialog.canceled.connect(self.hide_preload_dialog)
        
        # 创建并启动预加载线程
        self.preload_thread = DataPreloadThread(self.data_dir, self.stock_list)
        self.preload_thread.progress_updated.connect(self.on_preload_progress)
        self.preload_thread.finished_signal.connect(self.on_preload_finished)
        self.preload_thread.start()
    
    def hide_preload_dialog(self):
        """隐藏预加载对话框（继续后台加载）"""
        if self.preload_dialog:
            self.preload_dialog.hide()
            self.statusBar().showMessage("数据正在后台预加载中...")
    
    def on_preload_progress(self, current: int, total: int, code: str):
        """更新预加载进度"""
        if self.preload_dialog and self.preload_dialog.isVisible():
            self.preload_dialog.setValue(current)
            self.preload_dialog.setLabelText(f"正在加载: {code}\n({current}/{total})")
    
    def on_preload_finished(self, success: bool, loaded_count: int, message: str):
        """预加载完成回调"""
        if self.preload_dialog:
            self.preload_dialog.close()
            self.preload_dialog = None
        
        if success:
            self.statusBar().showMessage(f"✓ {message}")
        else:
            self.statusBar().showMessage(f"⚠ {message}")
        
        # 刷新当前图表（使用缓存数据）
        if success and self.current_code:
            self.load_and_display_chart()
    
    def on_stock_selected(self, code: str, name: str):
        """处理股票选择"""
        self.current_code = code
        self.current_name = name
        
        self.load_and_display_chart()
    
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
        
        # 更新K线图
        self.kline_widget.set_indicators(
            show_volume=self.volume_checkbox.isChecked(),
            show_macd=self.macd_checkbox.isChecked(),
            show_kdj=self.kdj_checkbox.isChecked()
        )
        self.kline_widget.set_ma_windows(self.ma_windows)
        self.kline_widget.set_data(df, self.current_code, self.current_name)
        
        # 更新窗口标题
        self.setWindowTitle(f"股票K线图查看器 - {self.current_code} {self.current_name}")
        
        self.statusBar().showMessage(
            f"{self.current_code} {self.current_name} | "
            f"数据范围: {df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')} | "
            f"共 {len(df)} 根K线"
        )
    
    def on_indicator_changed(self, state):
        """处理指标复选框变化"""
        # 同步菜单状态
        self.volume_action.setChecked(self.volume_checkbox.isChecked())
        self.macd_action.setChecked(self.macd_checkbox.isChecked())
        self.kdj_action.setChecked(self.kdj_checkbox.isChecked())
        
        # 重新加载图表
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
            self.load_and_display_chart()
    
    def refresh_chart(self):
        """刷新图表"""
        self.load_and_display_chart()
    
    def show_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于",
            "股票K线图查看器\n\n"
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

    def show_update_dialog(self):
        """显示数据更新对话框"""
        default_token = os.environ.get("TUSHARE_TOKEN", "")
        self.update_dialog = UpdateDialog(self, default_token)
        self.update_dialog.start_update.connect(self.start_data_update)
        self.update_dialog.stop_update.connect(self.stop_data_update)
        self.update_dialog.exec()

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
        menu = QMenu()
        
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
        
        inc_update_action = update_menu.addAction("增量更新 (补齐数据)")
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
            else:
                QMessageBox.warning(self, "错误", msg)

    def create_group_and_add(self):
        """新建分组并添加当前股票"""
        name, ok = QInputDialog.getText(self, "新建分组", "请输入分组名称:")
        if ok and name:
            success, msg = self.watchlist_manager.create_group(name)
            if success:
                self.stock_list_widget.update_group_combo()
                self.add_current_to_watchlist(name)
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
        
        screener_widget = StockScreenerWidget(self.data_dir)
        screener_widget.stockSelected.connect(self.on_screener_stock_selected)
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

    def on_screener_stock_selected(self, code):
        """处理选股结果点击"""
        # 在主窗口选中该股票
        self.stock_list_widget.select_stock(code)
        # 激活主窗口
        self.activateWindow()
        self.raise_()

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
