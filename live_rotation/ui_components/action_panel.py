"""Action controls for the ETF rotation live panel."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout


class ETFRotationActionPanel(QGroupBox):
    """Small action panel that delegates all runtime actions to the parent."""

    check_signal_requested = pyqtSignal()
    execute_signal_requested = pyqtSignal()
    schedule_settings_requested = pyqtSignal()
    config_requested = pyqtSignal()
    manual_order_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__("操作", parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        section_title_style = "color:#94A3B8;font-size:11px;font-weight:bold;"
        utility_btn_min_width = 112
        utility_btn_height = 30
        manual_btn_height = 30

        main_label = QLabel("主操作", self)
        main_label.setStyleSheet(section_title_style)
        layout.addWidget(main_label)

        row1 = QHBoxLayout()
        row1.setSpacing(6)

        self.btn_check = QPushButton("计算信号", self)
        self.btn_check.setToolTip("仅计算信号，交易由实盘策略中枢统一执行")
        self.btn_check.clicked.connect(self.check_signal_requested.emit)
        self.btn_check.setMinimumHeight(36)
        self.btn_check.setStyleSheet(
            "QPushButton{background:#3B82F6;color:white;padding:8px 16px;"
            "border-radius:5px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#2563EB;}"
        )
        row1.addWidget(self.btn_check)

        self.btn_execute = QPushButton("生成执行信号", self)
        self.btn_execute.setToolTip("生成统一执行信号，交易由实盘策略中枢提交")
        self.btn_execute.clicked.connect(self.execute_signal_requested.emit)
        self.btn_execute.setMinimumHeight(36)
        self.btn_execute.setStyleSheet(
            "QPushButton{background:#DC2626;color:white;padding:8px 16px;"
            "border-radius:5px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#B91C1C;}"
        )
        row1.addWidget(self.btn_execute)
        layout.addLayout(row1)

        layout.addWidget(self._separator())

        settings_label = QLabel("设置", self)
        settings_label.setStyleSheet(section_title_style)
        layout.addWidget(settings_label)

        self.lbl_auto_status = QLabel("定时任务: 未启用", self)
        self.lbl_auto_status.setStyleSheet("color:#6B7B8D;font-size:11px;")
        layout.addWidget(self.lbl_auto_status)

        config_ctl_row = QHBoxLayout()
        config_ctl_row.setSpacing(6)

        self.btn_toggle_schedule = QPushButton("⏰ 定时任务", self)
        self.btn_toggle_schedule.setToolTip("打开 ETF 自动调度配置")
        self.btn_toggle_schedule.clicked.connect(self.schedule_settings_requested.emit)
        self.btn_toggle_schedule.setMinimumWidth(utility_btn_min_width)
        self.btn_toggle_schedule.setMinimumHeight(utility_btn_height)
        self.btn_toggle_schedule.setStyleSheet(
            "QPushButton{background:#0EA5E9;color:white;padding:5px 10px;"
            "border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#0284C7;}"
        )
        config_ctl_row.addWidget(self.btn_toggle_schedule)

        self.btn_toggle_config = QPushButton("⚙ 查看配置", self)
        self.btn_toggle_config.setToolTip("打开 ETF 轮动实盘配置弹窗（默认只读）")
        self.btn_toggle_config.clicked.connect(self.config_requested.emit)
        self.btn_toggle_config.setMinimumWidth(utility_btn_min_width)
        self.btn_toggle_config.setMinimumHeight(utility_btn_height)
        self.btn_toggle_config.setStyleSheet(
            "QPushButton{background:#6366F1;color:white;padding:5px 10px;"
            "border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#4F46E5;}"
        )
        config_ctl_row.addWidget(self.btn_toggle_config)
        layout.addLayout(config_ctl_row)

        layout.addWidget(self._separator())

        manual_label = QLabel("手动干预", self)
        manual_label.setStyleSheet(section_title_style)
        layout.addWidget(manual_label)

        row3 = QHBoxLayout()
        row3.setSpacing(6)
        self.btn_manual_sell = QPushButton("手动委托", self)
        self.btn_manual_sell.clicked.connect(self.manual_order_requested.emit)
        self.btn_manual_sell.setMinimumHeight(manual_btn_height)
        self.btn_manual_sell.setStyleSheet(
            "QPushButton{background:#2d2d2d;color:#ffffff;padding:6px 10px;"
            "border:1px solid #3c3c3c;border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#3c3c3c;}"
        )
        row3.addWidget(self.btn_manual_sell)
        layout.addLayout(row3)

    def _separator(self) -> QFrame:
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color:#3c3c3c;")
        return separator

    def set_check_running(self, running: bool) -> None:
        self.btn_check.setEnabled(not running)
        self.btn_check.setText("计算中..." if running else "计算信号")

    def set_execute_running(self, running: bool) -> None:
        self.btn_execute.setEnabled(not running)
        self.btn_execute.setText("计算中..." if running else "生成执行信号")

    def set_auto_status(self, text: str, style_sheet: str) -> None:
        self.lbl_auto_status.setText(text)
        self.lbl_auto_status.setStyleSheet(style_sheet)

    def auto_status_text(self) -> str:
        return self.lbl_auto_status.text()
