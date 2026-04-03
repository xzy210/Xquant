from __future__ import annotations

import logging
from pathlib import Path

from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from common.broker_session_service import get_broker_session_service

logger = logging.getLogger(__name__)


class _ClientStatusWorker(QThread):
    finished_status = pyqtSignal(dict)
    failed_status = pyqtSignal(str)

    def __init__(self, broker, parent=None):
        super().__init__(parent)
        self.broker = broker

    def run(self):
        try:
            self.finished_status.emit(self.broker.get_client_status())
        except Exception as exc:
            self.failed_status.emit(str(exc))


class _ClientActionWorker(QThread):
    finished_action = pyqtSignal(str, bool, str, dict)
    failed_action = pyqtSignal(str, str)

    def __init__(self, broker, action: str, parent=None):
        super().__init__(parent)
        self.broker = broker
        self.action = action

    def run(self):
        try:
            if self.action == "launch":
                ok, message, status = self.broker.launch_client()
            elif self.action == "login":
                ok, message, status = self.broker.login_client()
            elif self.action == "close":
                if self.broker.is_connected:
                    self.broker.disconnect()
                ok, message, status = self.broker.close_client()
            else:
                raise ValueError(f"unsupported action: {self.action}")
            self.finished_action.emit(self.action, ok, message, status)
        except Exception as exc:
            self.failed_action.emit(self.action, str(exc))


