# scheduled_task_dialog.py - 定时任务配置对话框
"""
用于配置自动数据更新的时间及参数

注意：选股功能已迁移到 strategy_app，此对话框仅保留数据更新配置
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QCheckBox, QTimeEdit,
    QGroupBox, QMessageBox, QComboBox, QTextEdit, QFrame,
    QTabWidget, QWidget
)
from PyQt6.QtCore import Qt, QTime
from pathlib import Path
import sys

# 添加父目录到路径
current_dir = Path(__file__).parent
parent_dir = current_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))


class ScheduledTaskDialog(QDialog):
    """定时任务配置对话框"""
    
    def __init__(self, scheduler_manager, parent=None):
        super().__init__(parent)
        self.sm = scheduler_manager
        self.setWindowTitle("定时任务配置")
        self.setMinimumWidth(500)
        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)
        
        # --- Tab 1: 数据更新任务 ---
        update_tab = QWidget()
        update_layout = QVBoxLayout(update_tab)
        
        # 基础配置
        base_group = QGroupBox("自动执行设置")
        base_layout = QVBoxLayout(base_group)
        self.update_enabled_cb = QCheckBox("启用每日数据更新任务")
        self.update_enabled_cb.setStyleSheet("font-weight: bold;")
        base_layout.addWidget(self.update_enabled_cb)
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("执行时间:"))
        self.update_time_edit = QTimeEdit()
        self.update_time_edit.setDisplayFormat("HH:mm")
        time_layout.addWidget(self.update_time_edit)
        time_layout.addStretch()
        base_layout.addLayout(time_layout)
        update_layout.addWidget(base_group)
        
        # 任务细节
        task_group = QGroupBox("更新内容")
        task_layout = QVBoxLayout(task_group)
        self.step_update_cb = QCheckBox("更新日线数据")
        self.step_update_cb.setChecked(True)
        task_layout.addWidget(self.step_update_cb)
        update_layout.addWidget(task_group)
        
        update_run_now_btn = QPushButton("立即执行更新任务")
        update_run_now_btn.clicked.connect(lambda: self.run_task_now("update"))
        update_layout.addWidget(update_run_now_btn)
        update_layout.addStretch()
        self.tab_widget.addTab(update_tab, "每日数据更新")
        
        # --- Tab 2: 全量更新任务 ---
        maint_tab = QWidget()
        maint_layout = QVBoxLayout(maint_tab)
        
        maint_base_group = QGroupBox("维护设置")
        maint_base_layout = QVBoxLayout(maint_base_group)
        self.maint_enabled_cb = QCheckBox("启用全量更新任务")
        self.maint_enabled_cb.setStyleSheet("font-weight: bold;")
        maint_base_layout.addWidget(self.maint_enabled_cb)
        maint_time_layout = QHBoxLayout()
        maint_time_layout.addWidget(QLabel("执行时间:"))
        self.maint_time_edit = QTimeEdit()
        self.maint_time_edit.setDisplayFormat("HH:mm")
        maint_time_layout.addWidget(self.maint_time_edit)
        maint_time_layout.addStretch()
        maint_base_layout.addLayout(maint_time_layout)
        maint_layout.addWidget(maint_base_group)
        
        maint_task_group = QGroupBox("更新内容")
        maint_task_layout = QVBoxLayout(maint_task_group)
        maint_task_layout.addWidget(QLabel("类型: 强制全量更新所有股票"))
        maint_start_date_layout = QHBoxLayout()
        maint_start_date_layout.addWidget(QLabel("起始日期:"))
        self.maint_start_date_edit = QLineEdit()
        self.maint_start_date_edit.setPlaceholderText("YYYYMMDD")
        maint_start_date_layout.addWidget(self.maint_start_date_edit)
        maint_task_layout.addLayout(maint_start_date_layout)
        maint_layout.addWidget(maint_task_group)
        
        maint_run_now_btn = QPushButton("立即执行全量更新")
        maint_run_now_btn.clicked.connect(lambda: self.run_task_now("maintenance"))
        maint_layout.addWidget(maint_run_now_btn)
        maint_layout.addStretch()
        self.tab_widget.addTab(maint_tab, "全量数据更新")
        
        # --- 公共配置 (数据源) ---
        common_group = QGroupBox("公共数据源配置")
        common_layout = QVBoxLayout(common_group)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("数据源:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("miniQMT (xtquant)", "xtquant")
        self.source_combo.addItem("Tushare", "tushare")
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        source_row.addWidget(self.source_combo)
        common_layout.addLayout(source_row)
        self.token_layout = QHBoxLayout()
        self.token_layout.addWidget(QLabel("Tushare Token:"))
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_layout.addWidget(self.token_edit)
        common_layout.addLayout(self.token_layout)
        layout.addWidget(common_group)
        
        # 日志区域
        log_group = QGroupBox("任务运行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMinimumHeight(150)
        self.log_display.setStyleSheet("background-color: #1e1e1e; color: #dcdcdc; font-family: 'Consolas';")
        log_layout.addWidget(self.log_display)
        layout.addWidget(log_group)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("保存配置")
        save_btn.setFixedWidth(100)
        save_btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold;")
        save_btn.clicked.connect(self.save_settings)
        btn_layout.addWidget(save_btn)
        cancel_btn = QPushButton("关闭")
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.sm.task_log.connect(self.append_log)
        self.sm.task_finished.connect(self.on_task_finished)

    def load_settings(self):
        config = self.sm.config
        
        # 数据更新任务（原选股推送任务改为数据更新）
        self.update_enabled_cb.setChecked(config.get("update_enabled", False))
        h, m = map(int, config.get("update_time", "14:30").split(':'))
        self.update_time_edit.setTime(QTime(h, m))
        self.step_update_cb.setChecked(config.get("step_update", True))
            
        # 维护任务
        self.maint_enabled_cb.setChecked(config.get("maint_enabled", False))
        mh, mm = map(int, config.get("maint_time", "18:00").split(':'))
        self.maint_time_edit.setTime(QTime(mh, mm))
        self.maint_start_date_edit.setText(config.get("maint_start_date", "20080101"))
        
        # 公共配置
        data_source = config.get("data_source", "xtquant")
        idx = self.source_combo.findData(data_source)
        if idx >= 0: self.source_combo.setCurrentIndex(idx)
        self.token_edit.setText(config.get("tushare_token", ""))
        self.on_source_changed()

    def on_source_changed(self):
        is_tushare = self.source_combo.currentData() == "tushare"
        for i in range(self.token_layout.count()):
            w = self.token_layout.itemAt(i).widget()
            if w: w.setVisible(is_tushare)

    def save_settings(self):
        config = {
            "update_enabled": self.update_enabled_cb.isChecked(),
            "update_time": self.update_time_edit.time().toString("HH:mm"),
            "step_update": self.step_update_cb.isChecked(),
            
            "maint_enabled": self.maint_enabled_cb.isChecked(),
            "maint_time": self.maint_time_edit.time().toString("HH:mm"),
            "maint_start_date": self.maint_start_date_edit.text().strip(),
            
            "data_source": self.source_combo.currentData(),
            "tushare_token": self.token_edit.text().strip()
        }
        self.sm.save_config(config)
        QMessageBox.information(self, "成功", "定时任务配置已保存")

    def run_task_now(self, task_id):
        success, msg = self.sm.run_now(task_id)
        if not success:
            QMessageBox.warning(self, "提示", msg)
        else:
            self.append_log(f"--- 手动触发 [{task_id}] 任务启动 ---")

    def append_log(self, text):
        self.log_display.append(text)

    def on_task_finished(self, success, message):
        self.append_log(f"--- 任务结束: {'成功' if success else '失败'} ({message}) ---")

    def set_dark_style(self):
        self.setStyleSheet("""
            QDialog { background-color: #252526; color: #ffffff; }
            QGroupBox { color: #0078d4; font-weight: bold; border: 1px solid #3c3c3c; margin-top: 10px; padding-top: 10px; }
            QLabel { color: #dcdcdc; }
            QCheckBox { color: #dcdcdc; }
            QPushButton { padding: 5px 15px; border-radius: 4px; background-color: #3c3c3c; color: white; }
            QPushButton:hover { background-color: #454545; }
            QComboBox, QTimeEdit, QLineEdit { background-color: #1e1e1e; color: white; border: 1px solid #3c3c3c; padding: 3px; }
            QTabWidget::pane { border: 1px solid #3c3c3c; }
            QTabBar::tab { background: #2d2d2d; color: #aaa; padding: 8px 15px; border: 1px solid #3c3c3c; border-bottom: none; }
            QTabBar::tab:selected { background: #3c3c3c; color: white; }
        """)
