# etf_list_widget.py - ETF列表组件
"""
ETF列表选择组件，支持搜索、分类过滤、自选分组
"""
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QPushButton,
    QComboBox, QMenu, QInputDialog, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
import re


class ETFListWidget(QWidget):
    """ETF列表组件"""
    
    # Signal: selected ETF changed
    etfSelected = pyqtSignal(str, str)  # code, name
    # Signal: display list changed
    displayListChanged = pyqtSignal(list)
    # Signal: watchlist group changed
    groupChanged = pyqtSignal(str)  # group_name, empty string means all/category mode
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.etf_list: List[str] = []  # All ETF codes
        self.name_map: Dict[str, str] = {}  # Code -> name mapping
        self.categories: List[Dict] = []  # Category info
        self.filtered_list: List[str] = []  # Filtered list
        self.current_category: str = ""  # Current category filter
        self.current_display_list: List[str] = []  # Current display list
        
        # 自选股相关
        self.watchlist_manager = None  # Will be set from MainWindow
        self.current_group: str = ""  # Empty means category/all mode
        self.is_watchlist_mode: bool = False  # True when viewing a watchlist group
        
        self.setupUI()
    
    def setupUI(self):
        """Setup UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        combo_style = """
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
        """
        
        # Watchlist group selector row
        group_layout = QHBoxLayout()
        
        self.group_combo = QComboBox()
        self.group_combo.setStyleSheet(combo_style)
        self.group_combo.addItem("📋 按分类浏览", "")
        self.group_combo.currentIndexChanged.connect(self.on_group_combo_changed)
        group_layout.addWidget(self.group_combo, stretch=1)
        
        # Group management button
        self.group_menu_btn = QPushButton("⚙")
        self.group_menu_btn.setFixedSize(28, 28)
        self.group_menu_btn.setToolTip("自选分组管理")
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
        
        # Category selector (shown when not in watchlist mode)
        category_layout = QHBoxLayout()
        
        self.category_combo = QComboBox()
        self.category_combo.setStyleSheet(combo_style)
        self.category_combo.addItem("📋 全部ETF", "")
        self.category_combo.currentIndexChanged.connect(self.on_category_changed)
        category_layout.addWidget(self.category_combo, stretch=1)
        
        layout.addLayout(category_layout)
        
        # Search box
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索ETF代码或名称...")
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
        
        # Clear search button
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
        
        # Stats info
        self.info_label = QLabel("共 0 只ETF")
        self.info_label.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 11px;
                padding: 2px;
            }
        """)
        layout.addWidget(self.info_label)
        
        # ETF list
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
    
    def set_etf_data(self, etf_list: List[str], name_map: Dict[str, str] = None, 
                     categories: List[Dict] = None):
        """
        Set ETF data
        
        Args:
            etf_list: List of ETF codes
            name_map: Code to name mapping
            categories: Category info list
        """
        self.etf_list = etf_list
        if name_map:
            self.name_map = name_map
        if categories:
            self.categories = categories
            self.update_category_combo()
        
        self.filtered_list = etf_list.copy()
        self.update_list_widget()
        self.update_info_label()
    
    def update_category_combo(self):
        """Update category dropdown"""
        current_data = self.category_combo.currentData()
        
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("📋 全部ETF", "")
        
        for category in self.categories:
            cat_name = category.get("name", "")
            if cat_name:
                self.category_combo.addItem(f"📁 {cat_name}", cat_name)
        
        # Restore selection
        if current_data is not None:
            index = self.category_combo.findData(current_data)
            if index >= 0:
                self.category_combo.setCurrentIndex(index)
            else:
                self.category_combo.setCurrentIndex(0)
        
        self.category_combo.blockSignals(False)
    
    def on_category_changed(self, index):
        """Handle category switch"""
        category_name = self.category_combo.currentData()
        self.current_category = category_name or ""
        
        if not category_name:
            # Show all ETFs
            base_list = self.etf_list.copy()
        else:
            # Show category ETFs
            base_list = []
            for category in self.categories:
                if category.get("name") == category_name:
                    for etf in category.get("etfs", []):
                        code = etf.get("code", "")
                        if code and code in self.etf_list:
                            base_list.append(code)
                    break
        
        # Apply search filter
        search_text = self.search_input.text().strip().lower()
        if search_text:
            self.filtered_list = []
            for code in base_list:
                name = self.name_map.get(code, "").lower()
                if search_text in code.lower() or search_text in name:
                    self.filtered_list.append(code)
        else:
            self.filtered_list = base_list
        
        self.update_list_widget()
        self.update_info_label()
    
    def on_search_changed(self, text: str):
        """Handle search text change"""
        text = text.strip().lower()
        
        # Get base list from category
        if not self.current_category:
            base_list = self.etf_list.copy()
        else:
            base_list = []
            for category in self.categories:
                if category.get("name") == self.current_category:
                    for etf in category.get("etfs", []):
                        code = etf.get("code", "")
                        if code and code in self.etf_list:
                            base_list.append(code)
                    break
        
        if not text:
            self.filtered_list = base_list
        else:
            self.filtered_list = []
            for code in base_list:
                name = self.name_map.get(code, "").lower()
                if text in code.lower() or text in name:
                    self.filtered_list.append(code)
        
        self.update_list_widget()
        self.update_info_label()
    
    def update_list_widget(self):
        """Update list display"""
        self.list_widget.clear()
        
        for code in self.filtered_list:
            name = self.name_map.get(code, "")
            display_text = f"{code}  {name}" if name else code
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, code)
            self.list_widget.addItem(item)
        
        self.displayListChanged.emit(self.filtered_list)
    
    def update_info_label(self):
        """Update stats info"""
        if self.is_watchlist_mode:
            # Watchlist mode: show group name
            total = len(self.current_display_list)
            filtered = len(self.filtered_list)
            prefix = f"[⭐{self.current_group}] " if self.current_group else ""
        else:
            # Category mode
            total = len(self.etf_list)
            filtered = len(self.filtered_list)
            prefix = f"[{self.current_category}] " if self.current_category else ""
        
        if total == filtered:
            self.info_label.setText(f"{prefix}共 {total} 只ETF")
        else:
            self.info_label.setText(f"{prefix}显示 {filtered} / {total} 只ETF")
    
    def clear_search(self):
        """Clear search"""
        self.search_input.clear()
    
    def on_item_clicked(self, item: QListWidgetItem):
        """Handle list item click"""
        code = item.data(Qt.ItemDataRole.UserRole)
        name = self.name_map.get(code, "")
        self.etfSelected.emit(code, name)
    
    def on_item_double_clicked(self, item: QListWidgetItem):
        """Handle list item double click"""
        code = item.data(Qt.ItemDataRole.UserRole)
        name = self.name_map.get(code, "")
        self.etfSelected.emit(code, name)
    
    def get_selected_etf(self) -> Optional[str]:
        """Get currently selected ETF code"""
        current = self.list_widget.currentItem()
        if current:
            return current.data(Qt.ItemDataRole.UserRole)
        return None
    
    def select_etf(self, code: str):
        """Select specified ETF"""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == code:
                self.list_widget.setCurrentItem(item)
                self.list_widget.scrollToItem(item)
                break
    
    def select_next(self):
        """Select next ETF"""
        current_row = self.list_widget.currentRow()
        if current_row < self.list_widget.count() - 1:
            self.list_widget.setCurrentRow(current_row + 1)
            item = self.list_widget.currentItem()
            if item:
                code = item.data(Qt.ItemDataRole.UserRole)
                name = self.name_map.get(code, "")
                self.etfSelected.emit(code, name)
    
    def select_previous(self):
        """Select previous ETF"""
        current_row = self.list_widget.currentRow()
        if current_row > 0:
            self.list_widget.setCurrentRow(current_row - 1)
            item = self.list_widget.currentItem()
            if item:
                code = item.data(Qt.ItemDataRole.UserRole)
                name = self.name_map.get(code, "")
                self.etfSelected.emit(code, name)
    
    # ==================== 自选分组相关方法 ====================
    
    def set_watchlist_manager(self, manager):
        """设置自选股管理器"""
        self.watchlist_manager = manager
        self.update_group_combo()
    
    def update_group_combo(self):
        """更新自选分组下拉框"""
        if not self.watchlist_manager:
            return
            
        current_data = self.group_combo.currentData()
        
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("📋 按分类浏览", "")
        
        groups = self.watchlist_manager.get_all_groups()
        for group in groups:
            if group in ("全部股票", ""):
                continue
            self.group_combo.addItem(f"⭐ {group}", group)
        
        # Restore selection
        if current_data is not None:
            index = self.group_combo.findData(current_data)
            if index >= 0:
                self.group_combo.setCurrentIndex(index)
            else:
                self.group_combo.setCurrentIndex(0)
        
        self.group_combo.blockSignals(False)
    
    def on_group_combo_changed(self, index):
        """处理自选分组切换"""
        group_name = self.group_combo.currentData()
        self.current_group = group_name or ""
        self.is_watchlist_mode = bool(group_name)
        
        # Show/hide category selector based on mode
        self.category_combo.setVisible(not self.is_watchlist_mode)
        
        if not group_name:
            # Back to category/all mode
            self.current_display_list = self.etf_list.copy()
            # Re-apply category filter
            self.on_category_changed(self.category_combo.currentIndex())
        else:
            # Show watchlist group ETFs
            if self.watchlist_manager:
                group_codes = self.watchlist_manager.get_group_stocks(group_name)
                # Filter to only include valid ETF codes
                self.current_display_list = [c for c in group_codes if c in self.etf_list or c in self.name_map]
            else:
                self.current_display_list = []
            
            # Reset search and update display
            self.search_input.clear()
            self.filtered_list = self.current_display_list.copy()
            self.update_list_widget()
            self.update_info_label()
        
        self.groupChanged.emit(self.current_group)
    
    def show_group_menu(self):
        """显示分组管理菜单"""
        menu = QMenu(self)
        
        # Create new group
        new_action = menu.addAction("✚ 新建自选分组")
        new_action.triggered.connect(self.create_new_group)
        
        # Only show these options if a watchlist group is selected
        if self.is_watchlist_mode and self.current_group:
            menu.addSeparator()
            import_action = menu.addAction("📥 导入ETF到当前分组")
            import_action.triggered.connect(self.import_etfs_to_group)
            
            rename_action = menu.addAction("✏ 重命名当前分组")
            rename_action.triggered.connect(self.rename_current_group)
            
            delete_action = menu.addAction("🗑 删除当前分组")
            delete_action.triggered.connect(self.delete_current_group)
        
        menu.exec(self.group_menu_btn.mapToGlobal(self.group_menu_btn.rect().bottomLeft()))
    
    def create_new_group(self):
        """创建新自选分组"""
        if not self.watchlist_manager:
            QMessageBox.warning(self, "错误", "自选股管理器未初始化")
            return
            
        name, ok = QInputDialog.getText(self, "新建自选分组", "请输入分组名称:")
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
    
    def import_etfs_to_group(self):
        """导入ETF到当前分组"""
        if not self.watchlist_manager or not self.current_group:
            return
            
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入ETF列表", "", 
            "Text Files (*.txt *.csv);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                # Extract ETF codes (6 digits)
                codes = re.findall(r'[0-9]{6}', content)
                codes = list(set(codes))
                
                # Filter valid ETF codes
                valid_codes = [c for c in codes if c in self.etf_list or c in self.name_map]
                        
                if valid_codes:
                    success, msg, count = self.watchlist_manager.import_stocks(
                        self.current_group, valid_codes
                    )
                    if success:
                        QMessageBox.information(self, "成功", f"成功导入 {count} 只ETF")
                        self.on_group_combo_changed(self.group_combo.currentIndex())
                    else:
                        QMessageBox.warning(self, "错误", msg)
                else:
                    QMessageBox.warning(self, "提示", "未找到有效的ETF代码")
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
            # Rename by creating new, copying items, deleting old
            items = self.watchlist_manager.get_group_stocks(self.current_group)
            self.watchlist_manager.create_group(new_name)
            self.watchlist_manager.import_stocks(new_name, items)
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
                self.group_combo.setCurrentIndex(0)  # Back to category mode
            else:
                QMessageBox.warning(self, "错误", msg)
    
    def add_etf_to_current_group(self, code: str):
        """添加ETF到当前自选分组"""
        if not self.watchlist_manager or not self.current_group:
            return False, "未选择分组"
            
        success, msg = self.watchlist_manager.add_to_group(self.current_group, code)
        if success:
            self.on_group_combo_changed(self.group_combo.currentIndex())
        return success, msg
    
    def remove_etf_from_current_group(self, code: str):
        """从当前自选分组移除ETF"""
        if not self.watchlist_manager or not self.current_group:
            return False, "未选择分组"
            
        success, msg = self.watchlist_manager.remove_from_group(self.current_group, code)
        if success:
            self.on_group_combo_changed(self.group_combo.currentIndex())
        return success, msg
    
    def is_showing_watchlist(self) -> bool:
        """是否正在显示自选分组"""
        return self.is_watchlist_mode
    
    def get_current_group(self) -> str:
        """获取当前显示的自选分组名"""
        return self.current_group if self.is_watchlist_mode else ""
