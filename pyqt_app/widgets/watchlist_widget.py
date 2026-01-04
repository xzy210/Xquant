# watchlist_widget.py - 自选列表组件
"""
自选列表组件，支持同时显示股票和ETF的混合列表
"""
from typing import Dict, List, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QPushButton,
    QComboBox, QMenu, QInputDialog, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
import re


class WatchlistWidget(QWidget):
    """自选列表组件 - 支持股票和ETF混合显示"""
    
    # Signal: item selected (code, name, is_etf)
    itemSelected = pyqtSignal(str, str, bool)
    # Signal: display list changed
    displayListChanged = pyqtSignal(list)
    # Signal: group changed
    groupChanged = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.watchlist_manager = None
        self.stock_name_map: Dict[str, str] = {}  # Stock code -> name
        self.etf_name_map: Dict[str, str] = {}    # ETF code -> name
        self.etf_codes: set = set()               # Set of ETF codes for quick lookup
        
        self.current_group: str = ""
        self.current_display_list: List[str] = []
        self.filtered_list: List[str] = []
        
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
        
        # Group selector row
        group_layout = QHBoxLayout()
        
        self.group_combo = QComboBox()
        self.group_combo.setStyleSheet(combo_style)
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
        
        # Search box
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索代码或名称...")
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
        self.info_label = QLabel("请选择自选分组")
        self.info_label.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 11px;
                padding: 2px;
            }
        """)
        layout.addWidget(self.info_label)
        
        # List widget
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
        """Set watchlist manager"""
        self.watchlist_manager = manager
        self.update_group_combo()
    
    def set_name_maps(self, stock_name_map: Dict[str, str], etf_name_map: Dict[str, str], etf_codes: List[str]):
        """Set name maps for stocks and ETFs"""
        self.stock_name_map = stock_name_map or {}
        self.etf_name_map = etf_name_map or {}
        self.etf_codes = set(etf_codes) if etf_codes else set()
        # Also add etf_name_map keys to etf_codes for completeness
        self.etf_codes.update(self.etf_name_map.keys())
    
    def _is_etf_code(self, code: str) -> bool:
        """Check if code is ETF"""
        # First check if in etf_codes set
        if code in self.etf_codes:
            return True
        # Also check by prefix pattern (ETF codes typically start with 5 or 1)
        code_num = code.split('.')[0] if '.' in code else code
        if code_num.startswith(('51', '52', '58', '56', '15', '16', '18')):
            return True
        return False
    
    def _get_name(self, code: str) -> str:
        """Get name for code (stock or ETF)"""
        if self._is_etf_code(code):
            return self.etf_name_map.get(code, "")
        return self.stock_name_map.get(code, "")
    
    def _get_type_tag(self, code: str) -> str:
        """Get type tag for display"""
        if self._is_etf_code(code):
            return "[ETF]"
        return "[股票]"
    
    def update_group_combo(self):
        """Update group combo box"""
        if not self.watchlist_manager:
            return
        
        current_data = self.group_combo.currentData()
        
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        
        # Add placeholder
        self.group_combo.addItem("📋 请选择分组...", "")
        
        # Add all groups
        groups = self.watchlist_manager.get_all_groups()
        for group in groups:
            if group in ("全部股票", ""):
                continue
            # Protected groups use different icon
            if self.watchlist_manager.is_protected_group(group):
                self.group_combo.addItem(f"💼 {group}", group)
            else:
                self.group_combo.addItem(f"⭐ {group}", group)
        
        # Restore selection
        if current_data:
            index = self.group_combo.findData(current_data)
            if index >= 0:
                self.group_combo.setCurrentIndex(index)
        
        self.group_combo.blockSignals(False)
    
    def on_group_combo_changed(self, index):
        """Handle group selection change"""
        group_name = self.group_combo.currentData()
        self.current_group = group_name or ""
        
        if not group_name:
            # No group selected
            self.current_display_list = []
            self.info_label.setText("请选择自选分组")
        else:
            # Get group stocks
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
    
    def on_search_changed(self, text: str):
        """Handle search text change"""
        text = text.strip().lower()
        
        if not text:
            self.filtered_list = self.current_display_list.copy()
        else:
            self.filtered_list = []
            for code in self.current_display_list:
                name = self._get_name(code).lower()
                if text in code.lower() or text in name:
                    self.filtered_list.append(code)
        
        self.update_list_widget()
        self.update_info_label()
    
    def update_list_widget(self):
        """Update list display"""
        self.list_widget.clear()
        
        for code in self.filtered_list:
            name = self._get_name(code)
            is_etf = self._is_etf_code(code)
            type_tag = self._get_type_tag(code)
            
            display_text = f"{type_tag} {code}  {name}" if name else f"{type_tag} {code}"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, code)
            item.setData(Qt.ItemDataRole.UserRole + 1, is_etf)  # Store is_etf flag
            
            # Color coding: ETF in different color
            if is_etf:
                item.setForeground(QColor("#4fc3f7"))  # Light blue for ETF
            else:
                item.setForeground(QColor("#ffffff"))  # White for stock
            
            self.list_widget.addItem(item)
        
        self.displayListChanged.emit(self.filtered_list)
    
    def update_info_label(self):
        """Update stats info"""
        if not self.current_group:
            self.info_label.setText("请选择自选分组")
            return
        
        total = len(self.current_display_list)
        filtered = len(self.filtered_list)
        
        # Count stocks and ETFs
        stock_count = sum(1 for c in self.current_display_list if not self._is_etf_code(c))
        etf_count = total - stock_count
        
        prefix = f"[⭐{self.current_group}] "
        
        if total == filtered:
            self.info_label.setText(f"{prefix}共 {total} 只 (股票:{stock_count}, ETF:{etf_count})")
        else:
            self.info_label.setText(f"{prefix}显示 {filtered} / {total}")
    
    def clear_search(self):
        """Clear search"""
        self.search_input.clear()
    
    def on_item_clicked(self, item: QListWidgetItem):
        """Handle item click"""
        code = item.data(Qt.ItemDataRole.UserRole)
        is_etf = item.data(Qt.ItemDataRole.UserRole + 1)
        name = self._get_name(code)
        self.itemSelected.emit(code, name, is_etf)
    
    def on_item_double_clicked(self, item: QListWidgetItem):
        """Handle item double click"""
        code = item.data(Qt.ItemDataRole.UserRole)
        is_etf = item.data(Qt.ItemDataRole.UserRole + 1)
        name = self._get_name(code)
        self.itemSelected.emit(code, name, is_etf)
    
    def get_selected_item(self) -> Optional[Tuple[str, bool]]:
        """Get currently selected item (code, is_etf)"""
        current = self.list_widget.currentItem()
        if current:
            code = current.data(Qt.ItemDataRole.UserRole)
            is_etf = current.data(Qt.ItemDataRole.UserRole + 1)
            return code, is_etf
        return None
    
    def select_item(self, code: str):
        """Select specified item"""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == code:
                self.list_widget.setCurrentItem(item)
                self.list_widget.scrollToItem(item)
                break
    
    def select_next(self):
        """Select next item"""
        current_row = self.list_widget.currentRow()
        if current_row < self.list_widget.count() - 1:
            self.list_widget.setCurrentRow(current_row + 1)
            item = self.list_widget.currentItem()
            if item:
                code = item.data(Qt.ItemDataRole.UserRole)
                is_etf = item.data(Qt.ItemDataRole.UserRole + 1)
                name = self._get_name(code)
                self.itemSelected.emit(code, name, is_etf)
    
    def select_previous(self):
        """Select previous item"""
        current_row = self.list_widget.currentRow()
        if current_row > 0:
            self.list_widget.setCurrentRow(current_row - 1)
            item = self.list_widget.currentItem()
            if item:
                code = item.data(Qt.ItemDataRole.UserRole)
                is_etf = item.data(Qt.ItemDataRole.UserRole + 1)
                name = self._get_name(code)
                self.itemSelected.emit(code, name, is_etf)
    
    def show_group_menu(self):
        """Show group management menu"""
        menu = QMenu(self)
        
        # Create new group
        new_action = menu.addAction("✚ 新建分组")
        new_action.triggered.connect(self.create_new_group)
        
        # Only show these options if a group is selected
        if self.current_group:
            is_protected = self._is_current_group_protected()
            
            menu.addSeparator()
            
            if not is_protected:
                import_action = menu.addAction("📥 导入代码到当前分组")
                import_action.triggered.connect(self.import_codes_to_group)
                
                rename_action = menu.addAction("✏ 重命名当前分组")
                rename_action.triggered.connect(self.rename_current_group)
                
                delete_action = menu.addAction("🗑 删除当前分组")
                delete_action.triggered.connect(self.delete_current_group)
            else:
                info_action = menu.addAction("ℹ️ 此分组由系统自动管理")
                info_action.setEnabled(False)
        
        menu.exec(self.group_menu_btn.mapToGlobal(self.group_menu_btn.rect().bottomLeft()))
    
    def _is_current_group_protected(self) -> bool:
        """Check if current group is protected"""
        if not self.watchlist_manager or not self.current_group:
            return False
        return self.watchlist_manager.is_protected_group(self.current_group)
    
    def create_new_group(self):
        """Create new group"""
        if not self.watchlist_manager:
            QMessageBox.warning(self, "错误", "自选股管理器未初始化")
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
    
    def import_codes_to_group(self):
        """Import codes to current group"""
        if not self.watchlist_manager or not self.current_group:
            return
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入代码列表", "",
            "Text Files (*.txt *.csv);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Extract codes (6 digits with optional suffix)
                codes = re.findall(r'[0-9]{6}\.[A-Z]{2}|[0-9]{6}', content)
                codes = list(set(codes))
                
                if codes:
                    success, msg, count = self.watchlist_manager.import_stocks(
                        self.current_group, codes
                    )
                    if success:
                        QMessageBox.information(self, "成功", f"成功导入 {count} 只代码")
                        self.on_group_combo_changed(self.group_combo.currentIndex())
                    else:
                        QMessageBox.warning(self, "错误", msg)
                else:
                    QMessageBox.warning(self, "提示", "未找到有效的代码")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"读取文件失败: {e}")
    
    def rename_current_group(self):
        """Rename current group"""
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
        """Delete current group"""
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
                self.group_combo.setCurrentIndex(0)
            else:
                QMessageBox.warning(self, "错误", msg)
    
    def add_to_current_group(self, code: str) -> Tuple[bool, str]:
        """Add code to current group"""
        if not self.watchlist_manager or not self.current_group:
            return False, "未选择分组"
        
        if self._is_current_group_protected():
            return False, f"分组 '{self.current_group}' 是系统分组，不支持手动添加"
        
        success, msg = self.watchlist_manager.add_to_group(self.current_group, code)
        if success:
            self.on_group_combo_changed(self.group_combo.currentIndex())
        return success, msg
    
    def remove_from_current_group(self, code: str) -> Tuple[bool, str]:
        """Remove code from current group"""
        if not self.watchlist_manager or not self.current_group:
            return False, "未选择分组"
        
        if self._is_current_group_protected():
            return False, f"分组 '{self.current_group}' 是系统分组，不支持手动移除"
        
        success, msg = self.watchlist_manager.remove_from_group(self.current_group, code)
        if success:
            self.on_group_combo_changed(self.group_combo.currentIndex())
        return success, msg
    
    def get_current_group(self) -> str:
        """Get current group name"""
        return self.current_group
    
    def is_showing_group(self) -> bool:
        """Check if showing a group"""
        return bool(self.current_group)