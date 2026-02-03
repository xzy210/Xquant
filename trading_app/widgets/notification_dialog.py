# notification_dialog.py - 消息推送对话框
"""
消息推送设置和发送对话框
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QTextEdit, QPushButton, QCheckBox,
    QGroupBox, QMessageBox, QTabWidget, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

import sys
from pathlib import Path

# 添加父目录到路径
current_dir = Path(__file__).parent
parent_dir = current_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from notifier import get_notification_manager


class NotificationDialog(QDialog):
    """消息推送对话框"""
    
    def __init__(self, parent=None, stocks_data=None):
        """
        初始化对话框
        
        Args:
            parent: 父窗口
            stocks_data: 选股数据列表，格式 [{"code": "000001", "name": "平安银行", ...}, ...]
        """
        super().__init__(parent)
        self.stocks_data = stocks_data or []
        self.nm = get_notification_manager()
        
        self.setWindowTitle("消息推送中心")
        self.setMinimumSize(600, 500)
        self.setup_ui()
        self.load_settings()
    
    def setup_ui(self):
        """设置界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)
        
        # 使用 Tab 页面
        self.tab_widget = QTabWidget()
        
        # Tab 1: 发送消息 (放在第一位，最常用)
        send_tab = self.create_send_tab()
        self.tab_widget.addTab(send_tab, "📤 发送消息")
        
        # Tab 2: 发送选股数据（如果有数据）
        if self.stocks_data:
            stocks_tab = self.create_stocks_tab()
            self.tab_widget.addTab(stocks_tab, f"📊 选股数据 ({len(self.stocks_data)})")
        
        # Tab 3: 设置
        settings_tab = self.create_settings_tab()
        self.tab_widget.addTab(settings_tab, "⚙ 推送配置")
        
        layout.addWidget(self.tab_widget)
        
        # 底部状态和按钮栏
        bottom_layout = QHBoxLayout()
        
        self.loading_label = QLabel("")
        self.loading_label.setStyleSheet("color: #0078d4; font-weight: bold;")
        bottom_layout.addWidget(self.loading_label)
        
        bottom_layout.addStretch()
        
        close_btn = QPushButton("关闭窗口")
        close_btn.setFixedWidth(100)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #3c3c3c;
                padding: 6px;
            }
            QPushButton:hover {
                background-color: #454545;
            }
        """)
        close_btn.clicked.connect(self.close)
        bottom_layout.addWidget(close_btn)
        
        layout.addLayout(bottom_layout)
        
        # 设置全局 QSS
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
            }
            QTabWidget::pane {
                border: 1px solid #3c3c3c;
                background-color: #252526;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #2d2d2d;
                color: #aaaaaa;
                padding: 10px 20px;
                border: 1px solid #3c3c3c;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:hover {
                background-color: #353535;
            }
            QTabBar::tab:selected {
                background-color: #252526;
                color: #ffffff;
                border-bottom: 2px solid #0078d4;
            }
            QGroupBox {
                color: #0078d4;
                font-weight: bold;
                border: 1px solid #3c3c3c;
                border-radius: 6px;
                margin-top: 15px;
                padding: 15px 10px 10px 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                background-color: #252526;
            }
            QLabel {
                color: #dcdcdc;
            }
            QLineEdit, QTextEdit {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 8px;
                font-family: 'Consolas', 'Monaco', monospace;
            }
            QLineEdit:focus, QTextEdit:focus {
                border-color: #0078d4;
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
                background-color: #1084d8;
            }
            QPushButton:pressed {
                background-color: #006cbd;
            }
            QPushButton:disabled {
                background-color: #3c3c3c;
                color: #777777;
            }
            QCheckBox {
                color: #dcdcdc;
            }
            QTableWidget {
                background-color: #1e1e1e;
                color: #dcdcdc;
                gridline-color: #333333;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
            }
            QTableWidget::item {
                padding: 5px;
            }
            QTableWidget::item:selected {
                background-color: #264f78;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #2d2d2d;
                color: #ffffff;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #3c3c3c;
                border-right: 1px solid #3c3c3c;
            }
            QComboBox {
                background-color: #333333;
                color: #ffffff;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 5px 10px;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
        """)
    
    def create_settings_tab(self) -> QWidget:
        """创建设置页面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)
        
        # 配置面板
        config_group = QGroupBox("推送配置 (Webhook)")
        config_layout = QVBoxLayout(config_group)
        config_layout.setSpacing(15)
        
        # 启用开关
        self.enable_checkbox = QCheckBox("启用企业微信机器人推送")
        self.enable_checkbox.setStyleSheet("font-size: 14px; font-weight: bold;")
        config_layout.addWidget(self.enable_checkbox)
        
        # Webhook 地址行
        url_container = QVBoxLayout()
        url_container.addWidget(QLabel("机器人 Webhook 地址 (Key):"))
        
        url_input_layout = QHBoxLayout()
        self.webhook_input = QLineEdit()
        self.webhook_input.setPlaceholderText("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...")
        url_input_layout.addWidget(self.webhook_input)
        
        copy_btn = QPushButton("📋 复制")
        copy_btn.setFixedWidth(85)
        copy_btn.setStyleSheet("background-color: #444; font-weight: normal; padding: 5px;")
        copy_btn.clicked.connect(self.copy_webhook_to_clipboard)
        url_input_layout.addWidget(copy_btn)
        
        url_container.addLayout(url_input_layout)
        config_layout.addLayout(url_container)
        
        # 按钮行
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("💾 保存配置")
        save_btn.setFixedWidth(120)
        save_btn.clicked.connect(self.save_settings)
        btn_layout.addWidget(save_btn)
        
        test_btn = QPushButton("🧪 测试推送")
        test_btn.setFixedWidth(120)
        test_btn.setStyleSheet("background-color: #28a745;")
        test_btn.clicked.connect(self.send_test_message)
        btn_layout.addWidget(test_btn)
        
        btn_layout.addStretch()
        config_layout.addLayout(btn_layout)
        
        layout.addWidget(config_group)
        
        # 状态面板
        status_group = QGroupBox("当前连接状态")
        status_layout = QHBoxLayout(status_group)
        self.status_label = QLabel("正在检查...")
        self.status_label.setFont(QFont("Segoe UI", 10))
        status_layout.addWidget(self.status_label)
        layout.addWidget(status_group)
        
        layout.addStretch()
        return widget
    
    def create_send_tab(self) -> QWidget:
        """创建发送消息页面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # 顶部栏：类型选择和模板
        top_row = QHBoxLayout()
        
        type_box = QHBoxLayout()
        type_box.addWidget(QLabel("消息格式:"))
        self.msg_type_combo = QComboBox()
        self.msg_type_combo.addItems(["📝 纯文本", "M↓ Markdown"])
        self.msg_type_combo.currentIndexChanged.connect(self.on_msg_type_changed)
        type_box.addWidget(self.msg_type_combo)
        top_row.addLayout(type_box)
        
        top_row.addStretch()
        
        template_box = QHBoxLayout()
        template_box.addWidget(QLabel("快捷模板:"))
        self.template_combo = QComboBox()
        self.template_combo.addItems(["-- 选择模板 --", "收盘播报", "紧急提醒", "选股任务完成"])
        self.template_combo.currentIndexChanged.connect(self.apply_template)
        template_box.addWidget(self.template_combo)
        top_row.addLayout(template_box)
        
        layout.addLayout(top_row)
        
        # 输入区
        input_label_layout = QHBoxLayout()
        input_label_layout.addWidget(QLabel("推送内容:"))
        input_label_layout.addStretch()
        clear_btn = QPushButton("🧹 清空")
        clear_btn.setFixedWidth(80)
        clear_btn.setStyleSheet("background-color: transparent; color: #888; font-weight: normal; padding: 2px;")
        clear_btn.clicked.connect(lambda: self.message_input.clear())
        input_label_layout.addWidget(clear_btn)
        layout.addLayout(input_label_layout)
        
        self.message_input = QTextEdit()
        self.message_input.setPlaceholderText("请输入要推送到企业微信的消息内容...")
        self.message_input.setMinimumHeight(200)
        layout.addWidget(self.message_input)
        
        # Markdown 提示区
        self.format_hint_box = QGroupBox("Markdown 语法参考")
        hint_layout = QVBoxLayout(self.format_hint_box)
        self.format_hint = QLabel("")
        self.format_hint.setWordWrap(True)
        self.format_hint.setStyleSheet("color: #888888; font-size: 11px;")
        hint_layout.addWidget(self.format_hint)
        layout.addWidget(self.format_hint_box)
        self.on_msg_type_changed(0)
        
        # 发送按钮
        send_row = QHBoxLayout()
        send_row.addStretch()
        self.send_btn = QPushButton("🚀 立即推送消息")
        self.send_btn.setMinimumWidth(180)
        self.send_btn.setMinimumHeight(40)
        self.send_btn.clicked.connect(self.send_custom_message)
        send_row.addWidget(self.send_btn)
        layout.addLayout(send_row)
        
        return widget
    
    def create_stocks_tab(self) -> QWidget:
        """创建选股数据页面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # 工具栏
        tool_row = QHBoxLayout()
        tool_row.addWidget(QLabel("请选择要推送的股票 (多选):"))
        tool_row.addStretch()
        
        sel_all_btn = QPushButton("全选")
        sel_all_btn.setFixedWidth(60)
        sel_all_btn.setStyleSheet("background-color: #444; font-weight: normal;")
        sel_all_btn.clicked.connect(lambda: self.stocks_table.selectAll())
        tool_row.addWidget(sel_all_btn)
        
        unsel_all_btn = QPushButton("全不选")
        unsel_all_btn.setFixedWidth(70)
        unsel_all_btn.setStyleSheet("background-color: #444; font-weight: normal;")
        unsel_all_btn.clicked.connect(lambda: self.stocks_table.clearSelection())
        tool_row.addWidget(unsel_all_btn)
        
        layout.addLayout(tool_row)
        
        # 数据表格
        self.stocks_table = QTableWidget()
        self.stocks_table.setColumnCount(4)
        self.stocks_table.setHorizontalHeaderLabels(["代码", "名称", "现价", "涨跌幅"])
        self.stocks_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.stocks_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.stocks_table.setSelectionMode(QTableWidget.SelectionMode.MultiSelection)
        
        # 填充数据
        self.stocks_table.setRowCount(len(self.stocks_data))
        for i, stock in enumerate(self.stocks_data):
            self.stocks_table.setItem(i, 0, QTableWidgetItem(stock.get("code", "")))
            self.stocks_table.setItem(i, 1, QTableWidgetItem(stock.get("name", "")))
            
            price = stock.get("price", "")
            self.stocks_table.setItem(i, 2, QTableWidgetItem(f"{price:.2f}" if isinstance(price, (int, float)) else str(price)))
            
            change = stock.get("change_pct", "")
            if isinstance(change, (int, float)):
                item = QTableWidgetItem(f"{change:+.2f}%")
                item.setForeground(Qt.GlobalColor.red if change >= 0 else Qt.GlobalColor.green)
            else:
                item = QTableWidgetItem(str(change))
            self.stocks_table.setItem(i, 3, item)
        
        # 默认全选
        self.stocks_table.selectAll()
        layout.addWidget(self.stocks_table)
        
        # 推送设置
        push_settings = QHBoxLayout()
        push_settings.addWidget(QLabel("通知标题:"))
        self.stocks_title_input = QLineEdit()
        self.stocks_title_input.setText("选股策略推荐")
        push_settings.addWidget(self.stocks_title_input)
        layout.addLayout(push_settings)
        
        # 发送按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.send_stocks_btn = QPushButton("📊 推送选中股票数据")
        self.send_stocks_btn.setMinimumWidth(200)
        self.send_stocks_btn.setMinimumHeight(40)
        self.send_stocks_btn.setStyleSheet("background-color: #e67e22;")
        self.send_stocks_btn.clicked.connect(self.send_stocks_data)
        btn_row.addWidget(self.send_stocks_btn)
        layout.addLayout(btn_row)
        
        return widget
    
    def copy_webhook_to_clipboard(self):
        """复制 Webhook 到剪贴板"""
        url = self.webhook_input.text().strip()
        if url:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(url)
            self.show_loading_feedback("已复制到剪贴板")
    
    def apply_template(self, index):
        """应用消息模板"""
        if index == 0: return
        
        template_name = self.template_combo.currentText()
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if template_name == "收盘播报":
            text = f"# 📈 收盘播报\n> 日期：{now}\n\n今日行情已结束，选股列表中包含 {len(self.stocks_data)} 只潜力品种。"
            self.msg_type_combo.setCurrentIndex(1) # Markdown
        elif template_name == "紧急提醒":
            text = f"🚨 **紧急提醒**\n\n关键点位突破，请及时查看系统选股池！\n时间：{now}"
            self.msg_type_combo.setCurrentIndex(1)
        elif template_name == "选股任务完成":
            text = f"✅ 选股任务已完成！\n系统已扫描全部 A 股，共找到 {len(self.stocks_data)} 只符合条件的股票。"
            self.msg_type_combo.setCurrentIndex(0) # Text
            
        self.message_input.setText(text)
    
    def show_loading_feedback(self, text, duration=2000):
        """显示临时反馈信息"""
        self.loading_label.setText(text)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(duration, lambda: self.loading_label.setText(""))
    
    def load_settings(self):
        """加载设置"""
        self.enable_checkbox.setChecked(self.nm.config.enabled)
        self.webhook_input.setText(self.nm.get_webhook_url())
        self.update_status()
    
    def save_settings(self):
        """保存设置"""
        url = self.webhook_input.text().strip()
        enabled = self.enable_checkbox.isChecked()
        
        self.nm.set_webhook_url(url)
        self.nm.set_enabled(enabled)
        
        self.update_status()
        self.show_loading_feedback("✅ 配置已保存")
    
    def on_enable_changed(self, state):
        """启用状态改变"""
        pass
    
    def on_msg_type_changed(self, index):
        """消息类型改变"""
        is_markdown = (index == 1)
        self.format_hint_box.setVisible(is_markdown)
        if is_markdown:
            self.format_hint.setText(
                "• # 标题  • **粗体**  • [链接](url)  • > 引用\n"
                "• <font color=\"warning\">橙红</font>  • <font color=\"info\">绿色</font>  • <font color=\"comment\">灰色</font>"
            )
    
    def update_status(self):
        """更新状态显示"""
        if not self.nm.get_webhook_url():
            self.status_label.setText("❌ 未配置 Webhook URL")
            self.status_label.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        elif not self.nm.config.enabled:
            self.status_label.setText("⚠️ 推送功能已关闭")
            self.status_label.setStyleSheet("color: #ffd93d; font-weight: bold;")
        else:
            self.status_label.setText("✅ 连接正常，随时可以推送")
            self.status_label.setStyleSheet("color: #6bcf6b; font-weight: bold;")
    
    def send_test_message(self):
        """发送测试消息"""
        url = self.webhook_input.text().strip()
        if not url:
            QMessageBox.warning(self, "错误", "请先填写 Webhook URL")
            return
        
        self.nm.set_webhook_url(url)
        self.loading_label.setText("正在尝试连接...")
        
        success, msg = self.nm.send_test_message()
        
        self.loading_label.setText("")
        if success:
            QMessageBox.information(self, "成功", "测试推送成功！请查看企业微信。")
        else:
            QMessageBox.warning(self, "失败", f"推送失败：{msg}")
    
    def send_custom_message(self):
        """发送自定义消息"""
        if not self.nm.is_enabled():
            QMessageBox.warning(self, "提示", "推送未启用，请先到配置页开启。")
            return
        
        content = self.message_input.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "内容不能为空")
            return
        
        self.send_btn.setEnabled(False)
        self.loading_label.setText("正在推送...")
        
        msg_type = self.msg_type_combo.currentIndex()
        if msg_type == 0:
            success, msg = self.nm.send_text(content)
        else:
            success, msg = self.nm.send_markdown(content)
            
        self.send_btn.setEnabled(True)
        self.loading_label.setText("")
        
        if success:
            self.show_loading_feedback("🚀 推送成功")
            self.message_input.clear()
        else:
            QMessageBox.warning(self, "推送失败", msg)
    
    def send_stocks_data(self):
        """发送选股数据"""
        if not self.nm.is_enabled():
            QMessageBox.warning(self, "提示", "推送未启用，请先到配置页开启。")
            return
        
        # 获取选中的行
        selected_items = self.stocks_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "提示", "请先选择要推送的股票")
            return
            
        # 提取选中行的数据
        selected_rows = sorted(list(set(item.row() for item in selected_items)))
        stocks_to_send = []
        for row in selected_rows:
            stocks_to_send.append(self.stocks_data[row])
            
        title = self.stocks_title_input.text().strip() or "选股结果推送"
        
        self.send_stocks_btn.setEnabled(False)
        self.loading_label.setText(f"正在推送 {len(stocks_to_send)} 只股票...")
        
        success, msg = self.nm.send_stock_alert(title, stocks_to_send)
        
        self.send_stocks_btn.setEnabled(True)
        self.loading_label.setText("")
        
        if success:
            self.show_loading_feedback(f"🚀 已推送 {len(stocks_to_send)} 只股票")
        else:
            QMessageBox.warning(self, "推送失败", msg)


