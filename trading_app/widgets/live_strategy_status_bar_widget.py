from __future__ import annotations

from typing import Callable, Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)


_MODE_DISPLAY = {
    "off": "关闭",
    "shadow": "影子",
    "live": "实盘",
}


class LiveStrategyStatusBarWidget(QFrame):
    """Compact always-on status bar replacing the former Overview tab.

    Subscribes to ``HubStateService.state_changed`` to refresh its indicators.
    Emits ``navigate_requested(str)`` when the user clicks status chips that
    should jump to a specific tab (e.g. the pending alert counter).
    """

    navigate_requested = pyqtSignal(str)
    mode_change_requested = pyqtSignal(str)
    emergency_pause_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._suppress_mode_signal = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(12)

        self.lbl_connectivity = QLabel("连通: -")
        layout.addWidget(self.lbl_connectivity)

        layout.addWidget(self._separator())

        layout.addWidget(QLabel("执行模式:"))
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("关闭", "off")
        self.mode_combo.addItem("影子", "shadow")
        self.mode_combo.addItem("实盘", "live")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self.mode_combo)

        layout.addWidget(self._separator())

        self.alert_btn = QPushButton("未处理告警: 0")
        self.alert_btn.setFlat(True)
        self.alert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.alert_btn.clicked.connect(lambda: self.navigate_requested.emit("alerts"))
        layout.addWidget(self.alert_btn)

        self.exception_btn = QPushButton("异常订单: 0")
        self.exception_btn.setFlat(True)
        self.exception_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.exception_btn.clicked.connect(lambda: self.navigate_requested.emit("exceptions"))
        layout.addWidget(self.exception_btn)

        layout.addWidget(self._separator())

        self.lbl_eod = QLabel("今日日终: -")
        layout.addWidget(self.lbl_eod)

        layout.addStretch()

        self.lbl_updated_at = QLabel("-")
        self.lbl_updated_at.setStyleSheet("color:#888;font-size:11px;")
        layout.addWidget(self.lbl_updated_at)

        self.pause_btn = QPushButton("紧急暂停自动化")
        self.pause_btn.setStyleSheet("background:#d9534f;color:#fff;padding:2px 10px;border-radius:3px;")
        self.pause_btn.clicked.connect(self.emergency_pause_requested.emit)
        layout.addWidget(self.pause_btn)

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _on_mode_changed(self, _index: int) -> None:
        if self._suppress_mode_signal:
            return
        mode = str(self.mode_combo.currentData() or "off")
        self.mode_change_requested.emit(mode)

    def refresh_view(self, state: dict) -> None:
        state = dict(state or {})

        qmt_running = bool(state.get("qmt_running", False))
        broker_connected = bool(state.get("broker_connected", False))
        startup_running = bool(state.get("startup_running", False))

        if startup_running:
            connectivity_text = "连通: 自检中"
            connectivity_color = "#eab308"
        elif broker_connected and qmt_running:
            connectivity_text = "连通: 券商✓ QMT✓"
            connectivity_color = "#16a34a"
        elif broker_connected:
            connectivity_text = "连通: 券商✓ QMT?"
            connectivity_color = "#eab308"
        elif qmt_running:
            connectivity_text = "连通: 券商✗ QMT✓"
            connectivity_color = "#eab308"
        else:
            connectivity_text = "连通: 未就绪"
            connectivity_color = "#d9534f"
        self.lbl_connectivity.setText(connectivity_text)
        self.lbl_connectivity.setStyleSheet(f"color:{connectivity_color};font-weight:bold;")

        mode = str(state.get("auto_trade_mode", "off") or "off").strip().lower()
        mode_index = self.mode_combo.findData(mode)
        if mode_index >= 0 and mode_index != self.mode_combo.currentIndex():
            self._suppress_mode_signal = True
            try:
                self.mode_combo.setCurrentIndex(mode_index)
            finally:
                self._suppress_mode_signal = False

        alert_counts = dict(state.get("alert_counts", {}) or {})
        open_count = int(alert_counts.get("open", 0) or 0)
        self.alert_btn.setText(f"未处理告警: {open_count}")
        self.alert_btn.setStyleSheet(
            "color:#d9534f;font-weight:bold;" if open_count > 0 else "color:#4caf50;"
        )

        exception_count = int(state.get("exception_order_count", 0) or 0)
        self.exception_btn.setText(f"异常订单: {exception_count}")
        self.exception_btn.setStyleSheet(
            "color:#d9534f;font-weight:bold;" if exception_count > 0 else "color:#4caf50;"
        )

        eod_state = dict(state.get("eod_state", {}) or {})
        eod_status = str(eod_state.get("status", "") or "idle")
        eod_error = str(eod_state.get("last_error", "") or "")
        eod_tip = f"今日日终: {eod_status}"
        if eod_status == "completed":
            self.lbl_eod.setStyleSheet("color:#16a34a;")
        elif eod_status == "failed" or eod_error:
            self.lbl_eod.setStyleSheet("color:#d9534f;")
            if eod_error:
                eod_tip += f" · {eod_error}"
        else:
            self.lbl_eod.setStyleSheet("color:#888;")
        self.lbl_eod.setText(eod_tip)

        self.lbl_updated_at.setText(f"更新: {state.get('updated_at', '-')}")
