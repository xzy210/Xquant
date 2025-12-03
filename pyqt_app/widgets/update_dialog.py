from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QProgressBar, QTextEdit, QPushButton, QLineEdit,
    QCheckBox, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal

class UpdateDialog(QDialog):
    start_update = pyqtSignal(str, bool, list)  # token, full_update, exclude_boards
    stop_update = pyqtSignal()

    def __init__(self, parent=None, default_token=""):
        super().__init__(parent)
        self.setWindowTitle("更新股票数据")
        self.setMinimumSize(500, 450)
        self.setupUI(default_token)

    def setupUI(self, default_token):
        layout = QVBoxLayout(self)

        # Token input
        token_layout = QHBoxLayout()
        token_layout.addWidget(QLabel("Tushare Token:"))
        self.token_edit = QLineEdit(default_token)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        token_layout.addWidget(self.token_edit)
        layout.addLayout(token_layout)

        # Options
        self.full_update_cb = QCheckBox("强制全量更新 (较慢)")
        layout.addWidget(self.full_update_cb)

        # Exclude boards options
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
        
        layout.addLayout(exclude_layout)

        # Progress
        self.status_label = QLabel("准备就绪")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Log area
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        # Buttons
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始更新")
        self.start_btn.clicked.connect(self.on_start_clicked)
        btn_layout.addWidget(self.start_btn)

        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

    def on_start_clicked(self):
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "提示", "请输入 Tushare Token")
            return
        
        if self.start_btn.text() == "开始更新":
            self.start_btn.setText("停止")
            self.token_edit.setEnabled(False)
            self.full_update_cb.setEnabled(False)
            self.exclude_gem_cb.setEnabled(False)
            self.exclude_star_cb.setEnabled(False)
            self.exclude_bj_cb.setEnabled(False)
            self.close_btn.setEnabled(False)
            self.log_text.clear()
            
            exclude_boards = []
            if self.exclude_gem_cb.isChecked():
                exclude_boards.append("gem")
            if self.exclude_star_cb.isChecked():
                exclude_boards.append("star")
            if self.exclude_bj_cb.isChecked():
                exclude_boards.append("bj")
                
            self.start_update.emit(token, self.full_update_cb.isChecked(), exclude_boards)
        else:
            self.stop_update.emit()
            self.start_btn.setEnabled(False)
            self.append_log("正在停止...")

    def update_progress(self, current, total, message):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"进度: {current}/{total}")
        # self.append_log(message) # Optional: too many logs might slow down UI

    def append_log(self, message):
        self.log_text.append(message)

    def on_finished(self, success, message):
        self.start_btn.setText("开始更新")
        self.start_btn.setEnabled(True)
        self.token_edit.setEnabled(True)
        self.full_update_cb.setEnabled(True)
        self.exclude_gem_cb.setEnabled(True)
        self.exclude_star_cb.setEnabled(True)
        self.exclude_bj_cb.setEnabled(True)
        self.close_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "完成", message)
            self.append_log("更新完成")
        else:
            QMessageBox.critical(self, "错误", message)
            self.append_log(f"错误: {message}")
