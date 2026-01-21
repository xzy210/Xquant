# trade_history_widget.py - 交易历史查看组件
"""
交易历史记录查看和分析界面

功能：
- 显示所有历史交易记录
- 支持按日期、股票、方向筛选
- 显示交易统计摘要
- 支持导出到CSV
- 分页浏览
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QLineEdit, QComboBox, QDateEdit, QMessageBox,
    QFileDialog, QSpinBox
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont

from services.trade_record_service import (
    get_trade_record_service, TradeRecord, TradeDirection, TradeSource
)

logger = logging.getLogger(__name__)


class TradeHistoryWidget(QWidget):
    """交易历史查看组件"""
    
    # 信号：选中股票代码，用于跳转到K线图
    stock_selected = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 获取交易记录服务
        self.trade_service = get_trade_record_service()
        self.trade_service.records_changed.connect(self.refresh_data)
        
        # 分页参数
        self.page_size = 50
        self.current_page = 0
        self.total_count = 0
        
        self.setup_ui()
        self.refresh_data()
    
    def setup_ui(self):
        """设置界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # ========== 顶部筛选区域 ==========
        filter_group = QGroupBox("筛选条件")
        filter_layout = QHBoxLayout(filter_group)
        
        # 日期范围
        filter_layout.addWidget(QLabel("日期:"))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addDays(-30))
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        filter_layout.addWidget(self.start_date_edit)
        
        filter_layout.addWidget(QLabel("至"))
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        filter_layout.addWidget(self.end_date_edit)
        
        filter_layout.addSpacing(20)
        
        # 股票代码
        filter_layout.addWidget(QLabel("股票:"))
        self.stock_code_edit = QLineEdit()
        self.stock_code_edit.setPlaceholderText("股票代码")
        self.stock_code_edit.setMaximumWidth(100)
        filter_layout.addWidget(self.stock_code_edit)
        
        filter_layout.addSpacing(10)
        
        # 方向
        filter_layout.addWidget(QLabel("方向:"))
        self.direction_combo = QComboBox()
        self.direction_combo.addItem("全部", "")
        self.direction_combo.addItem("买入", TradeDirection.BUY.value)
        self.direction_combo.addItem("卖出", TradeDirection.SELL.value)
        self.direction_combo.setMaximumWidth(80)
        filter_layout.addWidget(self.direction_combo)
        
        filter_layout.addSpacing(10)
        
        # 来源
        filter_layout.addWidget(QLabel("来源:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("全部", "")
        self.source_combo.addItem("手动", TradeSource.MANUAL.value)
        self.source_combo.addItem("条件单", TradeSource.CONDITIONAL.value)
        self.source_combo.addItem("ETF网格", TradeSource.ETF_GRID.value)
        self.source_combo.addItem("AI智能", TradeSource.AI_AGENT.value)
        self.source_combo.setMaximumWidth(100)
        filter_layout.addWidget(self.source_combo)
        
        filter_layout.addStretch()
        
        # 查询按钮
        search_btn = QPushButton("🔍 查询")
        search_btn.clicked.connect(self.on_search)
        search_btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; padding: 5px 15px;")
        filter_layout.addWidget(search_btn)
        
        # 重置按钮
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self.on_reset_filter)
        filter_layout.addWidget(reset_btn)
        
        # 导出按钮
        export_btn = QPushButton("📤 导出CSV")
        export_btn.clicked.connect(self.on_export)
        filter_layout.addWidget(export_btn)
        
        main_layout.addWidget(filter_group)
        
        # ========== 交易记录表格 ==========
        table_group = QGroupBox("交易记录")
        table_layout = QVBoxLayout(table_group)
        
        # 表格样式
        table_style = """
            QTableWidget {
                background-color: #1e1e1e;
                color: #d4d4d4;
                gridline-color: #333;
                border: none;
                selection-background-color: #264f78;
                selection-color: #ffffff;
                alternate-background-color: #252526;
            }
            QHeaderView::section {
                background-color: #2d2d2d;
                color: #d4d4d4;
                padding: 6px;
                border: 1px solid #333;
                font-weight: bold;
            }
            QTableCornerButton::section {
                background-color: #2d2d2d;
                border: 1px solid #333;
            }
        """
        
        self.records_table = QTableWidget()
        self.records_table.setStyleSheet(table_style)
        self.records_table.setColumnCount(12)
        self.records_table.setHorizontalHeaderLabels([
            "ID", "交易日期", "股票代码", "股票名称",
            "方向", "价格", "数量", "金额", "佣金", "印花税", "过户费", "来源"
        ])
        self.records_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.records_table.horizontalHeader().setStretchLastSection(True)
        self.records_table.setAlternatingRowColors(True)
        self.records_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.records_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.records_table.itemDoubleClicked.connect(self.on_row_double_clicked)
        # 隐藏ID列
        self.records_table.setColumnHidden(0, True)
        
        table_layout.addWidget(self.records_table)
        
        # 分页控制
        page_layout = QHBoxLayout()
        
        self.page_info_label = QLabel("共 0 条记录")
        page_layout.addWidget(self.page_info_label)
        
        page_layout.addStretch()
        
        self.prev_btn = QPushButton("◀ 上一页")
        self.prev_btn.clicked.connect(self.on_prev_page)
        self.prev_btn.setEnabled(False)
        page_layout.addWidget(self.prev_btn)
        
        self.page_label = QLabel("第 1 页")
        self.page_label.setMinimumWidth(80)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_layout.addWidget(self.page_label)
        
        self.next_btn = QPushButton("下一页 ▶")
        self.next_btn.clicked.connect(self.on_next_page)
        self.next_btn.setEnabled(False)
        page_layout.addWidget(self.next_btn)
        
        page_layout.addSpacing(20)
        
        page_layout.addWidget(QLabel("每页:"))
        self.page_size_spin = QSpinBox()
        self.page_size_spin.setRange(10, 200)
        self.page_size_spin.setValue(self.page_size)
        self.page_size_spin.setSingleStep(10)
        self.page_size_spin.valueChanged.connect(self.on_page_size_changed)
        page_layout.addWidget(self.page_size_spin)
        
        table_layout.addLayout(page_layout)
        
        main_layout.addWidget(table_group, stretch=1)
    
    def get_filter_params(self) -> dict:
        """获取筛选参数"""
        params = {
            "start_date": self.start_date_edit.date().toString("yyyy-MM-dd"),
            "end_date": self.end_date_edit.date().toString("yyyy-MM-dd"),
        }
        
        stock_code = self.stock_code_edit.text().strip()
        if stock_code:
            params["stock_code"] = stock_code
        
        direction = self.direction_combo.currentData()
        if direction:
            params["direction"] = direction
        
        source = self.source_combo.currentData()
        if source:
            params["source"] = source
        
        return params
    
    def refresh_data(self):
        """刷新数据"""
        params = self.get_filter_params()
        
        # 获取总数
        self.total_count = self.trade_service.get_records_count(**params)
        
        # 获取当前页数据
        records = self.trade_service.get_records(
            **params,
            limit=self.page_size,
            offset=self.current_page * self.page_size
        )
        
        # 更新表格
        self.update_table(records)
        
        # 更新分页信息
        self.update_pagination()
    
    def update_table(self, records: list):
        """更新表格数据"""
        self.records_table.setRowCount(0)
        
        for record in records:
            row = self.records_table.rowCount()
            self.records_table.insertRow(row)
            
            # ID（隐藏列）
            self.records_table.setItem(row, 0, QTableWidgetItem(str(record.id)))
            
            # 交易日期
            self.records_table.setItem(row, 1, QTableWidgetItem(record.trade_date))
            
            # 股票代码
            self.records_table.setItem(row, 2, QTableWidgetItem(record.stock_code))
            
            # 股票名称
            self.records_table.setItem(row, 3, QTableWidgetItem(record.stock_name))
            
            # 方向
            direction_item = QTableWidgetItem(record.direction_display)
            if record.direction == TradeDirection.BUY.value:
                direction_item.setForeground(QBrush(QColor("#ec0000")))
            else:
                direction_item.setForeground(QBrush(QColor("#00da3c")))
            self.records_table.setItem(row, 4, direction_item)
            
            # 价格
            self.records_table.setItem(row, 5, QTableWidgetItem(f"{record.price:.4f}"))
            
            # 数量
            self.records_table.setItem(row, 6, QTableWidgetItem(str(record.volume)))
            
            # 金额
            amount_item = QTableWidgetItem(f"{record.amount:,.2f}")
            amount_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.records_table.setItem(row, 7, amount_item)
            
            # 佣金
            comm_item = QTableWidgetItem(f"{record.commission:.2f}")
            comm_item.setForeground(QBrush(QColor("#f0ad4e")))
            self.records_table.setItem(row, 8, comm_item)
            
            # 印花税
            stamp_item = QTableWidgetItem(f"{record.stamp_tax:.2f}")
            stamp_item.setForeground(QBrush(QColor("#f0ad4e")))
            self.records_table.setItem(row, 9, stamp_item)
            
            # 过户费
            transfer_item = QTableWidgetItem(f"{record.transfer_fee:.2f}")
            transfer_item.setForeground(QBrush(QColor("#f0ad4e")))
            self.records_table.setItem(row, 10, transfer_item)
            
            # 来源
            self.records_table.setItem(row, 11, QTableWidgetItem(record.source_display))
    
    def update_pagination(self):
        """更新分页控件"""
        total_pages = max(1, (self.total_count + self.page_size - 1) // self.page_size)
        current_page_display = self.current_page + 1
        
        self.page_info_label.setText(f"共 {self.total_count} 条记录")
        self.page_label.setText(f"第 {current_page_display}/{total_pages} 页")
        
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(current_page_display < total_pages)
    
    def on_search(self):
        """搜索按钮点击"""
        self.current_page = 0
        self.refresh_data()
    
    def on_reset_filter(self):
        """重置筛选条件"""
        self.start_date_edit.setDate(QDate.currentDate().addDays(-30))
        self.end_date_edit.setDate(QDate.currentDate())
        self.stock_code_edit.clear()
        self.direction_combo.setCurrentIndex(0)
        self.source_combo.setCurrentIndex(0)
        self.current_page = 0
        self.refresh_data()
    
    def on_export(self):
        """导出到CSV"""
        # 选择保存路径
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"trade_history_{timestamp}.csv"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出交易记录",
            default_name,
            "CSV文件 (*.csv)"
        )
        
        if not file_path:
            return
        
        params = self.get_filter_params()
        success = self.trade_service.export_to_csv(
            file_path,
            start_date=params.get("start_date"),
            end_date=params.get("end_date")
        )
        
        if success:
            QMessageBox.information(self, "导出成功", f"交易记录已导出到:\n{file_path}")
        else:
            QMessageBox.warning(self, "导出失败", "导出交易记录失败，请查看日志")
    
    def on_prev_page(self):
        """上一页"""
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_data()
    
    def on_next_page(self):
        """下一页"""
        total_pages = (self.total_count + self.page_size - 1) // self.page_size
        if self.current_page + 1 < total_pages:
            self.current_page += 1
            self.refresh_data()
    
    def on_page_size_changed(self, value):
        """每页数量变化"""
        self.page_size = value
        self.current_page = 0
        self.refresh_data()
    
    def on_row_double_clicked(self, item):
        """双击行跳转到K线图"""
        row = item.row()
        stock_code_item = self.records_table.item(row, 2)  # 股票代码在第2列
        if stock_code_item:
            stock_code = stock_code_item.text()
            self.stock_selected.emit(stock_code)

