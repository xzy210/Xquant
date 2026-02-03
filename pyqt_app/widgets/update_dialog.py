from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QProgressBar, QTextEdit, QPushButton, QLineEdit,
    QCheckBox, QMessageBox, QDateEdit, QComboBox,
    QGroupBox, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QDate

import sys
import os

# 导入 xtquant 检查函数
try:
    from pathlib import Path
    import sys
    project_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(project_root))
    from scripts import fetch_kline_xtquant
    HAS_XTQUANT_MODULE = True
except ImportError:
    HAS_XTQUANT_MODULE = False
    fetch_kline_xtquant = None


class UpdateDialog(QDialog):
    # 更新信号：token, full_update, exclude_boards, start_date, data_source, period
    start_update = pyqtSignal(str, bool, list, str, str, str)
    stop_update = pyqtSignal()

    def __init__(self, parent=None, default_token=""):
        super().__init__(parent)
        self.setWindowTitle("更新股票数据")
        self.setMinimumSize(550, 550)
        self.setupUI(default_token)

    def setupUI(self, default_token):
        layout = QVBoxLayout(self)

        # ========== 数据源选择 ==========
        source_group = QGroupBox("数据源")
        source_layout = QVBoxLayout(source_group)
        
        # 数据源下拉框
        source_select_layout = QHBoxLayout()
        source_select_layout.addWidget(QLabel("选择数据源:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("Tushare", "tushare")
        self.source_combo.addItem("xtquant (miniQMT)", "xtquant")
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        source_select_layout.addWidget(self.source_combo)
        source_select_layout.addStretch()
        source_layout.addLayout(source_select_layout)
        
        # Tushare Token 输入（默认显示）
        self.token_widget = QFrame()
        token_layout = QHBoxLayout(self.token_widget)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.addWidget(QLabel("Tushare Token:"))
        self.token_edit = QLineEdit(default_token)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        token_layout.addWidget(self.token_edit)
        source_layout.addWidget(self.token_widget)
        
        # xtquant 连接状态（默认隐藏）
        self.xtquant_widget = QFrame()
        xtquant_layout = QHBoxLayout(self.xtquant_widget)
        xtquant_layout.setContentsMargins(0, 0, 0, 0)
        self.xtquant_status_label = QLabel("miniQMT 状态: 未检测")
        xtquant_layout.addWidget(self.xtquant_status_label)
        self.check_connection_btn = QPushButton("检测连接")
        self.check_connection_btn.clicked.connect(self.check_xtquant_connection)
        xtquant_layout.addWidget(self.check_connection_btn)
        xtquant_layout.addStretch()
        source_layout.addWidget(self.xtquant_widget)
        self.xtquant_widget.setVisible(False)
        
        layout.addWidget(source_group)

        # ========== K线周期选择 ==========
        period_group = QGroupBox("K线周期")
        period_layout = QHBoxLayout(period_group)
        
        period_layout.addWidget(QLabel("周期:"))
        self.period_combo = QComboBox()
        self.period_combo.addItem("日线", "1d")
        self.period_combo.addItem("1分钟", "1m")
        self.period_combo.addItem("5分钟", "5m")
        self.period_combo.addItem("15分钟", "15m")
        self.period_combo.addItem("30分钟", "30m")
        self.period_combo.addItem("60分钟", "60m")
        self.period_combo.currentIndexChanged.connect(self.on_period_changed)
        period_layout.addWidget(self.period_combo)
        
        self.period_note_label = QLabel("")
        self.period_note_label.setStyleSheet("color: #888;")
        period_layout.addWidget(self.period_note_label)
        period_layout.addStretch()
        
        layout.addWidget(period_group)

        # ========== 更新选项 ==========
        options_group = QGroupBox("更新选项")
        options_layout = QVBoxLayout(options_group)
        
        # 全量更新选项
        self.full_update_cb = QCheckBox("强制全量更新 (较慢)")
        self.full_update_cb.stateChanged.connect(self.on_full_update_toggled)
        options_layout.addWidget(self.full_update_cb)
        
        # 起始日期选择（仅全量更新时显示）
        self.start_date_layout = QHBoxLayout()
        self.start_date_label = QLabel("起始日期:")
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate(2019, 1, 1))
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_layout.addWidget(self.start_date_label)
        self.start_date_layout.addWidget(self.start_date_edit)
        self.start_date_layout.addStretch()
        options_layout.addLayout(self.start_date_layout)
        
        # 默认隐藏日期选择
        self.start_date_label.setVisible(False)
        self.start_date_edit.setVisible(False)

        # 排除板块选项
        exclude_layout = QVBoxLayout()
        exclude_layout.addWidget(QLabel("排除板块:"))
        
        self.exclude_gem_cb = QCheckBox("排除创业板 (300/301)")
        self.exclude_gem_cb.setChecked(True)
        exclude_layout.addWidget(self.exclude_gem_cb)
        
        self.exclude_star_cb = QCheckBox("排除科创板 (688)")
        self.exclude_star_cb.setChecked(True)
        exclude_layout.addWidget(self.exclude_star_cb)
        
        self.exclude_bj_cb = QCheckBox("排除北交所 (BJ/4/8)")
        self.exclude_bj_cb.setChecked(True)
        exclude_layout.addWidget(self.exclude_bj_cb)
        
        options_layout.addLayout(exclude_layout)
        layout.addWidget(options_group)

        # ========== 进度显示 ==========
        self.status_label = QLabel("准备就绪")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # 日志区域
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        layout.addWidget(self.log_text)

        # ========== 按钮 ==========
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始更新")
        self.start_btn.clicked.connect(self.on_start_clicked)
        btn_layout.addWidget(self.start_btn)

        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)
        
        # 初始化界面状态
        self.on_source_changed(0)

    def on_source_changed(self, index):
        """数据源切换时更新界面"""
        source = self.source_combo.currentData()
        
        # 切换配置区域显示
        self.token_widget.setVisible(source == "tushare")
        self.xtquant_widget.setVisible(source == "xtquant")
        
        # 更新周期提示
        self.update_period_note()

    def on_period_changed(self, index):
        """周期切换时更新提示"""
        self.update_period_note()
    
    def update_period_note(self):
        """更新周期提示信息"""
        source = self.source_combo.currentData()
        period = self.period_combo.currentData()
        
        if source == "tushare" and period != "1d":
            self.period_note_label.setText("(Tushare 仅支持日线)")
            self.period_note_label.setStyleSheet("color: #f80;")
        else:
            self.period_note_label.setText("")
            self.period_note_label.setStyleSheet("color: #888;")

    def check_xtquant_connection(self):
        """检测 miniQMT 连接状态"""
        self.check_connection_btn.setEnabled(False)
        self.xtquant_status_label.setText("miniQMT 状态: 检测中...")
        
        # 强制刷新界面
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        
        if not HAS_XTQUANT_MODULE:
            self.xtquant_status_label.setText("miniQMT 状态: xtquant 模块未找到")
            self.xtquant_status_label.setStyleSheet("color: red;")
            self.check_connection_btn.setEnabled(True)
            return
        
        if not fetch_kline_xtquant.check_xtquant_available():
            self.xtquant_status_label.setText("miniQMT 状态: xtquant 未安装")
            self.xtquant_status_label.setStyleSheet("color: red;")
            self.check_connection_btn.setEnabled(True)
            return
        
        connected, msg = fetch_kline_xtquant.check_connection()
        
        if connected:
            self.xtquant_status_label.setText(f"miniQMT 状态: ✓ {msg}")
            self.xtquant_status_label.setStyleSheet("color: green;")
        else:
            self.xtquant_status_label.setText(f"miniQMT 状态: ✗ {msg}")
            self.xtquant_status_label.setStyleSheet("color: red;")
        
        self.check_connection_btn.setEnabled(True)

    def on_start_clicked(self):
        source = self.source_combo.currentData()
        
        # 验证输入
        if source == "tushare":
            token = self.token_edit.text().strip()
            if not token:
                QMessageBox.warning(self, "提示", "请输入 Tushare Token")
                return
        else:
            token = ""  # xtquant 不需要 token
        
        if self.start_btn.text() == "开始更新":
            self.start_btn.setText("停止")
            self._set_controls_enabled(False)
            self.log_text.clear()
            
            exclude_boards = []
            if self.exclude_gem_cb.isChecked():
                exclude_boards.append("gem")
            if self.exclude_star_cb.isChecked():
                exclude_boards.append("star")
            if self.exclude_bj_cb.isChecked():
                exclude_boards.append("bj")
                
            # 获取起始日期
            start_date = ""
            if self.full_update_cb.isChecked():
                start_date = self.start_date_edit.date().toString("yyyyMMdd")
            
            # 获取周期
            period = self.period_combo.currentData()
            
            self.start_update.emit(
                token, 
                self.full_update_cb.isChecked(), 
                exclude_boards, 
                start_date,
                source,
                period
            )
        else:
            self.stop_update.emit()
            self.start_btn.setEnabled(False)
            self.append_log("正在停止...")

    def _set_controls_enabled(self, enabled: bool):
        """设置控件启用状态"""
        self.source_combo.setEnabled(enabled)
        self.token_edit.setEnabled(enabled)
        self.check_connection_btn.setEnabled(enabled)
        self.period_combo.setEnabled(enabled)
        self.full_update_cb.setEnabled(enabled)
        self.start_date_edit.setEnabled(enabled)
        self.exclude_gem_cb.setEnabled(enabled)
        self.exclude_star_cb.setEnabled(enabled)
        self.exclude_bj_cb.setEnabled(enabled)
        self.close_btn.setEnabled(enabled)

    def update_progress(self, current, total, message):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"进度: {current}/{total}")

    def append_log(self, message):
        self.log_text.append(message)

    def on_full_update_toggled(self, state):
        """显示/隐藏起始日期选择器"""
        is_checked = state == Qt.CheckState.Checked.value
        self.start_date_label.setVisible(is_checked)
        self.start_date_edit.setVisible(is_checked)

    def on_finished(self, success, message):
        self.start_btn.setText("开始更新")
        self.start_btn.setEnabled(True)
        self._set_controls_enabled(True)
        
        if success:
            QMessageBox.information(self, "完成", message)
            self.append_log("更新完成")
        else:
            QMessageBox.critical(self, "错误", message)
            self.append_log(f"错误: {message}")
