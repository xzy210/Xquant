# auto_stop_loss_config_dialog.py - 自动止损配置对话框
"""
自动止损配置对话框

功能：
- 启用/禁用自动止损
- 设置默认止损比例
- 设置委托价格类型
- 设置有效期
- 管理豁免股票
"""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox,
    QSpinBox, QDoubleSpinBox, QComboBox, QGroupBox,
    QListWidget, QListWidgetItem, QMessageBox,
    QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

logger = logging.getLogger(__name__)


class AutoStopLossConfigDialog(QDialog):
    """自动止损配置对话框"""
    
    config_saved = pyqtSignal()
    
    def __init__(self, auto_stop_loss_service, parent=None):
        super().__init__(parent)
        self.service = auto_stop_loss_service
        self.setWindowTitle("自动止损设置")
        self.setMinimumWidth(450)
        self.setMinimumHeight(500)
        
        self._init_ui()
        self._load_config()
    
    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # ========== 启用开关 ==========
        enable_group = QGroupBox("自动止损")
        enable_layout = QHBoxLayout(enable_group)
        
        self.enable_checkbox = QCheckBox("启用自动止损（买入成交后自动创建止损条件单）")
        self.enable_checkbox.setFont(QFont("", 10))
        enable_layout.addWidget(self.enable_checkbox)
        
        layout.addWidget(enable_group)
        
        # ========== 止损参数 ==========
        param_group = QGroupBox("止损参数")
        param_layout = QGridLayout(param_group)
        param_layout.setSpacing(10)
        
        row = 0
        
        # 止损比例
        param_layout.addWidget(QLabel("默认止损比例:"), row, 0)
        self.stop_loss_pct_spin = QDoubleSpinBox()
        self.stop_loss_pct_spin.setRange(0.5, 30.0)
        self.stop_loss_pct_spin.setSingleStep(0.5)
        self.stop_loss_pct_spin.setSuffix(" %")
        self.stop_loss_pct_spin.setToolTip("当股价跌破成本价的该百分比时触发止损卖出")
        param_layout.addWidget(self.stop_loss_pct_spin, row, 1)
        
        # 快捷按钮
        pct_btn_layout = QHBoxLayout()
        for pct in [3, 5, 8, 10]:
            btn = QPushButton(f"{pct}%")
            btn.setMaximumWidth(50)
            btn.clicked.connect(lambda checked, p=pct: self.stop_loss_pct_spin.setValue(p))
            pct_btn_layout.addWidget(btn)
        pct_btn_layout.addStretch()
        param_layout.addLayout(pct_btn_layout, row, 2)
        
        row += 1
        
        # 委托价格类型
        param_layout.addWidget(QLabel("委托价格类型:"), row, 0)
        self.price_type_combo = QComboBox()
        self.price_type_combo.addItem("市价委托（确保成交）", "market")
        self.price_type_combo.addItem("限价委托", "limit")
        param_layout.addWidget(self.price_type_combo, row, 1, 1, 2)
        
        row += 1
        
        # 限价偏移（仅限价时有效）
        param_layout.addWidget(QLabel("限价偏移:"), row, 0)
        self.limit_offset_spin = QDoubleSpinBox()
        self.limit_offset_spin.setRange(0.0, 5.0)
        self.limit_offset_spin.setSingleStep(0.1)
        self.limit_offset_spin.setSuffix(" %")
        self.limit_offset_spin.setToolTip("限价单相对止损价再下浮的比例，确保能成交")
        param_layout.addWidget(self.limit_offset_spin, row, 1, 1, 2)
        
        row += 1
        
        # 有效期
        param_layout.addWidget(QLabel("止损单有效期:"), row, 0)
        self.expire_days_spin = QSpinBox()
        self.expire_days_spin.setRange(0, 365)
        self.expire_days_spin.setSuffix(" 天")
        self.expire_days_spin.setSpecialValueText("永不过期")
        self.expire_days_spin.setToolTip("0 表示永不过期")
        param_layout.addWidget(self.expire_days_spin, row, 1, 1, 2)
        
        layout.addWidget(param_group)
        
        # ========== 高级设置 ==========
        advanced_group = QGroupBox("高级设置")
        advanced_layout = QVBoxLayout(advanced_group)
        
        # 合并同一股票的止损单
        self.merge_checkbox = QCheckBox("合并同一股票的止损单（多次买入累加数量）")
        self.merge_checkbox.setToolTip("同一股票多次买入时，自动合并为一个止损单")
        advanced_layout.addWidget(self.merge_checkbox)
        
        # 创建时通知
        self.notify_checkbox = QCheckBox("创建止损单时发送通知")
        advanced_layout.addWidget(self.notify_checkbox)
        
        # 豁免ETF
        self.exempt_etf_checkbox = QCheckBox("豁免ETF（不自动创建止损单）")
        self.exempt_etf_checkbox.setToolTip("ETF通常用于长期定投，可豁免自动止损")
        advanced_layout.addWidget(self.exempt_etf_checkbox)
        
        layout.addWidget(advanced_group)
        
        # ========== 豁免股票 ==========
        exempt_group = QGroupBox("豁免股票（不自动创建止损单）")
        exempt_layout = QVBoxLayout(exempt_group)
        
        # 添加区域
        add_layout = QHBoxLayout()
        self.exempt_code_edit = QLineEdit()
        self.exempt_code_edit.setPlaceholderText("输入股票代码，如 600519")
        self.exempt_code_edit.setMaximumWidth(200)
        add_layout.addWidget(self.exempt_code_edit)
        
        self.add_exempt_btn = QPushButton("添加")
        self.add_exempt_btn.setMaximumWidth(80)
        self.add_exempt_btn.clicked.connect(self._add_exempt_code)
        add_layout.addWidget(self.add_exempt_btn)
        
        self.remove_exempt_btn = QPushButton("删除选中")
        self.remove_exempt_btn.setMaximumWidth(80)
        self.remove_exempt_btn.clicked.connect(self._remove_exempt_code)
        add_layout.addWidget(self.remove_exempt_btn)
        
        add_layout.addStretch()
        exempt_layout.addLayout(add_layout)
        
        # 列表
        self.exempt_list = QListWidget()
        self.exempt_list.setMaximumHeight(100)
        self.exempt_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        exempt_layout.addWidget(self.exempt_list)
        
        layout.addWidget(exempt_group)
        
        # ========== 说明 ==========
        info_frame = QFrame()
        info_frame.setStyleSheet("QFrame { background-color: #2d3436; border-radius: 5px; padding: 5px; }")
        info_layout = QVBoxLayout(info_frame)
        info_label = QLabel(
            "💡 自动止损说明：\n"
            "• 每次买入成交后，系统会自动创建一个止损条件单\n"
            "• 当股价跌至止损价时，自动触发卖出\n"
            "• 止损价 = 成本价 × (1 - 止损比例)"
        )
        info_label.setStyleSheet("color: #b2bec3; font-size: 9pt;")
        info_layout.addWidget(info_label)
        layout.addWidget(info_frame)
        
        # ========== 按钮 ==========
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.save_btn = QPushButton("保存")
        self.save_btn.setMinimumWidth(100)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #0984e3;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 5px;
                font-size: 10pt;
            }
            QPushButton:hover { background-color: #74b9ff; }
        """)
        self.save_btn.clicked.connect(self._save_config)
        btn_layout.addWidget(self.save_btn)
        
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setMinimumWidth(100)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(btn_layout)
        
        # 绑定事件
        self.price_type_combo.currentIndexChanged.connect(self._on_price_type_changed)
    
    def _on_price_type_changed(self, index):
        """价格类型变化"""
        is_limit = self.price_type_combo.currentData() == "limit"
        self.limit_offset_spin.setEnabled(is_limit)
    
    def _load_config(self):
        """加载配置"""
        config = self.service.config
        
        self.enable_checkbox.setChecked(config.enabled)
        self.stop_loss_pct_spin.setValue(config.stop_loss_pct)
        
        # 设置价格类型
        index = self.price_type_combo.findData(config.price_type)
        if index >= 0:
            self.price_type_combo.setCurrentIndex(index)
        
        self.limit_offset_spin.setValue(config.limit_offset_pct)
        self.expire_days_spin.setValue(config.expire_days)
        self.merge_checkbox.setChecked(config.merge_same_stock)
        self.notify_checkbox.setChecked(config.notify_on_create)
        self.exempt_etf_checkbox.setChecked(config.exempt_etf)
        
        # 加载豁免列表
        self.exempt_list.clear()
        for code in config.exempt_codes:
            self.exempt_list.addItem(code)
        
        # 更新UI状态
        self._on_price_type_changed(self.price_type_combo.currentIndex())
    
    def _save_config(self):
        """保存配置"""
        # 收集豁免列表
        exempt_codes = []
        for i in range(self.exempt_list.count()):
            exempt_codes.append(self.exempt_list.item(i).text())
        
        # 更新配置
        self.service.update_config(
            enabled=self.enable_checkbox.isChecked(),
            stop_loss_pct=self.stop_loss_pct_spin.value(),
            price_type=self.price_type_combo.currentData(),
            limit_offset_pct=self.limit_offset_spin.value(),
            expire_days=self.expire_days_spin.value(),
            merge_same_stock=self.merge_checkbox.isChecked(),
            notify_on_create=self.notify_checkbox.isChecked(),
            exempt_etf=self.exempt_etf_checkbox.isChecked(),
            exempt_codes=exempt_codes
        )
        
        self.config_saved.emit()
        QMessageBox.information(self, "成功", "自动止损配置已保存")
        self.accept()
    
    def _add_exempt_code(self):
        """添加豁免股票"""
        code = self.exempt_code_edit.text().strip()
        if not code:
            return
        
        # 去掉后缀
        code = code.split('.')[0]
        
        # 检查是否已存在
        for i in range(self.exempt_list.count()):
            if self.exempt_list.item(i).text() == code:
                QMessageBox.warning(self, "提示", f"股票 {code} 已在豁免列表中")
                return
        
        self.exempt_list.addItem(code)
        self.exempt_code_edit.clear()
    
    def _remove_exempt_code(self):
        """删除选中的豁免股票"""
        selected = self.exempt_list.selectedItems()
        if not selected:
            return
        
        for item in selected:
            self.exempt_list.takeItem(self.exempt_list.row(item))
