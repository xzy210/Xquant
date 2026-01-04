# stock_list_widget.py - 股票列表组件
"""
股票列表选择组件，支持搜索和过滤，以及自选股分组切换
"""
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QPushButton,
    QComboBox, QGroupBox, QMenu, QInputDialog, QMessageBox,
    QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
import re


class StockListWidget(QWidget):
    """股票列表组件"""
    
    # 信号：选中股票变化
    stockSelected = pyqtSignal(str, str)  # code, name
    # 信号：自选股分组变化
    groupChanged = pyqtSignal(str)  # group_name, empty string means all stocks
    # 信号：显示列表变化
    displayListChanged = pyqtSignal(list)
    # 信号：请求刷新策略分组
    refreshRequested = pyqtSignal(str)  # strategy_name
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.stock_list: List[str] = []  # 所有股票代码
        self.name_map: Dict[str, str] = {}  # 代码->名称映射
        self.filtered_list: List[str] = []  # 过滤后的列表
        self.current_display_list: List[str] = []  # 当前显示的列表（可能是全部或分组）
        
        self.watchlist_manager = None  # Will be set from MainWindow
        self.current_group = ""  # Empty means all stocks
        
        self.setupUI()
    
    def setupUI(self):
        """设置界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # Group selector row
        group_layout = QHBoxLayout()
        
        self.group_combo = QComboBox()
        self.group_combo.setStyleSheet("""
            QComboBox {
                padding: 6px;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                background-color: #2d2d2d;
                color: #ffffff;
                font-size: 13px;
                min-width: 120px;
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
        """)
        self.group_combo.addItem("📋 全部股票", "")
        self.group_combo.currentIndexChanged.connect(self.on_group_combo_changed)
        group_layout.addWidget(self.group_combo, stretch=1)
        
        # Refresh button for strategy groups
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.setToolTip("刷新策略分组")
        self.refresh_btn.setVisible(False)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-family: "Segoe UI Emoji", "Segoe UI Symbol";
                font-size: 14px;
                padding: 0;
            }
            QPushButton:hover {
                background-color: #0086ed;
            }
        """)
        self.refresh_btn.clicked.connect(self.on_refresh_clicked)
        group_layout.addWidget(self.refresh_btn)
        
        # Group management button
        self.group_menu_btn = QPushButton("⚙")
        self.group_menu_btn.setFixedSize(28, 28)
        self.group_menu_btn.setToolTip("分组管理")
        self.group_menu_btn.setStyleSheet("""
            QPushButton {
                background-color: #3c3c3c;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-family: "Segoe UI Emoji", "Segoe UI Symbol";
                font-size: 14px;
                padding: 0;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        self.group_menu_btn.clicked.connect(self.show_group_menu)
        group_layout.addWidget(self.group_menu_btn)
        
        layout.addLayout(group_layout)
        
        # 搜索框
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索股票代码或名称...")
        self.search_input.textChanged.connect(self.on_search_changed)
        self.search_input.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                background-color: #2d2d2d;
                color: #ffffff;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #0078d4;
            }
        """)
        search_layout.addWidget(self.search_input)
        
        # 清除搜索按钮
        self.clear_btn = QPushButton("✕")
        self.clear_btn.setFixedSize(28, 28)
        self.clear_btn.clicked.connect(self.clear_search)
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #3c3c3c;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-family: "Segoe UI Emoji", "Segoe UI Symbol";
                font-size: 14px;
                padding: 0;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        search_layout.addWidget(self.clear_btn)
        
        layout.addLayout(search_layout)
        
        # 统计信息
        self.info_label = QLabel("共 0 只股票")
        self.info_label.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 11px;
                padding: 2px;
            }
        """)
        layout.addWidget(self.info_label)
        
        # 股票列表
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #2d2d2d;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                color: #ffffff;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 8px 10px;
                border-bottom: 1px solid #3c3c3c;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
            }
            QListWidget::item:hover {
                background-color: #3c3c3c;
            }
        """)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)
        
        # Enable context menu
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        
        layout.addWidget(self.list_widget)
    
    def set_watchlist_manager(self, manager):
        """设置自选股管理器"""
        self.watchlist_manager = manager
        self.update_group_combo()
    
    def update_group_combo(self):
        """更新分组下拉框"""
        if not self.watchlist_manager:
            return
            
        current_data = self.group_combo.currentData()
        
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("📋 全部股票", "")
        
        groups = self.watchlist_manager.get_all_groups()
        for group in groups:
            # 避免重复添加全部股票（如果 manager 中也存在）
            if group in ("全部股票", ""):
                continue
            # 只显示策略分组（以"策略:"开头的受保护分组），不显示自选分组
            if self.watchlist_manager.is_protected_group(group) and group.startswith("策略:"):
                self.group_combo.addItem(f"💼 {group}", group)
        
        # Restore selection
        if current_data is not None:
            index = self.group_combo.findData(current_data)
            if index >= 0:
                self.group_combo.setCurrentIndex(index)
            else:
                self.group_combo.setCurrentIndex(0)
        
        self.group_combo.blockSignals(False)
    
    def on_group_combo_changed(self, index):
        """处理分组切换"""
        group_name = self.group_combo.currentData()
        self.current_group = group_name or ""
        
        # 显示/隐藏刷新按钮 (仅针对以“策略: ”开头的分组)
        is_strategy_group = self.current_group.startswith("策略:")
        self.refresh_btn.setVisible(is_strategy_group)
        
        if not group_name:
            # Show all stocks
            self.current_display_list = self.stock_list.copy()
        else:
            # Show group stocks
            if self.watchlist_manager:
                self.current_display_list = self.watchlist_manager.get_group_stocks(group_name)
            else:
                self.current_display_list = []
        
        # Reset search and update display
        self.search_input.clear()
        self.filtered_list = self.current_display_list.copy()
        self.update_list_widget()
        self.update_info_label()
        
        self.groupChanged.emit(self.current_group)
    
    def on_refresh_clicked(self):
        """处理刷新按钮点击"""
        if self.current_group.startswith("策略:"):
            strategy_name = self.current_group.replace("策略:", "").strip()
            self.refresh_Requested_name = strategy_name # Keep it for reference if needed
            self.refreshRequested.emit(strategy_name)
    
    def show_group_menu(self):
        """显示分组管理菜单"""
        menu = QMenu(self)
        
        # Create new group
        new_action = menu.addAction("✚ 新建分组")
        new_action.triggered.connect(self.create_new_group)
        
        # Import stocks to current group (only if a group is selected and not protected)
        if self.current_group:
            is_protected = self._is_current_group_protected()
            
            menu.addSeparator()
            
            if not is_protected:
                import_action = menu.addAction("📥 导入股票到当前分组")
                import_action.triggered.connect(self.import_stocks_to_group)
                
                rename_action = menu.addAction("✏ 重命名当前分组")
                rename_action.triggered.connect(self.rename_current_group)
                
                delete_action = menu.addAction("🗑 删除当前分组")
                delete_action.triggered.connect(self.delete_current_group)
            else:
                # 显示提示信息
                info_action = menu.addAction("ℹ️ 此分组由系统自动管理")
                info_action.setEnabled(False)
        
        menu.exec(self.group_menu_btn.mapToGlobal(self.group_menu_btn.rect().bottomLeft()))
    
    def _is_current_group_protected(self) -> bool:
        """检查当前分组是否受保护"""
        if not self.watchlist_manager or not self.current_group:
            return False
        return self.watchlist_manager.is_protected_group(self.current_group)
    
    def create_new_group(self):
        """创建新分组"""
        if not self.watchlist_manager:
            return
            
        name, ok = QInputDialog.getText(self, "新建分组", "请输入分组名称:")
        if ok and name:
            success, msg = self.watchlist_manager.create_group(name)
            if success:
                self.update_group_combo()
                # Select the new group
                index = self.group_combo.findData(name)
                if index >= 0:
                    self.group_combo.setCurrentIndex(index)
            else:
                QMessageBox.warning(self, "错误", msg)
    
    def import_stocks_to_group(self):
        """导入股票到当前分组"""
        if not self.watchlist_manager or not self.current_group:
            return
            
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入股票列表", "", 
            "Text Files (*.txt *.csv);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                # Extract stock codes
                codes = re.findall(r'[0-9]{6}\.[A-Z]{2}|[0-9]{6}', content)
                
                valid_codes = []
                known_codes = set(self.name_map.keys())
                
                for code in codes:
                    if code in known_codes:
                        valid_codes.append(code)
                    elif len(code) == 6:
                        for suffix in ['.SZ', '.SH', '.BJ']:
                            full_code = code + suffix
                            if full_code in known_codes:
                                valid_codes.append(full_code)
                                break
                        else:
                            valid_codes.append(code)
                    else:
                        valid_codes.append(code)
                        
                valid_codes = list(set(valid_codes))
                        
                if valid_codes:
                    success, msg, count = self.watchlist_manager.import_stocks(
                        self.current_group, valid_codes
                    )
                    if success:
                        QMessageBox.information(self, "成功", msg)
                        self.on_group_combo_changed(self.group_combo.currentIndex())
                    else:
                        QMessageBox.warning(self, "错误", msg)
                else:
                    QMessageBox.warning(self, "提示", "未找到有效的股票代码")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"读取文件失败: {e}")
    
    def rename_current_group(self):
        """重命名当前分组"""
        if not self.watchlist_manager or not self.current_group:
            return
            
        new_name, ok = QInputDialog.getText(
            self, "重命名分组", "请输入新名称:", 
            text=self.current_group
        )
        if ok and new_name and new_name != self.current_group:
            # Rename by creating new, copying stocks, deleting old
            stocks = self.watchlist_manager.get_group_stocks(self.current_group)
            self.watchlist_manager.create_group(new_name)
            self.watchlist_manager.import_stocks(new_name, stocks)
            self.watchlist_manager.delete_group(self.current_group)
            
            self.update_group_combo()
            index = self.group_combo.findData(new_name)
            if index >= 0:
                self.group_combo.setCurrentIndex(index)
    
    def delete_current_group(self):
        """删除当前分组"""
        if not self.watchlist_manager or not self.current_group:
            return
            
        reply = QMessageBox.question(
            self, "确认删除", 
            f"确定要删除分组 '{self.current_group}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            success, msg = self.watchlist_manager.delete_group(self.current_group)
            if success:
                self.update_group_combo()
                self.group_combo.setCurrentIndex(0)  # Back to all stocks
            else:
                QMessageBox.warning(self, "错误", msg)
    
    def add_stock_to_current_group(self, code: str):
        """添加股票到当前分组"""
        if not self.watchlist_manager or not self.current_group:
            return False, "未选择分组"
        
        # 检查是否是受保护的分组
        if self._is_current_group_protected():
            return False, f"分组 '{self.current_group}' 是系统分组，不支持手动添加股票"
            
        success, msg = self.watchlist_manager.add_to_group(self.current_group, code)
        if success:
            self.on_group_combo_changed(self.group_combo.currentIndex())
        return success, msg
    
    def remove_stock_from_current_group(self, code: str):
        """从当前分组移除股票"""
        if not self.watchlist_manager or not self.current_group:
            return False, "未选择分组"
        
        # 检查是否是受保护的分组
        if self._is_current_group_protected():
            return False, f"分组 '{self.current_group}' 是系统分组，不支持手动移除股票"
            
        success, msg = self.watchlist_manager.remove_from_group(self.current_group, code)
        if success:
            self.on_group_combo_changed(self.group_combo.currentIndex())
        return success, msg
    
    def set_stock_list(self, stocks: List[str], name_map: Dict[str, str] = None):
        """
        设置股票列表
        
        Args:
            stocks: 股票代码列表
            name_map: 代码到名称的映射字典
        """
        self.stock_list = stocks
        if name_map:
            self.name_map = name_map
        
        # If showing all stocks, update display list
        if not self.current_group:
            self.current_display_list = stocks.copy()
            self.filtered_list = stocks.copy()
        
        self.update_list_widget()
        self.update_info_label()
    
    def update_list_widget(self):
        """更新列表显示"""
        self.list_widget.clear()
        
        for code in self.filtered_list:
            name = self.name_map.get(code, "")
            display_text = f"{code}  {name}" if name else code
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, code)  # 存储股票代码
            self.list_widget.addItem(item)
            
        # 发送显示列表变化信号
        self.displayListChanged.emit(self.filtered_list)
    
    def update_info_label(self):
        """更新统计信息"""
        total = len(self.current_display_list)
        filtered = len(self.filtered_list)
        
        group_prefix = f"[{self.current_group}] " if self.current_group else ""
        
        if total == filtered:
            self.info_label.setText(f"{group_prefix}共 {total} 只股票")
        else:
            self.info_label.setText(f"{group_prefix}显示 {filtered} / {total} 只股票")
    
    def on_search_changed(self, text: str):
        """处理搜索文本变化"""
        text = text.strip().lower()
        
        if not text:
            self.filtered_list = self.current_display_list.copy()
        else:
            self.filtered_list = []
            for code in self.current_display_list:
                name = self.name_map.get(code, "").lower()
                if text in code.lower() or text in name:
                    self.filtered_list.append(code)
        
        self.update_list_widget()
        self.update_info_label()
    
    def clear_search(self):
        """清除搜索"""
        self.search_input.clear()
    
    def on_item_clicked(self, item: QListWidgetItem):
        """处理列表项点击"""
        code = item.data(Qt.ItemDataRole.UserRole)
        name = self.name_map.get(code, "")
        self.stockSelected.emit(code, name)
    
    def on_item_double_clicked(self, item: QListWidgetItem):
        """处理列表项双击"""
        code = item.data(Qt.ItemDataRole.UserRole)
        name = self.name_map.get(code, "")
        self.stockSelected.emit(code, name)
    
    def get_selected_stock(self) -> Optional[str]:
        """获取当前选中的股票代码"""
        current = self.list_widget.currentItem()
        if current:
            return current.data(Qt.ItemDataRole.UserRole)
        return None
    
    def select_stock(self, code: str):
        """
        选中指定的股票
        
        Args:
            code: 股票代码
        """
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == code:
                self.list_widget.setCurrentItem(item)
                self.list_widget.scrollToItem(item)
                break
    
    def select_next(self):
        """选中下一只股票"""
        current_row = self.list_widget.currentRow()
        if current_row < self.list_widget.count() - 1:
            self.list_widget.setCurrentRow(current_row + 1)
            item = self.list_widget.currentItem()
            if item:
                code = item.data(Qt.ItemDataRole.UserRole)
                name = self.name_map.get(code, "")
                self.stockSelected.emit(code, name)
    
    def select_previous(self):
        """选中上一只股票"""
        current_row = self.list_widget.currentRow()
        if current_row > 0:
            self.list_widget.setCurrentRow(current_row - 1)
            item = self.list_widget.currentItem()
            if item:
                code = item.data(Qt.ItemDataRole.UserRole)
                name = self.name_map.get(code, "")
                self.stockSelected.emit(code, name)
    
    def is_showing_group(self) -> bool:
        """是否正在显示自选股分组"""
        return bool(self.current_group)
    
    def get_current_group(self) -> str:
        """获取当前显示的分组名"""
        return self.current_group
