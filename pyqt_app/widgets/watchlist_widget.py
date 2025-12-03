import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, 
    QPushButton, QInputDialog, QMessageBox, QSplitter,
    QMenu, QFileDialog, QLabel, QListWidgetItem
)
from PyQt6.QtCore import Qt, pyqtSignal
from .stock_list_widget import StockListWidget
from watchlist_manager import WatchlistManager

class WatchlistWidget(QWidget):
    """自选股管理组件"""
    stockSelected = pyqtSignal(str, str)  # code, name

    def __init__(self, watchlist_manager: WatchlistManager, name_map: dict, parent=None):
        super().__init__(parent)
        self.manager = watchlist_manager
        self.name_map = name_map
        self.setupUI()
        self.load_groups()

    def setupUI(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # Left side: Groups
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(5, 5, 5, 5)
        
        left_layout.addWidget(QLabel("自选分组"))
        
        self.group_list = QListWidget()
        self.group_list.itemClicked.connect(self.on_group_selected)
        self.group_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.group_list.customContextMenuRequested.connect(self.show_group_context_menu)
        left_layout.addWidget(self.group_list)
        
        # Group buttons
        group_btn_layout = QHBoxLayout()
        self.add_group_btn = QPushButton("新建")
        self.add_group_btn.clicked.connect(self.create_group)
        group_btn_layout.addWidget(self.add_group_btn)
        
        self.del_group_btn = QPushButton("删除")
        self.del_group_btn.clicked.connect(self.delete_group)
        group_btn_layout.addWidget(self.del_group_btn)
        
        left_layout.addLayout(group_btn_layout)
        
        splitter.addWidget(left_widget)

        # Right side: Stocks
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 5, 5, 5)
        
        self.current_group_label = QLabel("请选择分组")
        right_layout.addWidget(self.current_group_label)
        
        # Stock list (reuse StockListWidget)
        self.stock_list_widget = StockListWidget()
        self.stock_list_widget.stockSelected.connect(self.stockSelected)
        # Add context menu to stock list
        self.stock_list_widget.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.stock_list_widget.list_widget.customContextMenuRequested.connect(self.show_stock_context_menu)
        
        right_layout.addWidget(self.stock_list_widget)
        
        # Stock buttons
        stock_btn_layout = QHBoxLayout()
        self.import_btn = QPushButton("导入列表")
        self.import_btn.clicked.connect(self.import_stocks)
        stock_btn_layout.addWidget(self.import_btn)
        
        self.remove_stock_btn = QPushButton("移除股票")
        self.remove_stock_btn.clicked.connect(self.remove_stock)
        stock_btn_layout.addWidget(self.remove_stock_btn)
        
        right_layout.addLayout(stock_btn_layout)
        
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 2)

    def load_groups(self):
        self.group_list.clear()
        groups = self.manager.get_all_groups()
        for group in groups:
            self.group_list.addItem(group)
        
        if groups:
            self.group_list.setCurrentRow(0)
            self.on_group_selected(self.group_list.currentItem())

    def on_group_selected(self, item):
        if not item:
            return
        group_name = item.text()
        self.current_group_label.setText(f"分组: {group_name}")
        self.load_group_stocks(group_name)

    def load_group_stocks(self, group_name):
        stocks = self.manager.get_group_stocks(group_name)
        self.stock_list_widget.set_stock_list(stocks, self.name_map)

    def create_group(self):
        name, ok = QInputDialog.getText(self, "新建分组", "请输入分组名称:")
        if ok and name:
            success, msg = self.manager.create_group(name)
            if success:
                self.load_groups()
                # Select the new group
                items = self.group_list.findItems(name, Qt.MatchFlag.MatchExactly)
                if items:
                    self.group_list.setCurrentItem(items[0])
                    self.on_group_selected(items[0])
            else:
                QMessageBox.warning(self, "错误", msg)

    def delete_group(self):
        item = self.group_list.currentItem()
        if not item:
            return
        group_name = item.text()
        
        reply = QMessageBox.question(self, "确认删除", 
                                   f"确定要删除分组 '{group_name}' 吗？",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            success, msg = self.manager.delete_group(group_name)
            if success:
                self.load_groups()
                self.stock_list_widget.set_stock_list([], self.name_map)
                self.current_group_label.setText("请选择分组")
            else:
                QMessageBox.warning(self, "错误", msg)

    def import_stocks(self):
        item = self.group_list.currentItem()
        if not item:
            QMessageBox.warning(self, "提示", "请先选择一个分组")
            return
        group_name = item.text()
        
        file_path, _ = QFileDialog.getOpenFileName(self, "导入股票列表", "", "Text Files (*.txt *.csv);;All Files (*)")
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                    # 提取所有可能的股票代码
                    # 匹配 6位数字.后缀 或 纯6位数字
                    codes = re.findall(r'[0-9]{6}\.[A-Z]{2}|[0-9]{6}', content)
                    
                    valid_codes = []
                    # 获取所有已知的带后缀的代码
                    known_codes = set(self.name_map.keys())
                    
                    for code in codes:
                        if code in known_codes:
                            valid_codes.append(code)
                        elif len(code) == 6:
                            # 尝试匹配后缀
                            matched = False
                            for suffix in ['.SZ', '.SH', '.BJ']:
                                full_code = code + suffix
                                if full_code in known_codes:
                                    valid_codes.append(full_code)
                                    matched = True
                                    break
                            if not matched:
                                # 如果没找到匹配的后缀，但格式正确，也添加进去（可能是新股或数据未更新）
                                valid_codes.append(code)
                        else:
                            valid_codes.append(code)
                            
                    # 去重
                    valid_codes = list(set(valid_codes))
                            
                    if valid_codes:
                        success, msg, count = self.manager.import_stocks(group_name, valid_codes)
                        if success:
                            QMessageBox.information(self, "成功", msg)
                            self.load_group_stocks(group_name)
                        else:
                            QMessageBox.warning(self, "错误", msg)
                    else:
                        QMessageBox.warning(self, "提示", "未找到有效的股票代码")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"读取文件失败: {e}")

    def remove_stock(self):
        group_item = self.group_list.currentItem()
        if not group_item:
            return
        group_name = group_item.text()
        
        stock_code = self.stock_list_widget.get_selected_stock()
        if not stock_code:
            QMessageBox.warning(self, "提示", "请先选择要移除的股票")
            return
            
        reply = QMessageBox.question(self, "确认移除", 
                                   f"确定要从 '{group_name}' 移除 {stock_code} 吗？",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            success, msg = self.manager.remove_from_group(group_name, stock_code)
            if success:
                self.load_group_stocks(group_name)
            else:
                QMessageBox.warning(self, "错误", msg)

    def show_group_context_menu(self, position):
        menu = QMenu()
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        
        action = menu.exec(self.group_list.mapToGlobal(position))
        
        if action == delete_action:
            self.delete_group()
        elif action == rename_action:
            self.rename_group()

    def rename_group(self):
        item = self.group_list.currentItem()
        if not item:
            return
        old_name = item.text()
        new_name, ok = QInputDialog.getText(self, "重命名分组", "请输入新名称:", text=old_name)
        if ok and new_name and new_name != old_name:
            # Implement rename in manager (need to add this method to manager first)
            # For now, let's do a manual rename: create new, move stocks, delete old
            stocks = self.manager.get_group_stocks(old_name)
            self.manager.create_group(new_name)
            self.manager.import_stocks(new_name, stocks)
            self.manager.delete_group(old_name)
            self.load_groups()

    def show_stock_context_menu(self, position):
        menu = QMenu()
        remove_action = menu.addAction("从分组移除")
        
        action = menu.exec(self.stock_list_widget.list_widget.mapToGlobal(position))
        
        if action == remove_action:
            self.remove_stock()