class BrokerConnectionPanel(QGroupBox):
    broker_connected = pyqtSignal()
    broker_disconnected = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setFlat(True)
        self.broker = get_broker_session_service()
        self._status_worker = None
        self._action_worker = None
        self._was_connected = bool(self.broker.is_connected)
        self._setup_ui()
        self._load_config()
        self.broker.connection_changed.connect(self._on_connection_changed)
        self.broker.config_changed.connect(self._on_config_changed)
        self.broker.client_state_changed.connect(self._on_client_state_changed)
        self._apply_connection_ui(self.broker.is_connected, "券商会话已连接" if self.broker.is_connected else "券商已断开")
        self._refresh_client_status_safe()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        self.panel_title = QLabel("miniQMT")
        self.panel_title.setStyleSheet("font-weight:bold;color:#3B82F6;")
        top_row.addWidget(self.panel_title)

        self.status_icon = QLabel("🔴")
        self.status_icon.setFixedWidth(16)
        top_row.addWidget(self.status_icon)
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("font-weight: bold;")
        top_row.addWidget(self.status_label)

        self.client_status_label = QLabel("客户端: 未检测")
        self.client_status_label.setWordWrap(False)
        self.client_status_label.setStyleSheet("color:#888;")
        top_row.addWidget(self.client_status_label)

        self.config_summary_label = QLabel("配置: 未设置")
        self.config_summary_label.setStyleSheet("color:#666;")
        self.config_summary_label.setWordWrap(False)
        top_row.addWidget(self.config_summary_label, 1)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(4)
        self.launch_btn = QPushButton("启动")
        self.launch_btn.setFixedHeight(24)
        self.launch_btn.setMinimumWidth(44)
        self.launch_btn.clicked.connect(self._on_launch_clicked)
        action_row.addWidget(self.launch_btn)

        self.login_btn = QPushButton("登录")
        self.login_btn.setFixedHeight(24)
        self.login_btn.setMinimumWidth(44)
        self.login_btn.clicked.connect(self._on_login_clicked)
        action_row.addWidget(self.login_btn)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setFixedHeight(24)
        self.close_btn.setMinimumWidth(44)
        self.close_btn.clicked.connect(self._on_close_clicked)
        action_row.addWidget(self.close_btn)

        self.connect_btn = QPushButton("连接")
        self.connect_btn.setFixedHeight(24)
        self.connect_btn.setMinimumWidth(52)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        action_row.addWidget(self.connect_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(24, 24)
        self.settings_btn.setToolTip("展开/收起连接设置")
        self.settings_btn.clicked.connect(self._toggle_settings)
        action_row.addWidget(self.settings_btn)

        top_row.addLayout(action_row)
        layout.addLayout(top_row)

        self.settings_widget = QWidget()
        settings_layout = QVBoxLayout(self.settings_widget)
        settings_layout.setContentsMargins(0, 2, 0, 0)
        settings_layout.setSpacing(4)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.addWidget(QLabel("路径:"))
        self.edit_qmt_path = QLineEdit()
        self.edit_qmt_path.setPlaceholderText(r"例: D:\中金财富QMT个人版交易端\userdata_mini")
        self.edit_qmt_path.textChanged.connect(self._refresh_config_summary)
        path_row.addWidget(self.edit_qmt_path)
        browse_btn = QPushButton("…")
        browse_btn.setFixedSize(24, 24)
        browse_btn.clicked.connect(self._on_browse_qmt_path)
        path_row.addWidget(browse_btn)
        settings_layout.addLayout(path_row)

        account_row = QHBoxLayout()
        account_row.setContentsMargins(0, 0, 0, 0)
        account_row.addWidget(QLabel("账户:"))
        self.edit_account = QLineEdit()
        self.edit_account.setPlaceholderText("资金账号")
        self.edit_account.textChanged.connect(self._refresh_config_summary)
        account_row.addWidget(self.edit_account)
        settings_layout.addLayout(account_row)

        self.settings_widget.setVisible(False)
        layout.addWidget(self.settings_widget)

    def _load_config(self):
        config = self.broker.get_config()
        self.edit_qmt_path.setText(config.get("qmt_path", ""))
        self.edit_account.setText(config.get("account", ""))
        self._refresh_config_summary()

    def _on_config_changed(self, config: dict):
        self.edit_qmt_path.setText(config.get("qmt_path", ""))
        self.edit_account.setText(config.get("account", ""))
        self._refresh_config_summary()

    def _refresh_config_summary(self):
        qmt_path = self.edit_qmt_path.text().strip()
        account = self.edit_account.text().strip()
        folder_name = Path(qmt_path).name if qmt_path else "未设置"
        summary = f"配置: {account or '-'} @ {folder_name}"
        self.config_summary_label.setText(summary)
        tooltip = f"账号: {account or '-'}\n路径: {qmt_path or '-'}"
        self.config_summary_label.setToolTip(tooltip)

    def _toggle_settings(self):
        self.settings_widget.setVisible(not self.settings_widget.isVisible())

    def _on_browse_qmt_path(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "选择 miniQMT userdata_mini 目录",
            self.edit_qmt_path.text() or "C:\\",
        )
        if path:
            self.edit_qmt_path.setText(path)
            self._refresh_config_summary()

    def _on_connect_clicked(self):
        if self.broker.is_connected:
            self.broker.disconnect()
            return
        qmt_path = self.edit_qmt_path.text().strip()
        account = self.edit_account.text().strip()
        if not qmt_path:
            self.settings_widget.setVisible(True)
            QMessageBox.warning(self, "提示", "请先填写 miniQMT 路径")
            return
        if not account:
            self.settings_widget.setVisible(True)
            QMessageBox.warning(self, "提示", "请先填写资金账号")
            return
        self.broker.save_config({"qmt_path": qmt_path, "account": account})
        started = self.broker.connect_async(qmt_path, account)
        if not started:
            self.connect_btn.setEnabled(True)

    def _on_connection_changed(self, connected: bool, message: str):
        self._apply_connection_ui(connected, message)
        if connected and not self._was_connected:
            self._was_connected = True
            self.settings_widget.setVisible(False)
            self.broker_connected.emit()
        elif not connected and self._was_connected and message not in ("正在连接券商...", "券商连接正在进行中"):
            self._was_connected = False
            self.broker_disconnected.emit()

    def _apply_connection_ui(self, connected: bool, message: str):
        if connected:
            account = self.broker.get_config().get("account", "")
            self.status_icon.setText("🟢")
            self.status_label.setText(f"已连接 {account}".strip())
            self.status_label.setStyleSheet("font-weight:bold;color:#16A34A;")
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText("断开")
            return
        if message in ("正在连接券商...", "券商连接正在进行中"):
            self.status_icon.setText("🟡")
            self.status_label.setText("连接中...")
            self.status_label.setStyleSheet("font-weight:bold;color:#D97706;")
            self.connect_btn.setEnabled(False)
            self.connect_btn.setText("连接中...")
            return
        self.status_icon.setText("🔴")
        self.status_label.setText("未连接")
        self.status_label.setStyleSheet("font-weight:bold;color:#888;")
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("连接")

    def _on_client_state_changed(self, _state: dict):
        self._refresh_client_status_safe()

    def _refresh_client_status_safe(self):
        if self._status_worker and self._status_worker.isRunning():
            return
        self._status_worker = _ClientStatusWorker(self.broker, parent=self)
        self._status_worker.finished_status.connect(self._apply_client_status)
        self._status_worker.failed_status.connect(self._apply_client_status_error)
        self._status_worker.start()

    def _apply_client_status(self, status: dict):
        message = status.get("message", "未检测")
        self.client_status_label.setText(f"客户端: {message}")
        login_visible = bool(status.get("login_window_visible"))
        running = bool(status.get("running"))
        self.launch_btn.setEnabled(not running)
        self.login_btn.setEnabled(running)
        self.close_btn.setEnabled(running)
        if login_visible:
            self.client_status_label.setStyleSheet("color:#D97706;")
        elif running:
            self.client_status_label.setStyleSheet("color:#16A34A;")
        else:
            self.client_status_label.setStyleSheet("color:#888;")
        self._status_worker = None

    def _apply_client_status_error(self, message: str):
        logger.warning("刷新 QMT 客户端状态失败: %s", message)
        self.client_status_label.setText("客户端: 状态检测失败")
        self.client_status_label.setStyleSheet("color:#DC2626;")
        self._status_worker = None

    def _on_launch_clicked(self):
        self._run_client_action("launch", "正在启动 miniQMT...")

    def _on_login_clicked(self):
        self._run_client_action("login", "正在登录 miniQMT...")

    def _on_close_clicked(self):
        self._run_client_action("close", "正在关闭 miniQMT...")

    def _run_client_action(self, action: str, pending_text: str):
        if self._action_worker and self._action_worker.isRunning():
            return
        self.launch_btn.setEnabled(False)
        self.login_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.client_status_label.setText(f"客户端: {pending_text}")
        self.client_status_label.setStyleSheet("color:#D97706;")
        self._action_worker = _ClientActionWorker(self.broker, action, parent=self)
        self._action_worker.finished_action.connect(self._on_client_action_finished)
        self._action_worker.failed_action.connect(self._on_client_action_failed)
        self._action_worker.start()

    def _on_client_action_finished(self, _action: str, success: bool, message: str, _status: dict):
        self._action_worker = None
        self.client_status_label.setText(f"客户端: {message}")
        self.client_status_label.setStyleSheet("color:#16A34A;" if success else "color:#DC2626;")
        self._refresh_client_status_safe()

    def _on_client_action_failed(self, _action: str, message: str):
        self._action_worker = None
        self.client_status_label.setText(f"客户端: {message}")
        self.client_status_label.setStyleSheet("color:#DC2626;")
        self._refresh_client_status_safe()

    def show_client_workflow_status(self, message: str, *, success: Optional[bool] = None):
        self.client_status_label.setText(f"客户端: {message}")
        if success is True:
            self.client_status_label.setStyleSheet("color:#16A34A;")
        elif success is False:
            self.client_status_label.setStyleSheet("color:#DC2626;")
        else:
            self.client_status_label.setStyleSheet("color:#D97706;")

    def refresh_client_status(self):
        self._refresh_client_status_safe()
