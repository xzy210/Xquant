# etf_list_widget.py - ETF列表组件
"""
ETF列表选择组件，支持搜索、分类过滤
"""
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QPushButton,
    QComboBox
)
from PyQt6.QtCore import Qt, pyqtSignal


class ETFListWidget(QWidget):
    """ETF列表组件"""
    
    # Signal: selected ETF changed
    etfSelected = pyqtSignal(str, str)  # code, name
    # Signal: display list changed
    displayListChanged = pyqtSignal(list)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.etf_list: List[str] = []  # All ETF codes
        self.name_map: Dict[str, str] = {}  # Code -> name mapping
        self.categories: List[Dict] = []  # Category info
        self.filtered_list: List[str] = []  # Filtered list
        self.current_category: str = ""  # Current category filter
        
        self.setupUI()
    
    def setupUI(self):
        """Setup UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # Category selector
        category_layout = QHBoxLayout()
        
        self.category_combo = QComboBox()
        self.category_combo.setStyleSheet("""
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
        total = len(self.etf_list)
        filtered = len(self.filtered_list)
        
        category_prefix = f"[{self.current_category}] " if self.current_category else ""
        
        if total == filtered:
            self.info_label.setText(f"{category_prefix}共 {total} 只ETF")
        else:
            self.info_label.setText(f"{category_prefix}显示 {filtered} / {total} 只ETF")
    
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
