# conditional_order_dialog.py - 条件单管理对话框
"""
条件单（止盈止损）管理界面

功能：
- 添加新条件单
- 查看条件单列表
- 撤销/删除条件单
- 条件单状态显示
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QTabWidget, QDateEdit, QTextEdit, QMessageBox,
    QDialogButtonBox, QCheckBox, QFrame, QSplitter
)
from PyQt6.QtCore import Qt, pyqtSignal, QDate
from PyQt6.QtGui import QColor, QBrush, QFont

logger = logging.getLogger(__name__)


class AddConditionalOrderDialog(QDialog):
    """添加条件单对话框"""
    
    def __init__(self, parent=None, stock_code: str = "", stock_name: str = "",
                 current_price: float = 0.0, available_volume: int = 0, 
                 cost_price: float = 0.0, total_cash: float = 0.0):
        super().__init__(parent)
        self.setWindowTitle("添加条件单")
        self.setMinimumWidth(500)
        self.setModal(True)
        
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.current_price = current_price
        self.available_volume = available_volume
        self.cost_price = cost_price  # Cost price for calculating percentage
        self.total_cash = total_cash  # Available cash for buy orders
        
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # 股票信息
        info_group = QGroupBox("股票信息")
        info_layout = QFormLayout(info_group)
        
        stock_label = QLabel(f"{self.stock_name} ({self.stock_code})")
        stock_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        info_layout.addRow("股票:", stock_label)
        
        price_label = QLabel(f"¥{self.current_price:.3f}" if self.current_price > 0 else "-")
        price_label.setStyleSheet("color: #f0ad4e; font-weight: bold;")
        info_layout.addRow("当前价:", price_label)
        
        # Show cost price if available
        if self.cost_price > 0:
            cost_label = QLabel(f"¥{self.cost_price:.3f}")
            cost_label.setStyleSheet("color: #5bc0de; font-weight: bold;")
            info_layout.addRow("成本价:", cost_label)
        
        if self.available_volume > 0:
            vol_label = QLabel(f"{self.available_volume} 股")
            info_layout.addRow("可用数量:", vol_label)
        
        layout.addWidget(info_group)
        
        # 条件设置
        condition_group = QGroupBox("条件设置")
        condition_layout = QFormLayout(condition_group)
        
        # 条件类型
        self.condition_type_combo = QComboBox()
        self.condition_type_combo.addItem("止盈 (价格≥触发价时卖出)", "take_profit")
        self.condition_type_combo.addItem("止损 (价格≤触发价时卖出)", "stop_loss")
        self.condition_type_combo.addItem("突破买入 (价格≥触发价时买入)", "breakout_buy")
        self.condition_type_combo.addItem("回调买入 (价格≤触发价时买入)", "pullback_buy")
        self.condition_type_combo.currentIndexChanged.connect(self.on_condition_type_changed)
        condition_layout.addRow("条件类型:", self.condition_type_combo)
        
        # 触发价格
        trigger_price_widget = QWidget()
        trigger_layout = QHBoxLayout(trigger_price_widget)
        trigger_layout.setContentsMargins(0, 0, 0, 0)
        
        self.trigger_price_spin = QDoubleSpinBox()
        self.trigger_price_spin.setDecimals(3)
        self.trigger_price_spin.setMinimum(0.001)
        self.trigger_price_spin.setMaximum(9999.999)
        self.trigger_price_spin.setSingleStep(0.01)
        if self.current_price > 0:
            # 默认止盈为当前价+5%，止损为当前价-5%
            self.trigger_price_spin.setValue(self.current_price * 1.05)
        trigger_layout.addWidget(self.trigger_price_spin)
        
        # Quick percentage buttons - based on cost price
        pct_btn_layout = QHBoxLayout()
        pct_btn_layout.setSpacing(2)
        for pct in [3, 5, 8, 10]:
            btn = QPushButton(f"+{pct}%")
            btn.setFixedWidth(40)
            btn.setStyleSheet("font-size: 10px; padding: 2px;")
            btn.clicked.connect(lambda checked, p=pct: self.set_trigger_by_pct(p))
            pct_btn_layout.addWidget(btn)
        for pct in [3, 5, 8, 10]:
            btn = QPushButton(f"-{pct}%")
            btn.setFixedWidth(40)
            btn.setStyleSheet("font-size: 10px; padding: 2px;")
            btn.clicked.connect(lambda checked, p=-pct: self.set_trigger_by_pct(p))
            pct_btn_layout.addWidget(btn)
        trigger_layout.addLayout(pct_btn_layout)
        
        # Tooltip for percentage buttons
        base_price_hint = "成本价" if self.cost_price > 0 else "当前价"
        tip = QLabel(f"(基于{base_price_hint}计算)")
        tip.setStyleSheet("color: #666; font-size: 10px;")
        trigger_layout.addWidget(tip)
        
        condition_layout.addRow("触发价格:", trigger_price_widget)
        
        # 委托数量
        volume_widget = QWidget()
        volume_layout = QHBoxLayout(volume_widget)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        
        self.volume_spin = QSpinBox()
        self.volume_spin.setMinimum(100)
        self.volume_spin.setMaximum(1000000)
        self.volume_spin.setSingleStep(100)
        self.volume_spin.setValue(min(100, self.available_volume) if self.available_volume > 0 else 100)
        volume_layout.addWidget(self.volume_spin)
        
        # Quick volume buttons for sell orders (based on available volume)
        if self.available_volume > 0:
            for ratio, label in [(0.25, "1/4仓"), (0.5, "半仓"), (0.75, "3/4仓"), (1.0, "全仓")]:
                btn = QPushButton(label)
                btn.setFixedWidth(45)
                btn.setStyleSheet("font-size: 10px; padding: 2px;")
                vol = int(self.available_volume * ratio)
                vol = (vol // 100) * 100  # Round to 100
                btn.clicked.connect(lambda checked, v=vol: self.volume_spin.setValue(max(100, v)))
                volume_layout.addWidget(btn)
        
        # Quick volume buttons for buy orders (based on available cash)
        if self.total_cash > 0 and self.current_price > 0:
            volume_layout.addWidget(QLabel(" | "))
            for ratio, label in [(0.25, "1/4资金"), (0.5, "半仓资金"), (0.75, "3/4资金"), (1.0, "全仓资金")]:
                btn = QPushButton(label)
                btn.setFixedWidth(55)
                btn.setStyleSheet("font-size: 10px; padding: 2px;")
                # Calculate volume based on cash and current price
                vol = int((self.total_cash * ratio) / self.current_price)
                vol = (vol // 100) * 100  # Round to 100
                btn.clicked.connect(lambda checked, v=vol: self.volume_spin.setValue(max(100, v)))
                volume_layout.addWidget(btn)
        
        volume_layout.addStretch()
        condition_layout.addRow("委托数量:", volume_widget)
        
        # 委托价格类型
        self.price_type_combo = QComboBox()
        self.price_type_combo.addItem("市价委托 (触发后按市价成交)", "market")
        self.price_type_combo.addItem("限价委托 (按指定价格挂单)", "limit")
        self.price_type_combo.currentIndexChanged.connect(self.on_price_type_changed)
        condition_layout.addRow("委托方式:", self.price_type_combo)
        
        # 限价价格（默认隐藏）
        self.limit_price_widget = QWidget()
        limit_layout = QHBoxLayout(self.limit_price_widget)
        limit_layout.setContentsMargins(0, 0, 0, 0)
        
        self.limit_price_spin = QDoubleSpinBox()
        self.limit_price_spin.setDecimals(3)
        self.limit_price_spin.setMinimum(0.001)
        self.limit_price_spin.setMaximum(9999.999)
        self.limit_price_spin.setSingleStep(0.01)
        self.limit_price_spin.setValue(self.current_price if self.current_price > 0 else 10.0)
        limit_layout.addWidget(self.limit_price_spin)
        
        same_btn = QPushButton("同触发价")
        same_btn.clicked.connect(lambda: self.limit_price_spin.setValue(self.trigger_price_spin.value()))
        limit_layout.addWidget(same_btn)
        limit_layout.addStretch()
        
        condition_layout.addRow("限价价格:", self.limit_price_widget)
        self.limit_price_widget.hide()
        
        # 过期日期
        expire_widget = QWidget()
        expire_layout = QHBoxLayout(expire_widget)
        expire_layout.setContentsMargins(0, 0, 0, 0)
        
        self.expire_check = QCheckBox("设置过期日期")
        self.expire_check.toggled.connect(self.on_expire_toggled)
        expire_layout.addWidget(self.expire_check)
        
        self.expire_date = QDateEdit()
        self.expire_date.setCalendarPopup(True)
        self.expire_date.setDate(QDate.currentDate().addDays(30))
        self.expire_date.setEnabled(False)
        expire_layout.addWidget(self.expire_date)
        expire_layout.addStretch()
        
        condition_layout.addRow("有效期:", expire_widget)
        
        # 备注
        self.remark_edit = QLineEdit()
        self.remark_edit.setPlaceholderText("可选，添加备注信息")
        condition_layout.addRow("备注:", self.remark_edit)
        
        layout.addWidget(condition_group)
        
        # 提示信息
        tip_label = QLabel("💡 提示: 条件单会在交易时间内实时监控，满足条件时自动下单执行")
        tip_label.setStyleSheet("color: #888; font-size: 11px;")
        tip_label.setWordWrap(True)
        layout.addWidget(tip_label)
        
        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def set_trigger_by_pct(self, pct: int):
        """按百分比设置触发价格（基于成本价，若无成本价则用当前价）"""
        base_price = self.cost_price if self.cost_price > 0 else self.current_price
        if base_price > 0:
            price = base_price * (1 + pct / 100)
            self.trigger_price_spin.setValue(round(price, 3))
    
    def on_condition_type_changed(self, index: int):
        """条件类型变更"""
        cond_type = self.condition_type_combo.currentData()
        # Use cost price for sell orders, current price for buy orders
        if cond_type in ["take_profit", "stop_loss"]:
            base_price = self.cost_price if self.cost_price > 0 else self.current_price
        else:
            base_price = self.current_price
        
        if base_price > 0:
            if cond_type in ["take_profit", "breakout_buy"]:
                # Take profit/breakout: +5%
                self.trigger_price_spin.setValue(round(base_price * 1.05, 3))
            else:
                # Stop loss/pullback: -5%
                self.trigger_price_spin.setValue(round(base_price * 0.95, 3))
    
    def on_price_type_changed(self, index: int):
        """价格类型变更"""
        is_limit = self.price_type_combo.currentData() == "limit"
        self.limit_price_widget.setVisible(is_limit)
        self.adjustSize()
    
    def on_expire_toggled(self, checked: bool):
        """过期日期开关"""
        self.expire_date.setEnabled(checked)
    
    def validate_and_accept(self):
        """验证并接受"""
        trigger_price = self.trigger_price_spin.value()
        volume = self.volume_spin.value()
        
        if trigger_price <= 0:
            QMessageBox.warning(self, "错误", "请输入有效的触发价格")
            return
        
        if volume < 100:
            QMessageBox.warning(self, "错误", "委托数量不能少于100股")
            return
        
        cond_type = self.condition_type_combo.currentData()
        if cond_type in ["take_profit", "stop_loss"]:
            if volume > self.available_volume > 0:
                QMessageBox.warning(self, "错误", f"卖出数量不能超过可用数量 {self.available_volume} 股")
                return
        
        self.accept()
    
    def get_order_data(self) -> dict:
        """获取条件单数据"""
        return {
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "condition_type": self.condition_type_combo.currentData(),
            "trigger_price": self.trigger_price_spin.value(),
            "order_volume": self.volume_spin.value(),
            "order_price_type": self.price_type_combo.currentData(),
            "order_price": self.limit_price_spin.value() if self.price_type_combo.currentData() == "limit" else 0,
            "expire_date": self.expire_date.date().toString("yyyy-MM-dd") if self.expire_check.isChecked() else "",
            "remark": self.remark_edit.text().strip()
        }


class ConditionalOrderWidget(QWidget):
    """条件单管理组件（嵌入到交易窗口的Tab页）"""
    
    # 信号
    order_added = pyqtSignal(object)  # ConditionalOrder
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 条件单服务
        self._service = None
        
        self.setup_ui()
    
    def set_service(self, service):
        """设置条件单服务"""
        self._service = service
        if service:
            service.orders_changed.connect(self.refresh_table)
            service.order_triggered.connect(self.on_order_triggered)
            service.order_executed.connect(self.on_order_executed)
            self.refresh_table()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)
        
        # 顶部工具栏
        toolbar = QHBoxLayout()
        
        # 监控状态
        self.status_label = QLabel("● 监控中")
        self.status_label.setStyleSheet("color: #5cb85c; font-weight: bold;")
        toolbar.addWidget(self.status_label)
        
        toolbar.addStretch()
        
        # 统计信息
        self.stats_label = QLabel("待触发: 0 | 已触发: 0")
        self.stats_label.setStyleSheet("color: #888;")
        toolbar.addWidget(self.stats_label)
        
        toolbar.addSpacing(15)
        
        # 操作按钮
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_table)
        toolbar.addWidget(refresh_btn)
        
        clear_btn = QPushButton("清理历史")
        clear_btn.clicked.connect(self.clear_history)
        toolbar.addWidget(clear_btn)
        
        layout.addLayout(toolbar)
        
        # 条件单表格
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "ID", "股票", "条件", "触发价", "方向", 
            "数量", "状态", "创建时间", "备注", "操作"
        ])
        
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #1e1e1e;
                color: #d4d4d4;
                gridline-color: #333;
                border: none;
                selection-background-color: #264f78;
            }
            QHeaderView::section {
                background-color: #2d2d2d;
                color: #d4d4d4;
                padding: 4px;
                border: 1px solid #333;
            }
        """)
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)
        
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        
        layout.addWidget(self.table)
        
        # 底部提示
        tip_layout = QHBoxLayout()
        tip_label = QLabel("💡 条件单会在交易时间内自动监控行情，满足触发条件时自动下单")
        tip_label.setStyleSheet("color: #666; font-size: 11px;")
        tip_layout.addWidget(tip_label)
        tip_layout.addStretch()
        layout.addLayout(tip_layout)
    
    def refresh_table(self):
        """刷新表格"""
        if not self._service:
            return
        
        orders = self._service.get_all_orders()
        self.table.setRowCount(0)
        
        pending_count = 0
        triggered_count = 0
        
        for order in orders:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            # ID
            self.table.setItem(row, 0, QTableWidgetItem(order.id))
            
            # 股票
            stock_item = QTableWidgetItem(f"{order.stock_name}\n{order.stock_code}")
            self.table.setItem(row, 1, stock_item)
            
            # 条件
            cond_item = QTableWidgetItem(order.condition_display)
            self.table.setItem(row, 2, cond_item)
            
            # 触发价
            price_item = QTableWidgetItem(f"{order.trigger_price:.3f}")
            self.table.setItem(row, 3, price_item)
            
            # 方向
            direction = order.direction_display
            dir_item = QTableWidgetItem(direction)
            if direction == "卖出":
                dir_item.setForeground(QBrush(QColor("#00da3c")))
            else:
                dir_item.setForeground(QBrush(QColor("#ec0000")))
            self.table.setItem(row, 4, dir_item)
            
            # 数量
            self.table.setItem(row, 5, QTableWidgetItem(str(order.order_volume)))
            
            # 状态
            status_item = QTableWidgetItem(order.status_display)
            status_colors = {
                "pending": "#f0ad4e",
                "triggered": "#5bc0de",
                "executed": "#5cb85c",
                "cancelled": "#888",
                "failed": "#d9534f",
                "expired": "#888"
            }
            color = status_colors.get(order.status, "#888")
            status_item.setForeground(QBrush(QColor(color)))
            self.table.setItem(row, 6, status_item)
            
            # 创建时间
            self.table.setItem(row, 7, QTableWidgetItem(order.created_at))
            
            # 备注
            self.table.setItem(row, 8, QTableWidgetItem(order.remark or "-"))
            
            # 操作按钮
            if order.status == "pending":
                pending_count += 1
                cancel_btn = QPushButton("撤销")
                cancel_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #f0ad4e;
                        color: white;
                        border: none;
                        padding: 3px 8px;
                        border-radius: 3px;
                    }
                    QPushButton:hover { background-color: #ec971f; }
                """)
                cancel_btn.clicked.connect(lambda checked, oid=order.id: self.cancel_order(oid))
                self.table.setCellWidget(row, 9, cancel_btn)
            else:
                if order.status in ["triggered", "executed"]:
                    triggered_count += 1
                del_btn = QPushButton("删除")
                del_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #555;
                        color: white;
                        border: none;
                        padding: 3px 8px;
                        border-radius: 3px;
                    }
                    QPushButton:hover { background-color: #666; }
                """)
                del_btn.clicked.connect(lambda checked, oid=order.id: self.delete_order(oid))
                self.table.setCellWidget(row, 9, del_btn)
        
        # 更新统计
        self.stats_label.setText(f"待触发: {pending_count} | 已触发: {triggered_count}")
        
        # 更新监控状态显示
        if self._service and self._service.is_monitoring:
            self.status_label.setText("● 监控中")
            self.status_label.setStyleSheet("color: #5cb85c; font-weight: bold;")
        else:
            self.status_label.setText("○ 未监控")
            self.status_label.setStyleSheet("color: #888; font-weight: bold;")
    
    def cancel_order(self, order_id: str):
        """撤销条件单"""
        if not self._service:
            return
        
        reply = QMessageBox.question(
            self, "确认撤销",
            "确定要撤销该条件单吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self._service.cancel_order(order_id)
            self.refresh_table()
    
    def delete_order(self, order_id: str):
        """删除条件单"""
        if not self._service:
            return
        
        reply = QMessageBox.question(
            self, "确认删除",
            "确定要删除该条件单记录吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self._service.remove_order(order_id)
            self.refresh_table()
    
    def clear_history(self):
        """清理历史记录"""
        if not self._service:
            return
        
        reply = QMessageBox.question(
            self, "清理历史",
            "确定要清理所有已完成的条件单记录吗？\n（待触发的条件单会保留）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self._service.clear_history(keep_pending=True)
            self.refresh_table()
    
    def on_order_triggered(self, order):
        """条件单触发回调"""
        QMessageBox.information(
            self, "条件单触发",
            f"条件单已触发！\n\n"
            f"股票: {order.stock_name} ({order.stock_code})\n"
            f"条件: {order.condition_display}\n"
            f"触发价: {order.trigger_price:.3f}\n"
            f"当前价: {order.last_price:.3f}\n\n"
            f"正在自动执行 {order.direction_display} {order.order_volume} 股..."
        )
        self.refresh_table()
    
    def on_order_executed(self, order, success: bool, message: str):
        """条件单执行回调"""
        if success:
            QMessageBox.information(
                self, "执行成功",
                f"条件单执行成功！\n\n"
                f"股票: {order.stock_name} ({order.stock_code})\n"
                f"{order.direction_display} {order.order_volume} 股\n"
                f"委托号: {order.broker_order_id}"
            )
        else:
            QMessageBox.warning(
                self, "执行失败",
                f"条件单执行失败！\n\n"
                f"股票: {order.stock_name} ({order.stock_code})\n"
                f"错误: {message}"
            )
        self.refresh_table()

