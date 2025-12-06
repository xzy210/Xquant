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
    QInputDialog, QDialog, QDialogButtonBox
)
from PyQt6.QtCore import Qt, QDate, QSize
from PyQt6.QtGui import QAction, QKeySequence, QShortcut

# 本地模块
from widgets.kline_widget import KLineWidget
from widgets.stock_list_widget import StockListWidget
from widgets.update_dialog import UpdateDialog
from watchlist_manager import WatchlistManager
from data_loader import load_stock_data, get_stock_list, load_stock_name_map
from indicators import attach_all_indicators
from data_updater import DataUpdateThread


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
        
        # 设置深色主题
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
            }
            QWidget {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QMenuBar {
                background-color: #2d2d2d;
                color: #ffffff;
                padding: 2px;
            }
            QMenuBar::item:selected {
                background-color: #3c3c3c;
            }
            QMenu {
                background-color: #2d2d2d;
                color: #ffffff;
                border: 1px solid #3c3c3c;
            }
            QMenu::item:selected {
                background-color: #0078d4;
            }
            QToolBar {
                background-color: #2d2d2d;
                border: none;
                spacing: 5px;
                padding: 5px;
            }
            QStatusBar {
                background-color: #007acc;
                color: #ffffff;
            }
            QGroupBox {
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QCheckBox {
                color: #ffffff;
                spacing: 5px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
            QComboBox {
                background-color: #2d2d2d;
                color: #ffffff;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 5px;
                min-width: 100px;
            }
            QComboBox:hover {
                border-color: #0078d4;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #2d2d2d;
                color: #ffffff;
                selection-background-color: #0078d4;
            }
            QPushButton {
                background-color: #0078d4;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1a8cdb;
            }
            QPushButton:pressed {
                background-color: #005a9e;
            }
            QDateEdit {
                background-color: #2d2d2d;
                color: #ffffff;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 5px;
            }
            QSplitter::handle {
                background-color: #3c3c3c;
            }
            QSplitter::handle:horizontal {
                width: 2px;
            }
            QSplitter::handle:vertical {
                height: 2px;
            }
        """)
        
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
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
        self.start_date_edit.setDate(QDate(2023, 1, 1))
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

    def start_data_update(self, token, full_update, exclude_boards):
        """开始数据更新"""
        if self.update_thread and self.update_thread.isRunning():
            return

        self.update_thread = DataUpdateThread(
            self.data_dir,
            self.stocklist_path,
            token,
            full_update,
            exclude_boards
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
            # 重新加载股票列表和当前图表
            self.load_stock_list()
            self.load_and_display_chart()

    def show_stock_list_context_menu(self, position):
        """显示股票列表右键菜单"""
        menu = QMenu()
        
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

    def start_single_stock_update(self, code, full_update=False, start_date=None):
        """启动单只股票更新"""
        default_token = os.environ.get("TUSHARE_TOKEN", "")
        self.update_dialog = UpdateDialog(self, default_token)
        self.update_dialog.setWindowTitle(f"更新股票 {code}")
        
        # Hide options that don't apply
        self.update_dialog.full_update_cb.setVisible(False)
        # Hide exclude group (it's in a layout, so we need to find the layout or widget)
        # The exclude options are in a QVBoxLayout inside the main layout.
        # Let's just hide the checkboxes directly
        self.update_dialog.exclude_gem_cb.setVisible(False)
        self.update_dialog.exclude_star_cb.setVisible(False)
        self.update_dialog.exclude_bj_cb.setVisible(False)
        # Also hide the label "排除板块:" if possible, but it's just a label added to layout.
        # It's fine if it stays, or we can traverse layout to hide it.
        
        # Connect dialog signals to a custom handler
        # We need to disconnect the original connection first if we want to override behavior completely
        try:
            self.update_dialog.start_update.disconnect()
        except:
            pass
            
        self.update_dialog.start_update.connect(
            lambda t, f, e: self.run_single_stock_thread(code, t, full_update, start_date)
        )
        
        self.update_dialog.exec()

    def run_single_stock_thread(self, code, token, full_update, start_date):
        """运行单只股票更新线程"""
        if self.update_thread and self.update_thread.isRunning():
            return

        self.update_thread = DataUpdateThread(
            self.data_dir,
            self.stocklist_path,
            token,
            full_update=full_update,
            codes=[code],
            start_date=start_date
        )
        self.update_thread.progress_updated.connect(self.update_dialog.update_progress)
        self.update_thread.log_message.connect(self.update_dialog.append_log)
        self.update_thread.finished_signal.connect(self.on_update_finished)
        self.update_thread.start()
