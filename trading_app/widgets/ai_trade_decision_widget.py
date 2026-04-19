"""AI 交易决策中心 — 独立窗口

将 AI 决策分析、交易下单、账户信息三大功能聚合在同一面板中，
使用户无需在多个窗口间切换即可完成「分析 → 决策 → 执行 → 追踪」的完整流程。
"""
from __future__ import annotations

import json
import logging
import math
import os
from uuid import uuid4
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, QTime, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from common.live_strategy_shell import LiveStrategyShell
from common.scheduler_dialog_base import BaseSchedulerSettingsDialog
from common.strategy_config_dialog_base import BaseStrategyConfigDialog
from common.strategy_panel_context import StrategyPanelContext

try:
    from services.agent_context_service import (
        AgentContextService,
        AgentRuntimeContext,
        BrokerContext,
        SymbolContext,
        TASK_MODE_TRADE_DECISION,
    )
    from services.agent_prompt_builder import AgentPromptBuilder
    from services.agent_runtime import StockAgentRuntime
    from services.trade_decision_extractor import TradeDecisionExtractor
    from services.trade_decision_models import (
        DecisionOutcome,
        TRADE_ACTION_LABELS,
        TradeAction,
        TradeDecision,
    )
    from services.risk_guard_service import RiskGuardService
    from services.strategy_risk import get_strategy_risk_registry, is_configurable
    from services.decision_tracker_service import DecisionTrackerService
    from services.decision_run_context import DecisionRunContext, build_decision_run_context
    from services.daily_auto_trade_service import get_daily_auto_trade_service
    from services.auto_trade_config_service import get_auto_trade_config_service
    from services.stock_pool_service import get_stock_pool_service
    from services.strategy_budget_service import get_strategy_budget_service
    from services.strategy_constants import AI_STOCK_STRATEGY_ID, AI_STOCK_STRATEGY_NAME, AI_STOCK_VIRTUAL_ACCOUNT_ID
    from services.strategy_registry_service import get_strategy_registry_service
    from services.trade_execution_service import ExecutionRequest, get_trade_execution_service
    from services.trade_record_service import TradeSource
    from services.live_strategy_end_of_day_service import StrategyEndOfDayResult
    from common.broker_session_service import get_broker_session_service
    from watchlist_manager import WatchlistManager
except ImportError:
    from trading_app.services.agent_context_service import (
        AgentContextService,
        AgentRuntimeContext,
        BrokerContext,
        SymbolContext,
        TASK_MODE_TRADE_DECISION,
    )
    from trading_app.services.agent_prompt_builder import AgentPromptBuilder
    from trading_app.services.agent_runtime import StockAgentRuntime
    from trading_app.services.trade_decision_extractor import TradeDecisionExtractor
    from trading_app.services.trade_decision_models import (
        DecisionOutcome,
        TRADE_ACTION_LABELS,
        TradeAction,
        TradeDecision,
    )
    from trading_app.services.risk_guard_service import RiskGuardService
    from trading_app.services.strategy_risk import get_strategy_risk_registry, is_configurable
    from trading_app.services.decision_tracker_service import DecisionTrackerService
    from trading_app.services.decision_run_context import DecisionRunContext, build_decision_run_context
    from trading_app.services.daily_auto_trade_service import get_daily_auto_trade_service
    from trading_app.services.auto_trade_config_service import get_auto_trade_config_service
    from trading_app.services.stock_pool_service import get_stock_pool_service
    from trading_app.services.strategy_budget_service import get_strategy_budget_service
    from trading_app.services.strategy_constants import AI_STOCK_STRATEGY_ID, AI_STOCK_STRATEGY_NAME, AI_STOCK_VIRTUAL_ACCOUNT_ID
    from trading_app.services.strategy_registry_service import get_strategy_registry_service
    from trading_app.services.trade_execution_service import ExecutionRequest, get_trade_execution_service
    from trading_app.services.trade_record_service import TradeSource
    from trading_app.services.live_strategy_end_of_day_service import StrategyEndOfDayResult
    from trading_app.common.broker_session_service import get_broker_session_service
    from trading_app.watchlist_manager import WatchlistManager

from trading_app.widgets.strategy_risk_settings_panel import StrategyRiskSettingsPanel

logger = logging.getLogger(__name__)

DECISION_MODE_POSITION_SCAN = "position_scan"
DECISION_MODE_CANDIDATE_POOL_SCAN = "candidate_pool_scan"
SCAN_SUBAGENT_CONCURRENCY = 3
SCAN_SUBAGENT_REQUEST_TIMEOUT_SECONDS = 120.0


class _StatusMessageProxy:
    def __init__(self, owner: QWidget):
        self.owner = owner

    def showMessage(self, message: str):
        logger.info("AI trade panel status: %s", message)


# ---------------------------------------------------------------------------
#  Helper: reuse ChatThread from ai_agent_widget to avoid duplication
# ---------------------------------------------------------------------------
def _get_chat_thread_class():
    try:
        from widgets.ai_agent_widget import ChatThread
    except ImportError:
        from trading_app.widgets.ai_agent_widget import ChatThread
    return ChatThread


class _AccountRefreshWorker(QThread):
    refresh_ready = pyqtSignal(object, object)
    refresh_failed = pyqtSignal(str)

    def __init__(self, broker, parent=None):
        super().__init__(parent)
        self.broker = broker

    def run(self):
        try:
            asset = self.broker.query_stock_asset()
            positions = self.broker.query_stock_positions() or []
            asset_payload = {
                "total_asset": float(getattr(asset, "total_asset", 0) or 0.0),
                "cash": float(getattr(asset, "cash", 0) or getattr(asset, "available_cash", 0) or 0.0),
                "market_value": float(getattr(asset, "market_value", 0) or 0.0),
            }
            position_payloads = [
                {
                    "stock_code": str(getattr(pos, "stock_code", "") or ""),
                    "stock_name": str(getattr(pos, "stock_name", "") or ""),
                    "volume": int(getattr(pos, "volume", 0) or 0),
                    "can_use_volume": int(getattr(pos, "can_use_volume", 0) or 0),
                    "open_price": float(getattr(pos, "open_price", 0) or 0.0),
                    "market_value": float(getattr(pos, "market_value", 0) or 0.0),
                    "profit_rate": float(getattr(pos, "profit_rate", 0) or 0.0),
                }
                for pos in positions
            ]
            self.refresh_ready.emit(asset_payload, position_payloads)
        except Exception as exc:
            self.refresh_failed.emit(str(exc))


class CollapsibleStepCard(QWidget):
    """A small collapsible card used to display one summarized progress step."""

    STATUS_STYLES = {
        "pending": ("●", "#888888", "#242424"),
        "running": ("◔", "#0078d4", "#1c2733"),
        "done": ("●", "#107c10", "#1f2a1f"),
        "warning": ("●", "#d8a300", "#322b17"),
    }

    def __init__(
        self,
        title: str,
        detail: str = "",
        status: str = "pending",
        parent=None,
        *,
        action_label: str = "",
        action_callback=None,
        preview_path: str = "",
    ):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._action_callback = None
        self._preview_path = ""
        self._setup_ui()
        self.set_content(
            title,
            detail,
            status=status,
            action_label=action_label,
            action_callback=action_callback,
            preview_path=preview_path,
        )

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header_btn = QToolButton()
        self.header_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header_btn.setArrowType(Qt.ArrowType.RightArrow)
        self.header_btn.setCheckable(True)
        self.header_btn.setChecked(False)
        self.header_btn.clicked.connect(self._toggle_expanded)
        self.header_btn.setStyleSheet(
            """
            QToolButton {
                text-align: left;
                padding: 5px 8px;
                border: 1px solid #333333;
                border-bottom: none;
                font-weight: bold;
                color: #f0f0f0;
            }
            """
        )
        layout.addWidget(self.header_btn)

        self.detail_label = QTextEdit()
        self.detail_label.setReadOnly(True)
        self.detail_label.setVisible(False)
        self.detail_label.setMinimumHeight(0)
        self.detail_label.setMaximumHeight(320)
        self.detail_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.detail_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.detail_label.document().setDocumentMargin(2)
        self.detail_label.setStyleSheet(
            """
            QTextEdit {
                color: #d0d0d0;
                padding: 3px 8px;
                border: 1px solid #333333;
                border-top: none;
                background-color: #171717;
                selection-background-color: #264f78;
            }
            """
        )
        layout.addWidget(self.detail_label)

        self.preview_label = QLabel("")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setVisible(False)
        self.preview_label.setStyleSheet(
            """
            QLabel {
                background-color: #111111;
                border: 1px solid #333333;
                border-top: none;
                padding: 8px;
            }
            """
        )
        layout.addWidget(self.preview_label)

        self.action_row = QWidget()
        action_layout = QHBoxLayout(self.action_row)
        action_layout.setContentsMargins(10, 0, 10, 8)
        action_layout.addStretch()
        self.action_btn = QPushButton("打开证据文件/图片")
        self.action_btn.setVisible(False)
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #2b579a;
                color: white;
                border: 1px solid #3d6db5;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #3568b2;
            }
            """
        )
        self.action_btn.clicked.connect(self._on_action_clicked)
        action_layout.addWidget(self.action_btn)
        layout.addWidget(self.action_row)
        self.action_row.setVisible(False)

        self.children_host = QWidget()
        self.children_layout = QVBoxLayout(self.children_host)
        self.children_layout.setContentsMargins(18, 2, 0, 0)
        self.children_layout.setSpacing(2)
        self.children_host.setVisible(False)
        layout.addWidget(self.children_host)

    def _toggle_expanded(self):
        expanded = self.header_btn.isChecked()
        self.header_btn.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.detail_label.setVisible(expanded)
        if expanded:
            self._adjust_detail_height()
        preview_pixmap = self.preview_label.pixmap()
        self.preview_label.setVisible(expanded and preview_pixmap is not None and not preview_pixmap.isNull())
        self.action_row.setVisible(expanded and self.action_btn.isVisible())
        self.children_host.setVisible(expanded and self.children_layout.count() > 0)

    def set_content(
        self,
        title: str,
        detail: str,
        *,
        status: str = "pending",
        action_label: str = "",
        action_callback=None,
        preview_path: str = "",
    ):
        self.title_text = title
        self.detail_text = detail or "无额外说明"
        self.status = status
        self._action_callback = action_callback
        self._preview_path = preview_path or ""
        dot, color, bg = self.STATUS_STYLES.get(status, self.STATUS_STYLES["pending"])
        self.header_btn.setText(f"{dot} {title}")
        self.header_btn.setStyleSheet(
            f"""
            QToolButton {{
                text-align: left;
                padding: 5px 8px;
                border: 1px solid #333333;
                border-bottom: none;
                font-weight: bold;
                color: {color};
                background-color: {bg};
            }}
            """
        )
        self.detail_label.setPlainText(self.detail_text)
        self._adjust_detail_height()
        QTimer.singleShot(0, self._adjust_detail_height)
        self.action_btn.setText(action_label or "打开证据文件/图片")
        self.action_btn.setVisible(callable(action_callback))
        self.action_row.setVisible(self.header_btn.isChecked() and self.action_btn.isVisible())
        self._update_preview()

    def showEvent(self, event):
        super().showEvent(event)
        if self.detail_label.isVisible():
            QTimer.singleShot(0, self._adjust_detail_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._adjust_detail_height)

    def _adjust_detail_height(self):
        if not hasattr(self, "detail_label"):
            return
        if not self.detail_label.isVisible():
            return
        viewport = self.detail_label.viewport()
        if viewport is None:
            return
        vp_width = viewport.width()
        if vp_width < 50:
            return
        content_text = self.detail_label.toPlainText() or ""
        width = vp_width - 8
        metrics = QFontMetrics(self.detail_label.font())
        rect = metrics.boundingRect(
            0,
            0,
            width,
            10000,
            Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs,
            content_text,
        )
        padding = self.detail_label.frameWidth() * 2 + 10
        target_height = rect.height() + padding
        target_height = max(22, min(320, target_height))
        self.detail_label.setFixedHeight(target_height)

    def _update_preview(self):
        if not self._preview_path or not os.path.exists(self._preview_path):
            self.preview_label.clear()
            self.preview_label.setVisible(False)
            return
        pixmap = QPixmap(self._preview_path)
        if pixmap.isNull():
            self.preview_label.clear()
            self.preview_label.setVisible(False)
            return
        scaled = pixmap.scaled(
            760,
            420,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)
        self.preview_label.setVisible(self.header_btn.isChecked())

    def expand(self):
        if not self.header_btn.isChecked():
            self.header_btn.click()
        QTimer.singleShot(0, self._adjust_detail_height)

    def clear_children(self):
        while self.children_layout.count():
            item = self.children_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.children_host.setVisible(False)

    def add_child_card(self, child_card: "CollapsibleStepCard"):
        self.children_layout.addWidget(child_card)
        if self.header_btn.isChecked():
            self.children_host.setVisible(True)

    def _on_action_clicked(self):
        if callable(self._action_callback):
            self._action_callback()


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
                raise RuntimeError(f"未知的客户端动作: {self.action}")
            self.finished_action.emit(self.action, ok, message, status)
        except Exception as exc:
            self.failed_action.emit(self.action, str(exc))


class _ReconcileCatchupWorker(QThread):
    finished_reconcile = pyqtSignal(bool, str)
    failed_reconcile = pyqtSignal(str)

    def __init__(self, daily_auto_trade, parent=None):
        super().__init__(parent)
        self.daily_auto_trade = daily_auto_trade

    def run(self):
        try:
            success, message = self.daily_auto_trade.run_reconcile_catchup_if_needed()
            self.finished_reconcile.emit(success, message)
        except Exception as exc:
            self.failed_reconcile.emit(str(exc))


# ───────────────────────────────────────────────────────────────────────────
#  Left panel: Account & Position overview
# ───────────────────────────────────────────────────────────────────────────
class AccountPanel(QWidget):
    """Compact account + position summary panel."""

    scheduler_settings_requested = pyqtSignal()
    manual_order_requested = pyqtSignal()

    def __init__(self, parent=None, *, show_connection_panel: bool = True, shared_broker_panel=None):
        super().__init__(parent)
        self.broker = get_broker_session_service()
        self.strategy_registry = get_strategy_registry_service()
        self.strategy_budget = get_strategy_budget_service()
        self.auto_trade_config_service = get_auto_trade_config_service()
        self.show_connection_panel = bool(show_connection_panel)
        self.shared_broker_panel = shared_broker_panel
        self._status_worker = None
        self._action_worker = None
        self._refresh_worker = None
        self._trade_config_loading = False
        self._config_dialog = None
        self._setup_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(30_000)
        self.broker.client_state_changed.connect(self._on_client_state_changed)
        # 避免在窗口构造阶段同步跑 pywinauto，先让主窗口显示出来。
        QTimer.singleShot(200, self._refresh_client_status_safe)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        section_title_style = "color:#94A3B8;font-size:11px;font-weight:bold;"

        # -- Connection status bar --
        self.connection_widget = QWidget(self)
        conn_group = QVBoxLayout(self.connection_widget)
        conn_group.setSpacing(6)
        conn_group.setContentsMargins(0, 0, 0, 0)

        conn_row = QHBoxLayout()
        self.status_icon = QLabel("🔴")
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("font-weight: bold;")
        conn_row.addWidget(self.status_icon)
        conn_row.addWidget(self.status_label)
        self.client_status_label = QLabel("客户端: 未检测")
        self.client_status_label.setStyleSheet("color: #888;")
        self.client_status_label.setWordWrap(True)
        conn_row.addWidget(self.client_status_label)
        conn_row.addStretch()
        conn_group.addLayout(conn_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.launch_btn = QPushButton("启动")
        self.launch_btn.setMinimumWidth(64)
        self.launch_btn.clicked.connect(self._on_launch_clicked)
        action_row.addWidget(self.launch_btn)
        self.login_btn = QPushButton("登录")
        self.login_btn.setMinimumWidth(64)
        self.login_btn.clicked.connect(self._on_login_clicked)
        action_row.addWidget(self.login_btn)
        self.close_btn = QPushButton("关闭")
        self.close_btn.setMinimumWidth(64)
        self.close_btn.clicked.connect(self._on_close_clicked)
        action_row.addWidget(self.close_btn)
        self.connect_btn = QPushButton("连接券商")
        self.connect_btn.setMinimumWidth(84)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        action_row.addWidget(self.connect_btn)
        action_row.addStretch()
        conn_group.addLayout(action_row)
        self.connection_widget.setVisible(self.show_connection_panel)
        layout.addWidget(self.connection_widget)

        # -- Asset summary --
        asset_group = QGroupBox("账户概览（AI策略虚拟账户）")
        asset_form = QFormLayout(asset_group)
        asset_form.setSpacing(4)
        self.lbl_total_asset = QLabel("-")
        self.lbl_available = QLabel("-")
        self.lbl_market_value = QLabel("-")
        self.lbl_profit = QLabel("-")
        for label in (self.lbl_total_asset, self.lbl_available, self.lbl_market_value, self.lbl_profit):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setStyleSheet("font-weight: bold;")
        asset_form.addRow("总资产:", self.lbl_total_asset)
        asset_form.addRow("可用资金:", self.lbl_available)
        asset_form.addRow("持仓市值:", self.lbl_market_value)
        asset_form.addRow("总盈亏:", self.lbl_profit)
        layout.addWidget(asset_group)

        action_group = QGroupBox("操作")
        action_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        action_layout = QVBoxLayout(action_group)
        action_layout.setContentsMargins(8, 8, 8, 8)
        action_layout.setSpacing(6)
        action_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        utility_btn_min_width = 112
        utility_btn_height = 30
        manual_btn_height = 30

        settings_label = QLabel("设置")
        settings_label.setStyleSheet(section_title_style)
        action_layout.addWidget(settings_label)

        self.lbl_scheduler_status = QLabel("定时任务: 未启用")
        self.lbl_scheduler_status.setStyleSheet("color:#6B7B8D;font-size:11px;")
        action_layout.addWidget(self.lbl_scheduler_status)

        # 统一的配置入口：主面板只保留“查看配置”，详细参数迁入独立弹窗。
        config_ctl_row = QHBoxLayout()
        config_ctl_row.setSpacing(6)
        self.btn_open_schedule = QPushButton("⏰ 定时任务")
        self.btn_open_schedule.setToolTip("打开 AI 定时任务配置")
        self.btn_open_schedule.clicked.connect(lambda: self.scheduler_settings_requested.emit())
        self.btn_open_schedule.setMinimumWidth(utility_btn_min_width)
        self.btn_open_schedule.setMinimumHeight(utility_btn_height)
        self.btn_open_schedule.setStyleSheet(
            "QPushButton{background:#0EA5E9;color:white;padding:5px 10px;"
            "border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#0284C7;}"
        )
        config_ctl_row.addWidget(self.btn_open_schedule)

        self.btn_toggle_config = QPushButton("⚙ 查看配置")
        self.btn_toggle_config.setToolTip("打开 AI 策略配置弹窗（默认只读）")
        self.btn_toggle_config.clicked.connect(self._on_toggle_config)
        self.btn_toggle_config.setMinimumWidth(utility_btn_min_width)
        self.btn_toggle_config.setMinimumHeight(utility_btn_height)
        self.btn_toggle_config.setStyleSheet(
            "QPushButton{background:#6366F1;color:white;padding:5px 10px;"
            "border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#4F46E5;}"
        )
        config_ctl_row.addWidget(self.btn_toggle_config)
        config_ctl_row.addStretch()
        action_layout.addLayout(config_ctl_row)
        self._config_locked = True

        # 配置容器：统一收纳“交易方式”和“策略风控（网关统一）”，由弹窗承载。
        self._config_container = QWidget()
        container_layout = QVBoxLayout(self._config_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(6)

        self.trade_group = QGroupBox("自动交易方式")
        trade_form = QFormLayout(self.trade_group)
        trade_form.setSpacing(4)

        self.execution_sequence_combo = QComboBox()
        self.execution_sequence_combo.addItem("先卖后买", "sell_first")
        self.execution_sequence_combo.addItem("先买后卖", "buy_first")
        self.execution_sequence_combo.addItem("只卖出", "sell_only")
        self.execution_sequence_combo.addItem("只买入", "buy_only")
        trade_form.addRow("执行顺序:", self.execution_sequence_combo)

        self.buy_sizing_combo = QComboBox()
        self.buy_sizing_combo.addItem("按剩余槽位均分", "equal_slots")
        self.buy_sizing_combo.addItem("每笔固定金额", "fixed_amount")
        self.buy_sizing_combo.addItem("每笔固定仓位%", "fixed_pct")
        self.buy_sizing_combo.currentIndexChanged.connect(self._update_trade_config_widget_state)
        trade_form.addRow("买入方式:", self.buy_sizing_combo)

        self.buy_amount_spin = QDoubleSpinBox()
        self.buy_amount_spin.setRange(0.0, 10_000_000.0)
        self.buy_amount_spin.setDecimals(0)
        self.buy_amount_spin.setSingleStep(1000.0)
        self.buy_amount_spin.setSuffix(" 元")
        trade_form.addRow("每笔金额:", self.buy_amount_spin)

        self.buy_pct_spin = QDoubleSpinBox()
        self.buy_pct_spin.setRange(0.0, 100.0)
        self.buy_pct_spin.setDecimals(1)
        self.buy_pct_spin.setSingleStep(1.0)
        self.buy_pct_spin.setSuffix(" %")
        trade_form.addRow("每笔仓位:", self.buy_pct_spin)

        self.sell_sizing_combo = QComboBox()
        self.sell_sizing_combo.addItem("按AI信号", "signal_driven")
        self.sell_sizing_combo.addItem("直接清仓", "full_exit")
        self.sell_sizing_combo.addItem("半仓卖出", "half_exit")
        trade_form.addRow("卖出方式:", self.sell_sizing_combo)

        self.max_buy_orders_spin = QSpinBox()
        self.max_buy_orders_spin.setRange(0, 20)
        trade_form.addRow("每日最多买单:", self.max_buy_orders_spin)

        self.max_sell_orders_spin = QSpinBox()
        self.max_sell_orders_spin.setRange(0, 20)
        trade_form.addRow("每日最多卖单:", self.max_sell_orders_spin)

        self.max_new_positions_spin = QSpinBox()
        self.max_new_positions_spin.setRange(0, 20)
        trade_form.addRow("每日最多新开仓:", self.max_new_positions_spin)

        self.allow_open_new_cb = QCheckBox("允许新开仓")
        self.allow_add_existing_cb = QCheckBox("允许已有持仓加仓")
        trade_form.addRow("", self.allow_open_new_cb)
        trade_form.addRow("", self.allow_add_existing_cb)

        trade_btn_widget = QWidget()
        trade_btn_row = QHBoxLayout(trade_btn_widget)
        trade_btn_row.setContentsMargins(0, 0, 0, 0)
        self.trade_config_status = QLabel("")
        self.trade_config_status.setStyleSheet("color:#888; font-size:12px;")
        self.trade_config_status.setWordWrap(True)
        trade_btn_row.addWidget(self.trade_config_status, stretch=1)
        self.trade_config_save_btn = QPushButton("保存交易方式")
        self.trade_config_save_btn.clicked.connect(self._save_trade_config)
        trade_btn_row.addWidget(self.trade_config_save_btn)
        trade_form.addRow("", trade_btn_widget)
        container_layout.addWidget(self.trade_group)

        # 策略风控（声明式 schema 自动渲染；与 ETF Tab 共用 StrategyRiskSettingsPanel）
        # 触发一次 TradeExecutionService 初始化，确保 AIStockRiskPolicy 已注册到 registry
        configurable_policy = None
        try:
            get_trade_execution_service()
            registry = get_strategy_risk_registry()
            for candidate in registry.resolve(AI_STOCK_STRATEGY_ID):
                if is_configurable(candidate):
                    configurable_policy = candidate
                    break
        except Exception as exc:
            logger.error("初始化 AI 策略风控面板失败: %s", exc, exc_info=True)

        self.risk_policy_panel: Optional[StrategyRiskSettingsPanel] = None
        if configurable_policy is not None:
            self.risk_policy_panel = StrategyRiskSettingsPanel(
                policy=configurable_policy,
                title="AI 策略风控（网关统一）",
            )
            container_layout.addWidget(self.risk_policy_panel)

        self._lock_config_panels()

        sep_settings = QFrame()
        sep_settings.setFrameShape(QFrame.Shape.HLine)
        sep_settings.setStyleSheet("color:#3c3c3c;")
        action_layout.addWidget(sep_settings)

        manual_label = QLabel("手动干预")
        manual_label.setStyleSheet(section_title_style)
        action_layout.addWidget(manual_label)

        self.btn_manual_order = QPushButton("手动委托")
        self.btn_manual_order.clicked.connect(lambda: self.manual_order_requested.emit())
        self.btn_manual_order.setMinimumHeight(manual_btn_height)
        self.btn_manual_order.setStyleSheet(
            "QPushButton{background:#2d2d2d;color:#ffffff;padding:6px 10px;"
            "border:1px solid #3c3c3c;border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#3c3c3c;}"
        )
        action_layout.addWidget(self.btn_manual_order)
        layout.addWidget(action_group)
        layout.addStretch()

        self.broker.connection_changed.connect(self._on_connection_changed)
        if self.broker.is_connected:
            self._on_connection_changed(True, "已连接")
        self._load_trade_config()

    def _on_connect_clicked(self):
        if self.broker.is_connected:
            self.broker.disconnect()
            return
        config = self.broker.get_config()
        qmt_path = config.get("qmt_path", "")
        account = config.get("account", "")
        if not qmt_path or not account:
            QMessageBox.warning(self, "提示", "请先在交易窗口中配置券商路径和账号")
            return
        self.broker.connect_async(qmt_path, account)
        self.connect_btn.setEnabled(False)
        self.status_label.setText("连接中...")
        self._refresh_client_status_safe()

    def _on_connection_changed(self, connected: bool, message: str):
        self.connect_btn.setEnabled(True)
        if connected:
            self.status_icon.setText("🟢")
            self.status_label.setText("已连接")
            self.connect_btn.setText("断开")
            self.refresh()
        else:
            self.status_icon.setText("🔴")
            self.status_label.setText("未连接")
            self.connect_btn.setText("连接券商")
            self._clear_display()
        self._refresh_client_status_safe()

    def _on_client_state_changed(self, _state: dict):
        self._refresh_client_status_safe()

    def _refresh_client_status_safe(self):
        if self.shared_broker_panel is not None and not self.show_connection_panel:
            self.shared_broker_panel.refresh_client_status()
            return
        if self._status_worker and self._status_worker.isRunning():
            return
        self._status_worker = _ClientStatusWorker(self.broker, parent=self)
        self._status_worker.finished_status.connect(self._apply_client_status)
        self._status_worker.failed_status.connect(self._apply_client_status_error)
        self._status_worker.start()

    def _apply_client_status(self, status: dict):
        text = status.get("message", "客户端: 未检测")
        self.client_status_label.setText(f"客户端: {text}")
        login_visible = bool(status.get("login_window_visible"))
        running = bool(status.get("running"))
        self.launch_btn.setEnabled(not running)
        self.login_btn.setEnabled(running)
        self.close_btn.setEnabled(running)
        if login_visible:
            self.client_status_label.setStyleSheet("color: #f0ad4e;")
        elif running:
            self.client_status_label.setStyleSheet("color: #5cb85c;")
        else:
            self.client_status_label.setStyleSheet("color: #888;")
        self._status_worker = None

    def _apply_client_status_error(self, message: str):
        logger.warning("刷新 QMT 客户端状态失败: %s", message)
        self.client_status_label.setText("客户端: 状态检测失败")
        self.client_status_label.setStyleSheet("color: #d9534f;")
        self._status_worker = None

    def _show_client_action_result(self, title: str, success: bool, message: str):
        self._refresh_client_status_safe()
        status_text = f"{title}: {message}"
        self.client_status_label.setText(f"客户端: {status_text}")
        if success:
            self.client_status_label.setStyleSheet("color: #5cb85c;")
        else:
            self.client_status_label.setStyleSheet("color: #d9534f;")

    def show_client_workflow_status(self, message: str, *, success: Optional[bool] = None):
        if self.shared_broker_panel is not None and not self.show_connection_panel:
            self.shared_broker_panel.show_client_workflow_status(message, success=success)
            return
        self.client_status_label.setText(f"客户端: {message}")
        if success is True:
            self.client_status_label.setStyleSheet("color: #5cb85c;")
        elif success is False:
            self.client_status_label.setStyleSheet("color: #d9534f;")
        else:
            self.client_status_label.setStyleSheet("color: #f0ad4e;")

    def set_scheduler_status(self, text: str, color: str = "#6B7B8D"):
        self.lbl_scheduler_status.setText(text)
        self.lbl_scheduler_status.setStyleSheet(f"color:{color};font-size:11px;")

    def _on_launch_clicked(self):
        self._run_client_action("launch", "正在启动 miniQMT...")

    def _on_login_clicked(self):
        self._run_client_action("login", "正在登录 miniQMT...")

    def _on_close_clicked(self):
        self._run_client_action("close", "正在关闭 miniQMT...")

    def _run_client_action(self, action: str, pending_text: str):
        if self._action_worker and self._action_worker.isRunning():
            self.client_status_label.setText("客户端: QMT 操作正在进行中，请稍候")
            self.client_status_label.setStyleSheet("color: #f0ad4e;")
            return
        self.launch_btn.setEnabled(False)
        self.login_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.client_status_label.setText(f"客户端: {pending_text}")
        self.client_status_label.setStyleSheet("color: #f0ad4e;")
        self._action_worker = _ClientActionWorker(self.broker, action, parent=self)
        self._action_worker.finished_action.connect(self._on_client_action_finished)
        self._action_worker.failed_action.connect(self._on_client_action_failed)
        self._action_worker.start()

    def _on_client_action_finished(self, action: str, success: bool, message: str, _status: dict):
        self._action_worker = None
        title_map = {
            "launch": "启动 miniQMT",
            "login": "登录 miniQMT",
            "close": "关闭 miniQMT",
        }
        self._show_client_action_result(title_map.get(action, "QMT 操作"), success, message)

    def _on_client_action_failed(self, action: str, message: str):
        self._action_worker = None
        title_map = {
            "launch": "启动 miniQMT",
            "login": "登录 miniQMT",
            "close": "关闭 miniQMT",
        }
        self._show_client_action_result(title_map.get(action, "QMT 操作"), False, message)

    def refresh(self):
        if not self.broker.is_connected:
            return
        if self._refresh_worker and self._refresh_worker.isRunning():
            return
        self._refresh_worker = _AccountRefreshWorker(self.broker, parent=self)
        self._refresh_worker.refresh_ready.connect(self._apply_refresh_result)
        self._refresh_worker.refresh_failed.connect(self._on_refresh_failed)
        self._refresh_worker.start()

    def _apply_refresh_result(self, asset, positions):
        try:
            filtered_positions = self._filter_ai_strategy_positions(positions)
            self._update_assets(asset, filtered_positions)
        except Exception as exc:
            logger.warning("AccountPanel refresh apply failed: %s", exc)
        finally:
            self._refresh_worker = None

    def _on_refresh_failed(self, message: str):
        logger.warning("AccountPanel refresh failed: %s", message)
        self._refresh_worker = None

    def _update_assets(self, asset=None, positions=None):
        try:
            if asset is None:
                asset = self.broker.query_stock_asset()
            if asset is None:
                return
            if positions is None:
                positions = self._filter_ai_strategy_positions(self.broker.query_stock_positions() or [])
            live_positions = [
                {"market_value": float(self._record_value(pos, "market_value", default=0.0) or 0.0)}
                for pos in (positions or [])
            ]
            account = self.strategy_budget.build_account_snapshot(
                AI_STOCK_STRATEGY_ID,
                strategy_name=AI_STOCK_STRATEGY_NAME,
                virtual_account_id=AI_STOCK_VIRTUAL_ACCOUNT_ID,
                real_total_asset=float(self._record_value(asset, "total_asset", default=0.0) or 0.0),
                live_positions=live_positions,
            )
            total = float(account.get("total_asset", 0.0) or 0.0)
            cash = float(account.get("available_cash", 0.0) or 0.0)
            market = float(account.get("market_value", 0.0) or 0.0)
            profit = float(account.get("total_pnl", 0.0) or 0.0)
            self.lbl_total_asset.setText(f"¥{total:,.2f}")
            self.lbl_available.setText(f"¥{cash:,.2f}")
            self.lbl_market_value.setText(f"¥{market:,.2f}")
            color = "green" if profit >= 0 else "red"
            self.lbl_profit.setText(f"<span style='color:{color}'>¥{profit:,.2f}</span>")
        except Exception as exc:
            logger.warning("AccountPanel update assets failed: %s", exc)
            self._clear_display()

    def _clear_display(self):
        self.lbl_total_asset.setText("-")
        self.lbl_available.setText("-")
        self.lbl_market_value.setText("-")
        self.lbl_profit.setText("-")

    def _load_trade_config(self):
        self._trade_config_loading = True
        try:
            cfg = self.auto_trade_config_service.get_config()
            self._set_combo_data(self.execution_sequence_combo, cfg.execution_sequence)
            self._set_combo_data(self.buy_sizing_combo, cfg.buy_sizing_mode)
            self.buy_amount_spin.setValue(float(cfg.buy_value_per_order or 0.0))
            self.buy_pct_spin.setValue(float(cfg.buy_position_pct or 0.0) * 100.0)
            self._set_combo_data(self.sell_sizing_combo, cfg.sell_sizing_mode)
            self.max_buy_orders_spin.setValue(int(cfg.max_buy_orders_per_day or 0))
            self.max_sell_orders_spin.setValue(int(cfg.max_sell_orders_per_day or 0))
            self.max_new_positions_spin.setValue(int(cfg.max_new_positions_per_day or 0))
            self.allow_open_new_cb.setChecked(bool(cfg.allow_open_new_position))
            self.allow_add_existing_cb.setChecked(bool(cfg.allow_add_to_existing))
            self.trade_config_status.setText("已加载当前自动交易配置")
            self.trade_config_status.setStyleSheet("color:#888; font-size:12px;")
        finally:
            self._trade_config_loading = False
            self._update_trade_config_widget_state()

    # ------------------------------------------------------------------
    #  统一配置入口：⚙ 查看配置 -> 独立配置弹窗
    # ------------------------------------------------------------------

    def _get_config_dialog(self):
        if self._config_dialog is None:
            self._config_dialog = AIStrategyConfigDialog(self, parent=self.window())
        return self._config_dialog

    def _on_toggle_config(self):
        """打开独立的策略配置弹窗。"""
        self._load_trade_config()
        if self.risk_policy_panel is not None:
            try:
                self.risk_policy_panel.reload()
            except Exception as exc:
                logger.error("reload 策略风控面板失败: %s", exc, exc_info=True)
        self._lock_config_panels()
        dialog = self._get_config_dialog()
        dialog.prepare_for_open()
        dialog.exec()

    def request_unlock_config(self) -> bool:
        """二次确认后解锁配置容器。"""
        reply = QMessageBox.question(
            self,
            "解锁编辑",
            "确定要解锁配置面板进行编辑吗？\n修改后请点击对应模块的『保存』按钮。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._unlock_config_panels()
            return True
        return False

    def _lock_config_panels(self):
        """将配置容器内的输入控件设为只读（QLabel / QGroupBox 不动）。"""
        self._config_locked = True
        for w in self._config_container.findChildren(QWidget):
            if not isinstance(w, (QLabel, QGroupBox)):
                w.setEnabled(False)

    def _unlock_config_panels(self):
        """解锁配置容器，恢复所有控件的可编辑状态。"""
        self._config_locked = False
        for w in self._config_container.findChildren(QWidget):
            w.setEnabled(True)

    def _update_trade_config_widget_state(self):
        buy_mode = self.buy_sizing_combo.currentData() or "equal_slots"
        self.buy_amount_spin.setEnabled(buy_mode == "fixed_amount")
        self.buy_pct_spin.setEnabled(buy_mode == "fixed_pct")

    def _save_trade_config(self):
        if self._trade_config_loading:
            return
        try:
            config = self.auto_trade_config_service.update_config(
                execution_sequence=self.execution_sequence_combo.currentData() or "sell_first",
                buy_sizing_mode=self.buy_sizing_combo.currentData() or "equal_slots",
                buy_value_per_order=float(self.buy_amount_spin.value()),
                buy_position_pct=float(self.buy_pct_spin.value()) / 100.0,
                sell_sizing_mode=self.sell_sizing_combo.currentData() or "signal_driven",
                max_buy_orders_per_day=int(self.max_buy_orders_spin.value()),
                max_sell_orders_per_day=int(self.max_sell_orders_spin.value()),
                max_new_positions_per_day=int(self.max_new_positions_spin.value()),
                allow_open_new_position=self.allow_open_new_cb.isChecked(),
                allow_add_to_existing=self.allow_add_existing_cb.isChecked(),
            )
            self.trade_config_status.setText(
                f"已保存: {self._execution_sequence_label(config.execution_sequence)} / "
                f"{self._buy_mode_label(config.buy_sizing_mode)} / "
                f"{self._sell_mode_label(config.sell_sizing_mode)}"
            )
            self.trade_config_status.setStyleSheet("color:#4caf50; font-size:12px;")
        except Exception as exc:
            logger.exception("保存自动交易方式失败")
            self.trade_config_status.setText(f"保存失败: {exc}")
            self.trade_config_status.setStyleSheet("color:#d9534f; font-size:12px;")

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str):
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    @staticmethod
    def _execution_sequence_label(value: str) -> str:
        mapping = {
            "sell_first": "先卖后买",
            "buy_first": "先买后卖",
            "sell_only": "只卖出",
            "buy_only": "只买入",
        }
        return mapping.get(value, value or "-")

    @staticmethod
    def _buy_mode_label(value: str) -> str:
        mapping = {
            "equal_slots": "均分",
            "fixed_amount": "固定金额",
            "fixed_pct": "固定仓位",
        }
        return mapping.get(value, value or "-")

    @staticmethod
    def _sell_mode_label(value: str) -> str:
        mapping = {
            "signal_driven": "按AI信号",
            "full_exit": "清仓",
            "half_exit": "半仓",
        }
        return mapping.get(value, value or "-")

    def get_broker_context(self) -> BrokerContext:
        if not self.broker.is_connected:
            return BrokerContext()
        try:
            asset = self.broker.query_stock_asset()
            positions = self.broker.query_stock_positions() or []
            positions = self._filter_ai_strategy_positions(positions)
            top = []
            for p in positions:
                top.append({
                    "code": getattr(p, "stock_code", ""),
                    "volume": int(getattr(p, "volume", 0) or 0),
                    "cost_price": float(getattr(p, "open_price", 0) or 0),
                    "market_value": float(getattr(p, "market_value", 0) or 0),
                })
            return BrokerContext(
                connected=True,
                account_id=getattr(self.broker, "_last_config", {}).get("account", ""),
                total_asset=float(getattr(asset, "total_asset", 0) or 0),
                available_cash=float(getattr(asset, "cash", 0) or 0),
                position_count=len(positions),
                top_positions=top,
            )
        except Exception:
            return BrokerContext(connected=True)

    def get_live_positions(self) -> List[Dict[str, Any]]:
        if not self.broker.is_connected:
            return []
        try:
            positions = self.broker.query_stock_positions() or []
        except Exception:
            return []

        results: List[Dict[str, Any]] = []
        for pos in self._filter_ai_strategy_positions(positions):
            volume = int(getattr(pos, "volume", 0) or 0)
            code = getattr(pos, "stock_code", "") or ""
            results.append({
                "code": code,
                "name": self._resolve_symbol_name(code, getattr(pos, "stock_name", "") or ""),
                "volume": volume,
                "can_use_volume": int(getattr(pos, "can_use_volume", 0) or 0),
                "cost_price": float(getattr(pos, "open_price", 0) or 0),
                "market_value": float(getattr(pos, "market_value", 0) or 0),
                "profit_rate": float(getattr(pos, "profit_rate", 0) or 0),
            })
        return results

    def _filter_ai_strategy_positions(self, positions) -> List[Any]:
        filtered: List[Any] = []
        for pos in positions or []:
            volume = int(self._record_value(pos, "volume", default=0) or 0)
            if volume <= 0:
                continue
            code = self._plain_code(self._record_value(pos, "stock_code", default="") or "")
            if not code:
                continue
            owner = self.strategy_registry.get_owner(code)
            if owner is None or not owner.enabled:
                continue
            if owner.strategy_id != AI_STOCK_STRATEGY_ID:
                continue
            filtered.append(pos)
        return filtered

    @staticmethod
    def _plain_code(code: str) -> str:
        value = (code or "").strip().upper()
        return value.split(".")[0] if "." in value else value

    @staticmethod
    def _record_value(data: Any, key: str, default: Any = None) -> Any:
        if isinstance(data, dict):
            return data.get(key, default)
        return getattr(data, key, default)

    def _resolve_symbol_name(self, code: str, fallback_name: str = "") -> str:
        if fallback_name:
            return fallback_name
        trade_window = self._find_trade_window()
        if trade_window:
            looked_up = trade_window.lookup_symbol_name(code)
            if looked_up:
                return looked_up
        return code

    def _display_code(self, code: str) -> str:
        if "." in code:
            return code.split(".")[0]
        return code

    def _find_trade_window(self):
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "lookup_symbol_name") and hasattr(parent, "order_panel"):
                return parent
            parent = parent.parent() if hasattr(parent, "parent") and callable(parent.parent) else None
        return None


# ───────────────────────────────────────────────────────────────────────────
#  Right panel: Order execution and details
# ───────────────────────────────────────────────────────────────────────────
class OrderExecutionPanel(QWidget):
    """Manual order placement panel for AI strategy."""

    order_executed = pyqtSignal(bool, bool, str, int, float)  # success, filled_confirmed, message, order_id, price

    def __init__(self, parent=None):
        super().__init__(parent)
        self.broker = get_broker_session_service()
        self.execution_service = get_trade_execution_service()
        self._decision_context: Optional[dict] = None
        self._current_code = ""
        self._current_name = ""
        self._current_direction = "buy"
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        title = QLabel("手动委托")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        order_form = QFormLayout()
        order_form.setSpacing(6)
        self.lbl_order_code = QLabel("-")
        self.lbl_order_name = QLabel("-")
        self.lbl_order_direction = QLabel("-")
        self.lbl_order_confidence = QLabel("-")
        self.lbl_order_risk = QLabel("-")
        self.price_input = QLineEdit()
        self.price_input.setPlaceholderText("委托价格")
        self.volume_input = QLineEdit()
        self.volume_input.setPlaceholderText("委托数量(手,1手=100股)")
        self.amount_label = QLabel("-")
        order_form.addRow("代码:", self.lbl_order_code)
        order_form.addRow("名称:", self.lbl_order_name)
        order_form.addRow("方向:", self.lbl_order_direction)
        order_form.addRow("置信度:", self.lbl_order_confidence)
        order_form.addRow("风控:", self.lbl_order_risk)
        order_form.addRow("价格:", self.price_input)
        order_form.addRow("数量(手):", self.volume_input)
        order_form.addRow("委托金额:", self.amount_label)
        layout.addLayout(order_form)

        self.decision_note = QPlainTextEdit()
        self.decision_note.setReadOnly(True)
        self.decision_note.setPlaceholderText("这里会展示当前 AI 决策的委托说明。")
        self.decision_note.setMaximumHeight(180)
        layout.addWidget(self.decision_note)

        btn_row = QHBoxLayout()
        self.clear_btn = QPushButton("清空委托")
        self.clear_btn.clicked.connect(self._clear_order_form)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch()
        self.exec_btn = QPushButton("提交委托")
        self.exec_btn.setFixedHeight(38)
        self.exec_btn.setStyleSheet(
            "QPushButton { background-color: #0078d4; color: white; font-size: 14px; "
            "font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #106ebe; }"
            "QPushButton:disabled { background-color: #999999; }"
        )
        self.exec_btn.clicked.connect(self._on_execute)
        btn_row.addWidget(self.exec_btn)
        layout.addLayout(btn_row)
        layout.addStretch()

        self.price_input.textChanged.connect(self._update_amount)
        self.volume_input.textChanged.connect(self._update_amount)

    def fill_from_decision(
        self,
        decision: TradeDecision,
        *,
        risk_result=None,
        approved: bool = False,
        decision_record_id: str = "",
    ):
        self._decision_context = {
            "decision": decision,
            "risk_result": risk_result,
            "approved": approved,
            "decision_record_id": decision_record_id,
        }
        direction = "buy" if decision.action in (TradeAction.BUY.value, TradeAction.ADD.value) else "sell"
        self._current_code = decision.symbol_code
        self._current_name = decision.symbol_name
        self._current_direction = direction
        self.lbl_order_code.setText(decision.symbol_code)
        self.lbl_order_name.setText(decision.symbol_name)
        self.lbl_order_direction.setText(TRADE_ACTION_LABELS.get(decision.action, decision.action))
        self.lbl_order_confidence.setText(f"{decision.confidence:.0%}")
        risk_text = "通过" if getattr(risk_result, "passed", False) else "待确认"
        if getattr(risk_result, "blocked_reasons", None):
            risk_text = " / ".join(list(risk_result.blocked_reasons)[:2])
        self.lbl_order_risk.setText(risk_text)
        self.price_input.setText(f"{decision.current_price:.2f}" if decision.current_price > 0 else "")
        self.volume_input.setText(self._suggest_lots_for_decision(decision))
        note_parts = [
            f"操作建议: {decision.action_label}",
            f"目标价: {decision.target_price:.2f}" if decision.target_price > 0 else "目标价: -",
            f"止损价: {decision.stop_loss_price:.2f}" if decision.stop_loss_price > 0 else "止损价: -",
            f"建议仓位: {decision.position_pct:.0%}" if decision.position_pct > 0 else "建议仓位: -",
        ]
        if decision.reasoning:
            note_parts.append(f"理由: {decision.reasoning}")
        if getattr(risk_result, "warnings", None):
            note_parts.append("风险提示: " + "；".join(list(risk_result.warnings)[:3]))
        if not decision.is_actionable:
            note_parts.append("当前结论为非执行类建议，默认不提交委托。")
        self.decision_note.setPlainText("\n".join(note_parts))
        self.exec_btn.setEnabled(bool(decision.is_actionable))
        self._update_amount()

    def fill_order(self, code: str, direction: str, price: float):
        self.clear_decision_context()
        self._current_code = code
        self._current_name = code
        self._current_direction = "buy" if direction == "buy" else "sell"
        self.lbl_order_code.setText(code or "-")
        self.lbl_order_name.setText(code or "-")
        self.lbl_order_direction.setText("买入" if self._current_direction == "buy" else "卖出")
        self.lbl_order_confidence.setText("-")
        self.lbl_order_risk.setText("手动委托")
        if price > 0:
            self.price_input.setText(f"{price:.2f}")
        else:
            self.price_input.clear()
        self.volume_input.setText(self._suggest_lots_for_manual(code, self._current_direction))
        self.decision_note.setPlainText("该委托来自当前持仓/账户操作，不绑定 AI 决策记录。")
        self.exec_btn.setEnabled(True)
        self._update_amount()

    def clear_decision_context(self):
        self._decision_context = None

    def _clear_order_form(self):
        self.clear_decision_context()
        self._current_code = ""
        self._current_name = ""
        self._current_direction = "buy"
        self.lbl_order_code.setText("-")
        self.lbl_order_name.setText("-")
        self.lbl_order_direction.setText("-")
        self.lbl_order_confidence.setText("-")
        self.lbl_order_risk.setText("-")
        self.price_input.clear()
        self.volume_input.clear()
        self.amount_label.setText("-")
        self.decision_note.clear()
        self.exec_btn.setEnabled(True)

    def _suggest_lots_for_decision(self, decision: TradeDecision) -> str:
        try:
            if decision.action in (TradeAction.SELL.value, TradeAction.REDUCE.value):
                volume = self.execution_service.estimate_volume_for_decision(decision)
                return str(max(int(volume / 100), 0))
            if self.broker.is_connected and decision.current_price > 0:
                asset = self.broker.query_stock_asset()
                cash = float(getattr(asset, "cash", 0) or 0)
                amount = cash * max(float(decision.position_pct or 0.0), 0.0)
                lots = int(math.floor(amount / (decision.current_price * 100)))
                return str(max(lots, 1))
        except Exception:
            pass
        return ""

    def _suggest_lots_for_manual(self, code: str, direction: str) -> str:
        if not self.broker.is_connected or direction != "sell":
            return ""
        try:
            positions = self.broker.query_stock_positions() or []
            code_plain = (code or "").strip().upper().split(".")[0]
            for pos in positions:
                pos_code = str(getattr(pos, "stock_code", "") or "").strip().upper().split(".")[0]
                if pos_code != code_plain:
                    continue
                can_use = int(getattr(pos, "can_use_volume", 0) or 0)
                return str(max(int(can_use / 100), 0))
        except Exception:
            pass
        return ""

    def _update_amount(self):
        try:
            price = float(self.price_input.text())
            lots = int(self.volume_input.text())
            amount = price * lots * 100
            self.amount_label.setText(f"¥{amount:,.2f}")
        except (ValueError, TypeError):
            self.amount_label.setText("-")

    def _on_execute(self):
        code = self._current_code.strip()
        if not code:
            QMessageBox.warning(self, "提示", "当前没有可提交的委托")
            return
        if not self.broker.is_connected:
            QMessageBox.warning(self, "提示", "券商未连接")
            return
        try:
            price = float(self.price_input.text())
            lots = int(self.volume_input.text())
        except (ValueError, TypeError):
            QMessageBox.warning(self, "提示", "请输入有效的价格和数量")
            return
        volume = lots * 100
        if volume <= 0:
            QMessageBox.warning(self, "提示", "委托数量必须大于0")
            return
        order_type = 23 if self._current_direction == "buy" else 24
        action_label = "买入" if self._current_direction == "buy" else "卖出"
        confirm = QMessageBox.question(
            self,
            "委托确认",
            f"确认{action_label} {code} {lots}手(={volume}股) @ ¥{price:.2f}？\n"
            f"委托金额: ¥{price * volume:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            decision = self._decision_context.get("decision") if self._decision_context else None
            risk_result = self._decision_context.get("risk_result") if self._decision_context else None
            approved = bool(self._decision_context.get("approved")) if self._decision_context else False
            decision_record_id = str(self._decision_context.get("decision_record_id", "")) if self._decision_context else ""
            if decision is not None and not decision.is_actionable:
                QMessageBox.information(self, "提示", "当前 AI 结论不是可执行委托，无需提交下单。")
                return
            result = self.execution_service.execute(
                ExecutionRequest(
                    stock_code=code,
                    stock_name=self._current_name or code,
                    order_type=order_type,
                    order_volume=volume,
                    price_type=5,
                    price=price,
                    source=TradeSource.AI_AGENT.value,
                    trigger="manual",
                    strategy_name=AI_STOCK_STRATEGY_NAME,
                    strategy_id=AI_STOCK_STRATEGY_ID,
                    virtual_account_id=AI_STOCK_VIRTUAL_ACCOUNT_ID,
                    remark="AI交易决策中心委托下单",
                    decision=decision,
                    risk_result=risk_result,
                    decision_record_id=decision_record_id,
                    require_approval=decision is not None,
                    approved=approved,
                )
            )
            self.order_executed.emit(
                result.success,
                result.filled_confirmed,
                result.message,
                result.broker_order_id,
                price,
            )
            if result.success and approved:
                self.clear_decision_context()
        except Exception as exc:
            msg = f"下单失败: {exc}"
            self.order_executed.emit(False, False, msg, -1, 0.0)


# ───────────────────────────────────────────────────────────────────────────
#  Center panel: AI Decision analysis
# ───────────────────────────────────────────────────────────────────────────
class DecisionPanel(QWidget):
    """AI trade decision analysis and display panel."""

    decision_ready = pyqtSignal(object)  # TradeDecision
    scan_completed = pyqtSignal(object)

    def __init__(self, context_provider=None, parent=None):
        super().__init__(parent)
        self.context_provider = context_provider
        self.agent_runtime = StockAgentRuntime()
        self.risk_guard = RiskGuardService()
        self.decision_tracker = DecisionTrackerService()
        self._current_decision: Optional[TradeDecision] = None
        self._current_risk_result = None
        self._chat_thread = None
        self._ai_config = self._load_ai_config()
        self.stock_pool_service = get_stock_pool_service()
        self._full_response = ""
        self._context_for_decision = None
        self._current_mode = DECISION_MODE_POSITION_SCAN
        self._scan_queue: List[Dict[str, Any]] = []
        self._scan_results: List[Dict[str, Any]] = []
        self._current_scan_item: Optional[Dict[str, Any]] = None
        self._current_scan_index = -1
        self._scan_in_progress = False
        self._scan_total_count = 0
        self._scan_completed_count = 0
        self._scan_active_workers: Dict[str, Any] = {}
        self._scan_worker_states: Dict[str, Dict[str, Any]] = {}
        self._active_scan_run_id: str = ""
        self._active_scan_source: str = ""
        self._active_scan_task_id: str = ""
        self._run_context_override: Optional[DecisionRunContext] = None
        self._stream_started = False
        self._progress_cards: List[CollapsibleStepCard] = []
        self._current_approved_record_id: str = ""
        self._setup_ui()

    def _load_ai_config(self) -> dict:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "ai_config.json",
        )
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # -- Top: mode / symbol / model selector --
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("持仓巡检", DECISION_MODE_POSITION_SCAN)
        self.mode_combo.addItem("候选池巡检", DECISION_MODE_CANDIDATE_POOL_SCAN)
        self.mode_combo.setFixedWidth(120)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_row.addWidget(self.mode_combo)

        top_row.addWidget(QLabel("标的:"))
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("输入代码，如 000001.SZ（留空则用主窗口当前标的）")
        self.symbol_input.setFixedWidth(240)
        self.symbol_input.setVisible(False)
        top_row.addWidget(self.symbol_input)

        self.watchlist_group_combo = QComboBox()
        self.watchlist_group_combo.setFixedWidth(140)
        self.watchlist_group_combo.setVisible(False)
        top_row.addWidget(self.watchlist_group_combo)

        self.mode_hint_label = QLabel("持仓巡检: 自动读取当前券商持仓，逐只生成持有/加仓/减仓/卖出决策")
        self.mode_hint_label.setStyleSheet("color: #666;")
        top_row.addWidget(self.mode_hint_label)

        top_row.addWidget(QLabel("模型:"))
        self.model_combo = QComboBox()
        model_configs = self._ai_config.get("model_configs", {})
        if model_configs:
            self.model_combo.addItems(list(model_configs.keys()))
        else:
            self.model_combo.addItems(["deepseek-chat", "gpt-4o", "gemini-3-pro-preview"])
        selected = self._ai_config.get("selected_model", "")
        if selected and self.model_combo.findText(selected) >= 0:
            self.model_combo.setCurrentText(selected)
        self.model_combo.setFixedWidth(180)
        top_row.addWidget(self.model_combo)

        top_row.addStretch()
        self.analyze_btn = QPushButton("🔍 生成交易决策")
        self.analyze_btn.setFixedHeight(36)
        self.analyze_btn.setStyleSheet(
            "QPushButton { background-color: #107c10; color: white; font-size: 13px; "
            "font-weight: bold; border-radius: 4px; padding: 0 16px; }"
            "QPushButton:hover { background-color: #0e6b0e; }"
            "QPushButton:disabled { background-color: #888888; }"
        )
        self.analyze_btn.clicked.connect(self._on_analyze_clicked)
        top_row.addWidget(self.analyze_btn)
        layout.addLayout(top_row)

        # -- Stacked: placeholder vs result --
        self.stack = QStackedWidget()

        # Page 0: placeholder
        placeholder = QLabel(
            "点击「开始巡检」开始分析当前策略任务\n\n"
            "当前仅保留“持仓巡检”和“候选池巡检”两种模式。"
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #888; font-size: 14px;")
        self.stack.addWidget(placeholder)

        # Page 1: progress
        progress_widget = QWidget()
        progress_layout = QVBoxLayout(progress_widget)
        progress_layout.setContentsMargins(16, 16, 16, 16)
        progress_layout.setSpacing(10)
        self.progress_label = QLabel("正在分析...")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_label.setStyleSheet("color: #0078d4; font-size: 14px; font-weight: bold;")
        progress_layout.addWidget(self.progress_label)

        self.progress_hint_label = QLabel("以下为本轮交易决策生成的中间步骤概要")
        self.progress_hint_label.setStyleSheet("color: #888;")
        self.progress_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_layout.addWidget(self.progress_hint_label)

        self.progress_scroll = QScrollArea()
        self.progress_scroll.setWidgetResizable(True)
        self.progress_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.progress_scroll.setStyleSheet("background-color: transparent; border: none;")
        self.progress_cards_host = QWidget()
        self.progress_cards_layout = QVBoxLayout(self.progress_cards_host)
        self.progress_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_cards_layout.setSpacing(2)
        self.progress_cards_layout.addStretch()
        self.progress_scroll.setWidget(self.progress_cards_host)
        progress_layout.addWidget(self.progress_scroll, stretch=1)
        self.stack.addWidget(progress_widget)

        # Page 2: result area
        result_widget = QWidget()
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)

        # Tab: analysis text + decision card + history
        self.result_tabs = QTabWidget()

        # Tab 1: AI analysis text
        self.analysis_display = QTextEdit()
        self.analysis_display.setReadOnly(True)
        self.result_tabs.addTab(self.analysis_display, "AI 分析报告")

        # Tab 2: Process review
        self.process_review_scroll = QScrollArea()
        self.process_review_scroll.setWidgetResizable(True)
        self.process_review_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.process_review_scroll.setStyleSheet("background-color: transparent; border: none;")
        self.process_review_host = QWidget()
        self.process_review_layout = QVBoxLayout(self.process_review_host)
        self.process_review_layout.setContentsMargins(0, 0, 0, 0)
        self.process_review_layout.setSpacing(2)
        self.process_review_layout.addStretch()
        self.process_review_scroll.setWidget(self.process_review_host)
        self.result_tabs.addTab(self.process_review_scroll, "过程回看")

        # Tab 3: Batch summary
        self.scan_table = QTableWidget(0, 9)
        self.scan_table.setHorizontalHeaderLabels(
            ["序号", "代码", "名称", "操作", "置信度", "现价", "成本", "风控", "状态"]
        )
        self.scan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.scan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.scan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.scan_table.verticalHeader().setVisible(False)
        self.scan_table.setAlternatingRowColors(True)
        self.scan_table.setStyleSheet("""
            QTableWidget {
                background-color: #1e1e2e;
                alternate-background-color: #2a2a3e;
                color: #d0d0d0;
                gridline-color: #3a3a4e;
                selection-background-color: #3a5fcd;
                selection-color: #ffffff;
            }
            QTableWidget::item { padding: 4px 6px; }
            QHeaderView::section {
                background-color: #16162a;
                color: #e0e0e0;
                padding: 5px 6px;
                border: 1px solid #3a3a4e;
                font-weight: bold;
            }
        """)
        self.scan_table.itemSelectionChanged.connect(self._on_scan_selection_changed)
        self.result_tabs.addTab(self.scan_table, "巡检汇总")

        # Tab 4: Decision card
        self.decision_card_widget = QWidget()
        self.decision_card_layout = QVBoxLayout(self.decision_card_widget)
        self.decision_card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.result_tabs.addTab(self.decision_card_widget, "决策详情")

        # Tab 5: Decision history + stats
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(4)

        self.stats_bar = QLabel("")
        self.stats_bar.setStyleSheet(
            "background-color: #1e1e2e; color: #d0d0d0; padding: 8px 12px; "
            "border-radius: 4px; font-size: 13px;"
        )
        self.stats_bar.setWordWrap(True)
        history_layout.addWidget(self.stats_bar)

        self.history_table = QTableWidget(0, 9)
        self.history_table.setHorizontalHeaderLabels(
            ["时间", "标的", "操作", "置信度", "入场价", "盈亏%", "盈亏额", "风控", "结果"]
        )
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setStyleSheet("""
            QTableWidget {
                background-color: #1e1e2e;
                alternate-background-color: #2a2a3e;
                color: #d0d0d0;
                gridline-color: #3a3a4e;
                selection-background-color: #3a5fcd;
                selection-color: #ffffff;
            }
            QTableWidget::item { padding: 4px 6px; }
            QHeaderView::section {
                background-color: #16162a;
                color: #e0e0e0;
                padding: 5px 6px;
                border: 1px solid #3a3a4e;
                font-weight: bold;
            }
        """)
        self.history_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self._on_history_context_menu)
        history_layout.addWidget(self.history_table, stretch=1)

        export_bar = QHBoxLayout()
        export_bar.setContentsMargins(0, 2, 0, 0)
        export_bar.addStretch()
        export_csv_btn = QPushButton("📄 导出CSV")
        export_csv_btn.setFixedHeight(26)
        export_csv_btn.clicked.connect(self._export_csv)
        export_bar.addWidget(export_csv_btn)
        export_html_btn = QPushButton("📊 导出复盘报告")
        export_html_btn.setFixedHeight(26)
        export_html_btn.clicked.connect(self._export_html)
        export_bar.addWidget(export_html_btn)
        history_layout.addLayout(export_bar)

        self.result_tabs.addTab(history_widget, "决策记录")

        result_layout.addWidget(self.result_tabs)

        # Action bar under results
        action_row = QHBoxLayout()
        self.approve_btn = QPushButton("✅ 确认执行")
        self.approve_btn.setEnabled(False)
        self.approve_btn.setFixedHeight(34)
        self.approve_btn.setStyleSheet(
            "QPushButton { background-color: #0078d4; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 0 16px; }"
            "QPushButton:hover { background-color: #106ebe; }"
            "QPushButton:disabled { background-color: #aaaaaa; }"
        )
        self.approve_btn.clicked.connect(self._on_approve)
        action_row.addWidget(self.approve_btn)

        self.reject_btn = QPushButton("❌ 驳回")
        self.reject_btn.setEnabled(False)
        self.reject_btn.setFixedHeight(34)
        self.reject_btn.clicked.connect(self._on_reject)
        action_row.addWidget(self.reject_btn)

        self.regenerate_btn = QPushButton("🔄 重新生成")
        self.regenerate_btn.setFixedHeight(34)
        self.regenerate_btn.clicked.connect(self._on_analyze_clicked)
        action_row.addWidget(self.regenerate_btn)

        action_row.addStretch()
        self.decision_status_label = QLabel("")
        action_row.addWidget(self.decision_status_label)
        result_layout.addLayout(action_row)

        self.stack.addWidget(result_widget)
        layout.addWidget(self.stack, stretch=1)
        self._on_mode_changed()

    def set_symbol(self, code: str, name: str = ""):
        self.symbol_input.setText(code)

    def _infer_asset_type_for_code(self, code: str, fallback: str = "") -> str:
        if fallback:
            return fallback
        plain_code = (code or "").split(".")[0]
        if plain_code.startswith(("51", "52", "56", "58", "15", "16", "18")):
            return "ETF"
        return "股票"

    def _resolve_runtime_symbol_name(self, code: str, fallback_name: str = "") -> str:
        if fallback_name:
            return fallback_name
        trade_window = self._find_trade_window()
        if trade_window:
            looked_up = trade_window.lookup_symbol_name(code)
            if looked_up:
                return looked_up
        return ""

    def _find_trade_window(self):
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "lookup_symbol_name") and hasattr(parent, "order_panel"):
                return parent
            parent = parent.parent() if hasattr(parent, "parent") and callable(parent.parent) else None
        return None

    @staticmethod
    def _normalize_symbol_code(code: str) -> str:
        return str(code or "").split(".")[0].strip().upper()

    def _get_effective_run_context(self) -> DecisionRunContext:
        if isinstance(self._run_context_override, DecisionRunContext):
            return self._run_context_override
        return build_decision_run_context(prefer_realtime=True)

    def _set_run_context_override(
        self,
        run_context: DecisionRunContext | Dict[str, Any] | None,
    ) -> DecisionRunContext:
        if isinstance(run_context, DecisionRunContext):
            self._run_context_override = run_context
        elif isinstance(run_context, dict):
            self._run_context_override = DecisionRunContext.from_dict(run_context)
        else:
            self._run_context_override = build_decision_run_context(prefer_realtime=True)
        return self._run_context_override

    def _clear_run_context_override(self):
        self._run_context_override = None

    def _apply_symbol_override(
        self,
        raw_context: Dict[str, Any],
        code: str,
        fallback_name: str = "",
    ) -> Dict[str, Any]:
        symbol_raw = dict(raw_context.get("symbol", {}) or {})
        resolved_name = self._resolve_runtime_symbol_name(code, fallback_name)
        asset_type = self._infer_asset_type_for_code(code, str(symbol_raw.get("asset_type", "") or ""))
        current_view = "etf" if asset_type == "ETF" else "stock"
        indicators = list(symbol_raw.get("indicators", []) or [])
        same_symbol = self._normalize_symbol_code(symbol_raw.get("code", "")) == self._normalize_symbol_code(code)
        raw_context["symbol"] = {
            "code": code,
            "name": resolved_name or str(symbol_raw.get("name", "") or ""),
            "asset_type": asset_type,
            "current_view": current_view,
            "latest_close": float(symbol_raw.get("latest_close", 0.0) or 0.0) if same_symbol else 0.0,
            "latest_change_pct": float(symbol_raw.get("latest_change_pct", 0.0) or 0.0) if same_symbol else 0.0,
            "latest_volume": float(symbol_raw.get("latest_volume", 0.0) or 0.0) if same_symbol else 0.0,
            "data_points": int(symbol_raw.get("data_points", 0) or 0) if same_symbol else 0,
            "date_start": str(symbol_raw.get("date_start", "") or "") if same_symbol else "",
            "date_end": str(symbol_raw.get("date_end", "") or "") if same_symbol else "",
            "indicators": indicators,
        }
        return raw_context

    def _on_mode_changed(self, _index=None):
        self._current_mode = self.mode_combo.currentData() or DECISION_MODE_POSITION_SCAN
        self.symbol_input.setVisible(False)
        self.watchlist_group_combo.setVisible(False)
        hints = {
            DECISION_MODE_POSITION_SCAN: "持仓巡检: 自动读取当前券商持仓，逐只生成持有/加仓/减仓/卖出决策",
            DECISION_MODE_CANDIDATE_POOL_SCAN: "候选池巡检: 先按量化规则生成今日候选池，再交给AI逐只评估买入机会",
        }
        self.mode_hint_label.setText(hints.get(self._current_mode, ""))
        btn_texts = {
            DECISION_MODE_POSITION_SCAN: "🔎 开始持仓巡检",
            DECISION_MODE_CANDIDATE_POOL_SCAN: "🔎 开始候选池巡检",
        }
        self.analyze_btn.setText(btn_texts.get(self._current_mode, "🔍 开始"))

    def _refresh_watchlist_groups(self):
        try:
            wm = WatchlistManager()
            groups = wm.get_all_groups()
        except Exception:
            groups = []
        current = self.watchlist_group_combo.currentText()
        self.watchlist_group_combo.clear()
        for g in groups:
            self.watchlist_group_combo.addItem(g)
        if current and self.watchlist_group_combo.findText(current) >= 0:
            self.watchlist_group_combo.setCurrentText(current)

    def _request_runtime_raw_context(self, code: str = "", name: str = "") -> Dict[str, Any]:
        raw_context: Dict[str, Any] = {}
        if not self.context_provider:
            return raw_context
        run_context = self._get_effective_run_context()
        symbol_override = {
            "code": code,
            "name": name,
            "asset_type": self._infer_asset_type_for_code(code),
        }
        try:
            raw_context = self.context_provider(
                symbol_override=symbol_override,
                run_context=run_context.to_dict(),
            )
        except TypeError:
            try:
                raw_context = self.context_provider(symbol_override=symbol_override)
            except TypeError:
                try:
                    raw_context = self.context_provider()
                except Exception:
                    raw_context = {}
            except Exception:
                raw_context = {}
        except Exception:
            raw_context = {}
        raw_context = raw_context if isinstance(raw_context, dict) else {}
        raw_context["decision_run_context"] = run_context.to_dict()
        return raw_context

    def _build_runtime_context(self) -> AgentRuntimeContext:
        override_code = self.symbol_input.text().strip()
        raw_context = self._request_runtime_raw_context(override_code)
        if override_code:
            raw_context = self._apply_symbol_override(raw_context, override_code)

        context = AgentContextService.from_raw(raw_context)
        return context

    def _build_runtime_context_for_symbol(self, code: str, name: str = "") -> AgentRuntimeContext:
        raw_context = self._request_runtime_raw_context(code, name or "")
        raw_context = self._apply_symbol_override(raw_context, code, name or "")
        context = AgentContextService.from_raw(raw_context)
        return context

    def _resolve_model_config(self, *, show_dialog: bool = True) -> Optional[Dict[str, str]]:
        model = self.model_combo.currentText()
        model_configs = self._ai_config.get("model_configs", {})
        config = model_configs.get(model, {})
        api_key = config.get("api_key", "")
        base_url = config.get("base_url", "")
        if not api_key:
            logger.warning("AI 模型未配置可用 API Key: %s", model)
            if show_dialog:
                QMessageBox.warning(self, "提示", f"请先在智能体设置中配置模型 {model} 的 API Key")
            return None
        return {"model": model, "api_key": api_key, "base_url": base_url}

    def _clear_progress_cards(self):
        self._progress_cards = []
        while self.progress_cards_layout.count() > 1:
            item = self.progress_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._clear_review_cards()

    def _clear_review_cards(self):
        while self.process_review_layout.count() > 1:
            item = self.process_review_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _clone_progress_card(self, card: CollapsibleStepCard) -> CollapsibleStepCard:
        cloned = CollapsibleStepCard(
            title=getattr(card, "title_text", ""),
            detail=getattr(card, "detail_text", ""),
            status=getattr(card, "status", "pending"),
            action_label=card.action_btn.text() if hasattr(card, "action_btn") else "",
            action_callback=getattr(card, "_action_callback", None),
            preview_path=getattr(card, "_preview_path", ""),
        )
        if card.header_btn.isChecked():
            cloned.expand()
        for idx in range(card.children_layout.count()):
            child_item = card.children_layout.itemAt(idx)
            child_widget = child_item.widget()
            if isinstance(child_widget, CollapsibleStepCard):
                cloned.add_child_card(self._clone_progress_card(child_widget))
        return cloned

    def _sync_progress_review_tab(self):
        self._clear_review_cards()
        for card in self._progress_cards:
            self.process_review_layout.insertWidget(
                self.process_review_layout.count() - 1,
                self._clone_progress_card(card),
            )

    def _last_progress_card(self) -> Optional[CollapsibleStepCard]:
        return self._progress_cards[-1] if self._progress_cards else None

    def _find_progress_card(self, contains_text: str) -> Optional[CollapsibleStepCard]:
        for card in self._progress_cards:
            if contains_text in getattr(card, "title_text", ""):
                return card
        return None

    def _finish_last_progress_card(self):
        last = self._last_progress_card()
        if last is not None and getattr(last, "status", "") == "running":
            last.set_content(
                last.title_text,
                last.detail_text,
                status="done",
                action_label=last.action_btn.text() if hasattr(last, "action_btn") else "",
                action_callback=getattr(last, "_action_callback", None),
            )
            self._sync_progress_review_tab()

    def _parse_step_text(self, step: str) -> tuple[str, str]:
        clean = (step or "").strip()
        if "：" in clean:
            title, detail = clean.split("：", 1)
            return title.strip(), detail.strip()
        if ":" in clean:
            title, detail = clean.split(":", 1)
            return title.strip(), detail.strip()
        return clean, ""

    def _tool_display_name(self, tool_name: str) -> str:
        mapping = {
            "context_snapshot": "上下文快照",
            "symbol_technical_snapshot": "技术面摘要",
            "symbol_news_snapshot": "消息面摘要",
            "symbol_fundamental_snapshot": "基本面摘要",
            "symbol_analysis_packet": "深度分析资料",
            "current_kline_image": "K线截图",
            "position_snapshot": "持仓快照",
            "watchlist_snapshot": "自选快照",
            "compare_symbols": "标的对比",
        }
        return mapping.get(tool_name, tool_name)

    def _truncate_text(self, text: str, limit: int = 220) -> str:
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + " ..."

    def _open_local_evidence_path(self, path: str):
        if not path:
            return
        normalized = os.path.abspath(path)
        if not os.path.exists(normalized):
            QMessageBox.warning(self, "提示", f"证据文件不存在：\n{normalized}")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(normalized))
        if not opened:
            QMessageBox.warning(self, "提示", f"无法打开证据文件：\n{normalized}")

    def _attach_tool_subcards(self, prepared, parent_card: Optional[CollapsibleStepCard] = None):
        if parent_card is None:
            parent_card = self._find_progress_card("执行领域工具链")
        if parent_card is None:
            return
        parent_card.clear_children()
        for idx, item in enumerate(prepared.evidence_items, start=1):
            tool_label = self._tool_display_name(item.tool_name)
            detail_lines = [
                f"工具标识: {item.tool_name}",
                f"证据标题: {item.title}",
                f"摘要: {item.summary}",
            ]
            preview = self._truncate_text(item.content, 260)
            if preview:
                detail_lines.extend(["", "关键内容预览:", preview])
            metadata = item.metadata or {}
            file_path = str(metadata.get("image_path") or metadata.get("file_path") or "").strip()
            action_callback = None
            action_label = ""
            preview_path = ""
            if file_path:
                detail_lines.extend(["", f"原始证据路径: {file_path}"])
                action_label = "打开证据文件/图片"
                action_callback = lambda p=file_path: self._open_local_evidence_path(p)
            image_path = str(metadata.get("image_path") or "").strip()
            if image_path and os.path.exists(image_path):
                preview_path = image_path
            child_card = CollapsibleStepCard(
                title=f"子步骤 {idx}: {tool_label}",
                detail="\n".join(detail_lines),
                status="done",
                action_label=action_label,
                action_callback=action_callback,
                preview_path=preview_path,
            )
            parent_card.add_child_card(child_card)
        if prepared.evidence_items:
            parent_card.expand()
        self._sync_progress_review_tab()

    def _set_progress_steps(self, title: str, steps: List[str]):
        self.progress_hint_label.setText(title)
        self._clear_progress_cards()
        for idx, step in enumerate(steps, start=1):
            step_title, detail = self._parse_step_text(step)
            card = CollapsibleStepCard(
                title=f"步骤 {idx}: {step_title}",
                detail=detail or step,
                status="running" if idx == len(steps) else "done",
            )
            if idx == len(steps):
                card.expand()
            self._progress_cards.append(card)
            self.progress_cards_layout.insertWidget(self.progress_cards_layout.count() - 1, card)
        self.progress_scroll.verticalScrollBar().setValue(0)
        self._sync_progress_review_tab()

    def _append_progress_step(self, step: str):
        previous = self._last_progress_card()
        if previous is not None and getattr(previous, "status", "") == "running":
            previous.set_content(
                previous.title_text,
                previous.detail_text,
                status="done",
                action_label=previous.action_btn.text() if hasattr(previous, "action_btn") else "",
                action_callback=getattr(previous, "_action_callback", None),
            )
        existing_count = max(0, self.progress_cards_layout.count() - 1)
        step_title, detail = self._parse_step_text(step)
        card = CollapsibleStepCard(
            title=f"步骤 {existing_count + 1}: {step_title}",
            detail=detail or step,
            status="running",
        )
        card.expand()
        self._progress_cards.append(card)
        self.progress_cards_layout.insertWidget(self.progress_cards_layout.count() - 1, card)
        QTimer.singleShot(
            0,
            lambda: self.progress_scroll.verticalScrollBar().setValue(
                self.progress_scroll.verticalScrollBar().maximum()
            ),
        )
        self._sync_progress_review_tab()

    def _build_prepared_steps_summary(
        self,
        context: AgentRuntimeContext,
        prepared,
        *,
        model_name: str,
        scenario_label: str,
    ) -> List[str]:
        symbol_name = context.symbol.name or "-"
        symbol_code = context.symbol.code or "-"
        summary_lines = [
            f"识别任务场景：{scenario_label}，目标标的为 {symbol_name}({symbol_code})。",
            f"读取运行上下文：账户{'已连接' if context.broker.connected else '未连接'}，当前任务模式为交易决策。",
        ]
        if prepared.executed_tools:
            summary_lines.append(
                "执行领域工具链：" + " -> ".join(prepared.executed_tools)
            )
        if prepared.evidence_items:
            evidence_bits = []
            for item in prepared.evidence_items[:6]:
                evidence_bits.append(f"{item.title}（{item.summary}）")
            summary_lines.append("提取关键证据摘要：" + "；".join(evidence_bits))
        if prepared.evidence_report_path:
            summary_lines.append(f"生成证据存档：{prepared.evidence_report_path}")
        summary_lines.extend([
            "将结构化证据、输出协议和风控要求一起注入最终提示词。",
            f"调用模型 `{model_name}` 进入推理阶段，等待生成多空分析和结构化交易决策。",
        ])
        return summary_lines

    def _reset_current_result(self):
        self._current_decision = None
        self._current_risk_result = None
        self.approve_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)

    def _on_analyze_clicked(self):
        model_cfg = self._resolve_model_config()
        if not model_cfg:
            return

        if self._current_mode == DECISION_MODE_POSITION_SCAN:
            codes = self._collect_scan_codes_for_freshness("position")
            self._run_with_freshness_check(codes, lambda: self._start_position_scan(model_cfg))
            return
        if self._current_mode == DECISION_MODE_CANDIDATE_POOL_SCAN:
            items = self._load_candidate_pool_items(refresh=True)
            if not items:
                QMessageBox.warning(self, "提示", "当前候选池为空，请检查本地行情数据或股票池配置")
                return
            codes = [item.get("code", "") for item in items if item.get("code")]
            self._run_with_freshness_check(codes, lambda items=items: self._start_candidate_pool_scan(model_cfg, items))
            return
        QMessageBox.warning(self, "提示", "当前仅支持持仓巡检和候选池巡检")

    def _collect_scan_codes_for_freshness(self, scan_type: str) -> list:
        codes: list[str] = []
        if scan_type == "position":
            account_panel = self._find_account_panel()
            if account_panel:
                try:
                    positions = account_panel.get_live_positions()
                    codes = [p.get("code", "") for p in positions if p.get("code")]
                except Exception:
                    pass
        elif scan_type == "watchlist":
            group_name = self.watchlist_group_combo.currentText()
            if group_name:
                try:
                    wm = WatchlistManager()
                    codes = wm.get_group_stocks(group_name)
                except Exception:
                    pass
        elif scan_type == "candidate_pool":
            try:
                codes = [item.get("code", "") for item in self._load_candidate_pool_items(refresh=True)]
            except Exception:
                codes = []
        return [c for c in codes if c]

    def _run_with_freshness_check(self, codes: list, proceed_callback):
        try:
            from trading_app.services.data_freshness_service import check_parquet_freshness
        except ImportError:
            from services.data_freshness_service import check_parquet_freshness

        stale_items = []
        for code in codes[:20]:
            fresh, info = check_parquet_freshness(code)
            if not fresh:
                stale_items.append((code, info))

        if not stale_items:
            proceed_callback()
            return

        stale_preview = "\n".join(f"  {c}: 最新 {d}" for c, d in stale_items[:5])
        if len(stale_items) > 5:
            stale_preview += f"\n  ... 还有 {len(stale_items) - 5} 只"

        dlg = QMessageBox(self)
        dlg.setWindowTitle("本地数据未更新")
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setText(
            f"检测到 {len(stale_items)} 只股票的 K 线数据不是最新的：\n\n"
            f"{stale_preview}\n\n"
            "使用过期数据分析可能导致结论不准确。"
        )
        btn_update = dlg.addButton("先更新数据再分析", QMessageBox.ButtonRole.AcceptRole)
        btn_continue = dlg.addButton("使用现有数据继续", QMessageBox.ButtonRole.RejectRole)
        btn_cancel = dlg.addButton("取消", QMessageBox.ButtonRole.DestructiveRole)
        dlg.setDefaultButton(btn_update)
        dlg.exec()

        clicked = dlg.clickedButton()
        if clicked == btn_cancel:
            return
        if clicked == btn_continue:
            proceed_callback()
            return

        trade_window = self._find_trade_window()
        if trade_window and hasattr(trade_window, "freshness_guard"):
            guard = trade_window.freshness_guard
            self.analyze_btn.setEnabled(False)
            self.progress_label.setText("正在检测 miniQMT 数据源并更新...")
            self.stack.setCurrentIndex(1)

            def _on_done(ok, msg):
                try:
                    guard.update_finished.disconnect(_on_done)
                except (TypeError, RuntimeError):
                    pass
                self.progress_label.setText(msg)
                if not ok:
                    self.analyze_btn.setEnabled(True)
                    self.stack.setCurrentIndex(0)

            guard.update_finished.connect(_on_done)
            guard.ensure_fresh_then_run(
                [c for c, _ in stale_items],
                proceed_callback,
                include_indices=True,
                prefer_realtime=True,
            )
        else:
            QMessageBox.information(
                self, "提示",
                "当前环境未连接 DataFreshnessGuard，请通过 AI 交易决策窗口启动。\n"
                "将使用现有数据继续。",
            )
            proceed_callback()

    def _start_single_decision(
        self,
        context: AgentRuntimeContext,
        model_cfg: Dict[str, str],
        *,
        user_prompt: str | None = None,
        scan_item: Optional[Dict[str, Any]] = None,
    ):
        self.analyze_btn.setEnabled(False)
        self._reset_current_result()
        self.stack.setCurrentIndex(1)
        self._full_response = ""
        self._context_for_decision = context
        self._current_scan_item = scan_item
        self._stream_started = False
        scenario_label = "持仓巡检单票分析" if scan_item else "单股交易决策"
        self.progress_label.setText(
            f"正在收集 {context.symbol.name}({context.symbol.code}) 的多维度数据..."
        )
        self._set_progress_steps(
            "执行步骤概要",
            [
                f"接收请求并识别场景：{scenario_label}。",
                f"解析目标标的：{context.symbol.name or '-'}({context.symbol.code or '-'})。",
                "准备运行上下文，包括账户、图表、行情和可用持仓信息。",
                "开始执行领域工具，采集技术面、消息面、基本面和图表证据。",
            ],
        )

        system_prompt = AgentPromptBuilder.build_system_prompt(
            "你是一个专业的股票交易决策分析师。",
            context,
            task_mode=TASK_MODE_TRADE_DECISION,
        )
        latest_user_content = user_prompt or AgentPromptBuilder.build_quick_task_prompt(
            TASK_MODE_TRADE_DECISION,
            context,
        )
        prepared = self.agent_runtime.prepare_request(
            base_system_prompt=system_prompt,
            context=context,
            task_mode=TASK_MODE_TRADE_DECISION,
            chat_history=[],
            latest_user_content=latest_user_content,
        )
        self._set_progress_steps(
            "执行步骤概要",
            self._build_prepared_steps_summary(
                context,
                prepared,
                model_name=model_cfg["model"],
                scenario_label=scenario_label,
            ),
        )
        self._attach_tool_subcards(prepared)
        self.progress_label.setText("数据收集完成，AI 正在分析决策...")
        self._append_progress_step("模型已开始流式生成分析结果，正在持续接收输出片段。")

        ChatThread = _get_chat_thread_class()
        self._chat_thread = ChatThread(
            model_cfg["api_key"],
            model_cfg["base_url"],
            model_cfg["model"],
            prepared.system_prompt,
            prepared.messages,
            stream=True,
            request_timeout_seconds=SCAN_SUBAGENT_REQUEST_TIMEOUT_SECONDS,
            log_context=f"single:{self._current_mode}:{context.symbol.code or 'unknown'}",
        )
        self._chat_thread.message_received.connect(self._on_stream_message)
        self._chat_thread.finished_signal.connect(self._on_analysis_finished)
        self._chat_thread.start()

    def _start_position_scan(
        self,
        model_cfg: Dict[str, str],
        *,
        scan_source: str = "manual",
        scheduled_task_id: str = "",
    ):
        if self._run_context_override is None:
            self._set_run_context_override(None)
        account_panel = self._find_account_panel()
        if account_panel is None:
            QMessageBox.warning(self, "提示", "未找到账户面板")
            return ""
        positions = account_panel.get_live_positions()
        if not positions:
            QMessageBox.warning(self, "提示", "当前无可巡检持仓，请先连接券商并确认持仓数据")
            return ""

        scan_run_id = self._begin_scan_session(
            items=positions,
            scan_source=scan_source,
            scheduled_task_id=scheduled_task_id,
        )
        if not scan_run_id:
            return ""
        self._active_model_cfg = model_cfg
        self.scan_table.setRowCount(0)
        self.analysis_display.clear()
        self._populate_decision_card(None, None)
        self.stack.setCurrentIndex(1)
        self.progress_label.setText(f"准备开始持仓巡检，共 {len(positions)} 只持仓...")
        self._set_progress_steps(
            "执行步骤概要",
            [
                f"接收持仓巡检请求，本轮共识别到 {len(positions)} 只有效持仓。",
                f"选择并行子代理模式处理，最大并发数设为 {SCAN_SUBAGENT_CONCURRENCY}。",
                "每只持仓都会单独完成：上下文构建 -> 证据采集 -> 模型推理 -> 决策提取 -> 风控评估。",
                "巡检汇总表会在每只股票完成后实时追加结果。",
            ],
        )
        self.result_tabs.setCurrentWidget(self.scan_table)
        self._launch_scan_subagents()
        return scan_run_id

    def _start_watchlist_scan(self, model_cfg: Dict[str, str]):
        group_name = self.watchlist_group_combo.currentText()
        if not group_name:
            QMessageBox.warning(self, "提示", "请选择一个自选分组")
            return
        try:
            wm = WatchlistManager()
            codes = wm.get_group_stocks(group_name)
        except Exception as exc:
            QMessageBox.warning(self, "提示", f"读取自选分组失败: {exc}")
            return
        if not codes:
            QMessageBox.warning(self, "提示", f"分组「{group_name}」中暂无股票")
            return

        items: List[Dict[str, Any]] = []
        for code in codes:
            name = self._resolve_runtime_symbol_name(code)
            items.append({"code": code, "name": name or code})

        self._scan_queue = items
        self._scan_results = []
        self._current_scan_index = 0
        self._scan_in_progress = True
        self._scan_total_count = len(items)
        self._scan_completed_count = 0
        self._scan_active_workers = {}
        self._scan_worker_states = {}
        self._active_model_cfg = model_cfg
        self.scan_table.setRowCount(0)
        self.analysis_display.clear()
        self._populate_decision_card(None, None)
        self.stack.setCurrentIndex(1)
        self.progress_label.setText(f"准备开始自选巡检「{group_name}」，共 {len(items)} 只...")
        self._set_progress_steps(
            "执行步骤概要",
            [
                f"接收自选巡检请求（分组: {group_name}），本轮共 {len(items)} 只标的。",
                f"选择并行子代理模式处理，最大并发数设为 {SCAN_SUBAGENT_CONCURRENCY}。",
                "每只标的都会单独完成：上下文构建 -> 证据采集 -> 模型推理 -> 决策提取 -> 风控评估。",
                "巡检汇总表会在每只股票完成后实时追加结果。",
            ],
        )
        self.result_tabs.setCurrentWidget(self.scan_table)
        self._launch_scan_subagents()

    def _load_candidate_pool_items(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        try:
            cfg = self.stock_pool_service.get_config()
            return self.stock_pool_service.get_candidate_items(
                refresh=refresh,
                limit=int(cfg.ai_review_limit or 10),
                run_context=self._get_effective_run_context().to_dict(),
            )
        except Exception as exc:
            logger.exception("加载候选池失败")
            QMessageBox.warning(self, "提示", f"加载候选池失败: {exc}")
            return []

    def _start_candidate_pool_scan(
        self,
        model_cfg: Dict[str, str],
        items: Optional[List[Dict[str, Any]]] = None,
        *,
        scan_source: str = "manual",
        scheduled_task_id: str = "",
    ):
        if self._run_context_override is None:
            self._set_run_context_override(None)
        candidate_items = list(items or self._load_candidate_pool_items(refresh=True))
        if not candidate_items:
            QMessageBox.warning(self, "提示", "当前候选池为空，请先生成候选池")
            return ""

        scan_run_id = self._begin_scan_session(
            items=candidate_items,
            scan_source=scan_source,
            scheduled_task_id=scheduled_task_id,
        )
        if not scan_run_id:
            return ""
        self._active_model_cfg = model_cfg
        self.scan_table.setRowCount(0)
        self.analysis_display.clear()
        self._populate_decision_card(None, None)
        self.stack.setCurrentIndex(1)
        snapshot = self.stock_pool_service.get_snapshot()
        generated_at = str(snapshot.get("generated_at", "") or "")
        self.progress_label.setText(f"准备开始候选池巡检，共 {len(candidate_items)} 只...")
        self._set_progress_steps(
            "执行步骤概要",
            [
                f"候选池已生成，最新快照时间: {generated_at or '未知'}。",
                f"本轮按量化总分选取前 {len(candidate_items)} 只标的进入 AI 复核。",
                f"选择并行子代理模式处理，最大并发数设为 {SCAN_SUBAGENT_CONCURRENCY}。",
                "每只标的会单独完成：上下文构建 -> 证据采集 -> 模型推理 -> 决策提取 -> 风控评估。",
            ],
        )
        self.result_tabs.setCurrentWidget(self.scan_table)
        self._launch_scan_subagents()
        return scan_run_id

    def _begin_scan_session(
        self,
        *,
        items: List[Dict[str, Any]],
        scan_source: str,
        scheduled_task_id: str,
    ) -> str:
        if self._scan_in_progress:
            logger.warning(
                "忽略新的扫描启动请求: source=%s task_id=%s current_run=%s current_source=%s",
                scan_source,
                scheduled_task_id,
                self._active_scan_run_id,
                self._active_scan_source,
            )
            return ""
        self._scan_queue = list(items)
        self._scan_results = []
        self._current_scan_index = 0
        self._scan_in_progress = True
        self._scan_total_count = len(items)
        self._scan_completed_count = 0
        self._scan_active_workers = {}
        self._scan_worker_states = {}
        self._active_scan_run_id = uuid4().hex
        self._active_scan_source = str(scan_source or "manual")
        self._active_scan_task_id = str(scheduled_task_id or "")
        return self._active_scan_run_id

    def _launch_scan_subagents(self):
        is_candidate_pool = self._current_mode == DECISION_MODE_CANDIDATE_POOL_SCAN
        while self._scan_queue and len(self._scan_active_workers) < SCAN_SUBAGENT_CONCURRENCY:
            item = self._scan_queue.pop(0)
            context = self._build_runtime_context_for_symbol(item["code"], item["name"])
            if is_candidate_pool:
                prompt = self._build_candidate_pool_scan_prompt(context, item)
            else:
                prompt = self._build_position_scan_prompt(context, item)
            worker_id = f"{item['code']}::{self._current_scan_index}"
            self._current_scan_index += 1

            system_prompt = AgentPromptBuilder.build_system_prompt(
                "你是一个专业的股票交易决策分析师。",
                context,
                task_mode=TASK_MODE_TRADE_DECISION,
            )
            prepared = self.agent_runtime.prepare_request(
                base_system_prompt=system_prompt,
                context=context,
                task_mode=TASK_MODE_TRADE_DECISION,
                chat_history=[],
                latest_user_content=prompt,
            )

            ChatThread = _get_chat_thread_class()
            worker = ChatThread(
                self._active_model_cfg["api_key"],
                self._active_model_cfg["base_url"],
                self._active_model_cfg["model"],
                prepared.system_prompt,
                prepared.messages,
                stream=False,
                request_timeout_seconds=SCAN_SUBAGENT_REQUEST_TIMEOUT_SECONDS,
                log_context=f"scan:{item['code']}:{item['name']}",
            )
            self._scan_active_workers[worker_id] = worker
            self._scan_worker_states[worker_id] = {
                "response": "",
                "context": context,
                "scan_item": item,
                "prepared": prepared,
                "started_at": datetime.now(),
                "first_chunk_at": None,
            }
            if is_candidate_pool:
                scan_label = "候选池巡检"
            else:
                scan_label = "持仓决策"
            logger.info(
                "启动巡检子任务: %s(%s) stream=%s timeout=%.1fs",
                item["name"],
                item["code"],
                False,
                SCAN_SUBAGENT_REQUEST_TIMEOUT_SECONDS,
            )
            self._append_progress_step(
                f"启动子代理：{item['name']}({item['code']})，准备独立生成{scan_label}。"
            )
            self._attach_tool_subcards(prepared, parent_card=self._last_progress_card())
            worker.message_received.connect(
                lambda content, is_error, wid=worker_id: self._on_scan_worker_message(wid, content, is_error)
            )
            worker.finished_signal.connect(
                lambda wid=worker_id: self._on_scan_worker_finished(wid)
            )
            worker.start()

        self._update_scan_progress_label()

    def _update_scan_progress_label(self):
        running_names = [
            state["scan_item"].get("name") or state["scan_item"].get("code")
            for state in self._scan_worker_states.values()
        ]
        running_text = "、".join(running_names[:3]) if running_names else "无"
        if self._current_mode == DECISION_MODE_CANDIDATE_POOL_SCAN:
            mode_label = "候选池巡检"
        else:
            mode_label = "持仓巡检"
        self.progress_label.setText(
            f"{mode_label}中: 已完成 {self._scan_completed_count}/{self._scan_total_count} | "
            f"运行中 {len(self._scan_active_workers)} 个子代理 | 当前: {running_text}"
        )

    def _build_position_scan_prompt(self, context: AgentRuntimeContext, position: Dict[str, Any]) -> str:
        cost = float(position.get("cost_price", 0) or 0)
        profit_rate = float(position.get("profit_rate", 0) or 0) * 100
        can_use = int(position.get("can_use_volume", 0) or 0)
        volume = int(position.get("volume", 0) or 0)
        market_value = float(position.get("market_value", 0) or 0)
        base_prompt = AgentPromptBuilder.build_quick_task_prompt(TASK_MODE_TRADE_DECISION, context)
        extra_lines = [
            "",
            "这是持仓巡检场景，请结合当前已经持有该股票的事实做判断。",
            f"- 当前持仓数量: {volume} 股，可卖数量: {can_use} 股",
            f"- 持仓成本价: {cost:.3f}" if cost > 0 else "- 持仓成本价: 未知",
            f"- 当前持仓盈亏: {profit_rate:+.2f}%",
            f"- 当前持仓市值: ¥{market_value:,.2f}",
            "",
            "请重点判断：继续持有、加仓、减仓、卖出、还是继续观察。",
            "如果建议卖出或减仓，请明确给出触发依据；如果建议继续持有，也要说明需要继续跟踪的风险信号。",
        ]
        return "\n".join([base_prompt, *extra_lines]).strip()

    def _build_watchlist_scan_prompt(self, context: AgentRuntimeContext, item: Dict[str, Any]) -> str:
        base_prompt = AgentPromptBuilder.build_quick_task_prompt(TASK_MODE_TRADE_DECISION, context)
        extra_lines = [
            "",
            "这是自选股巡检场景，当前尚未持有该股票，请从买入机会角度进行评估。",
            "请重点判断：当前是否适合买入、应该继续观望、还是应该从自选中移除。",
            "如果建议买入，请给出建议的买入价位区间、止损价、仓位建议；",
            "如果建议观望，请说明需要等待什么条件或信号才值得介入。",
        ]
        return "\n".join([base_prompt, *extra_lines]).strip()

    def _build_candidate_pool_scan_prompt(self, context: AgentRuntimeContext, item: Dict[str, Any]) -> str:
        base_prompt = AgentPromptBuilder.build_quick_task_prompt(TASK_MODE_TRADE_DECISION, context)
        factors = item.get("factors", {}) or {}
        reasons = list(item.get("reasons", []) or [])
        extra_lines = [
            "",
            "这是自动候选池巡检场景，该股票由量化规则初筛后进入候选池，请在规则筛选结论基础上继续做买入机会复核。",
            f"- 候选池排名: #{int(item.get('rank', 0) or 0)} / 量化总分: {float(item.get('score', 0.0) or 0.0):.2f}",
            f"- 所属行业: {item.get('industry', '') or '未知'}",
            f"- 20日涨幅: {float(factors.get('momentum_20', 0.0) or 0.0):+.2%}",
            f"- 60日涨幅: {float(factors.get('momentum_60', 0.0) or 0.0):+.2%}",
            f"- 相对基准超额: {float(factors.get('excess_return_20', 0.0) or 0.0):+.2%}",
            f"- 量能比: {float(factors.get('volume_ratio', 0.0) or 0.0):.2f}",
            f"- 20日回撤: {float(factors.get('drawdown_20', 0.0) or 0.0):+.2%}",
            f"- 入池原因: {'；'.join(reasons) if reasons else '量化初筛通过'}",
            "",
            "请重点判断：当前是否适合买入、应继续观望、还是应从候选池剔除。",
            "本场景下若暂不买入，请优先使用 action=watch（观望）或 action=reject（剔除），不要输出 hold。",
            "如果建议买入，请给出仓位建议、止损位和触发条件；如果建议观望或剔除，请明确说明否决原因。",
        ]
        return "\n".join([base_prompt, *extra_lines]).strip()

    def _on_scan_worker_message(self, worker_id: str, content: str, is_error: bool):
        state = self._scan_worker_states.get(worker_id)
        if not state:
            return
        if state.get("first_chunk_at") is None:
            state["first_chunk_at"] = datetime.now()
            started_at = state.get("started_at")
            if isinstance(started_at, datetime):
                delay = (state["first_chunk_at"] - started_at).total_seconds()
                item = state.get("scan_item", {})
                logger.info(
                    "巡检子任务收到完整结果: %s(%s) delay=%.2fs error=%s",
                    item.get("name"),
                    item.get("code"),
                    delay,
                    is_error,
                )
        if is_error:
            state["response"] += f"\n\n[错误] {content}"
        else:
            state["response"] += content
        self._update_scan_progress_label()

    def _on_stream_message(self, content: str, is_error: bool):
        if not self._stream_started:
            self._stream_started = True
            self._append_progress_step("模型已返回首段内容，进入结果生成与结构化提取阶段。")
        if is_error:
            self._full_response += f"\n\n[错误] {content}"
        else:
            self._full_response += content
        if self._current_mode == DECISION_MODE_POSITION_SCAN and self._current_scan_item:
            self.progress_label.setText(
                f"巡检 {self._current_scan_item.get('name', '')} 中... ({len(self._full_response)} 字)"
            )
        else:
            self.progress_label.setText(f"AI 分析中... ({len(self._full_response)} 字)")

    def _on_scan_worker_finished(self, worker_id: str):
        worker = self._scan_active_workers.pop(worker_id, None)
        state = self._scan_worker_states.pop(worker_id, None)
        if worker is not None:
            worker.deleteLater()
        if not state:
            self._update_scan_progress_label()
            return

        started_at = state.get("started_at")
        first_chunk_at = state.get("first_chunk_at")
        elapsed = (datetime.now() - started_at).total_seconds() if isinstance(started_at, datetime) else -1.0
        first_delay = (first_chunk_at - started_at).total_seconds() if isinstance(started_at, datetime) and isinstance(first_chunk_at, datetime) else -1.0
        item = state.get("scan_item", {})
        logger.info(
            "巡检子任务完成: %s(%s) elapsed=%.2fs first_result=%.2fs response_len=%d",
            item.get("name"),
            item.get("code"),
            elapsed,
            first_delay,
            len(state.get("response", "")),
        )

        result = self._build_analysis_result(
            state.get("response", ""),
            state["context"],
            state["scan_item"],
        )
        self._append_scan_result(result)
        if self.scan_table.currentRow() < 0:
            self._display_result(result, switch_to_details=False, emit_decision=False)

        record = self.decision_tracker.save_decision(
            result["decision"] or TradeDecision(
                action=TradeAction.HOLD.value,
                symbol_code=result["symbol_code"],
                symbol_name=result["symbol_name"],
            ),
            result["risk_result"] or self.risk_guard.evaluate(
                TradeDecision(
                    action=TradeAction.HOLD.value,
                    symbol_code=result["symbol_code"],
                    symbol_name=result["symbol_name"],
                ),
                BrokerContext(),
            ),
            DecisionOutcome.INSPECTED.value,
        )
        result["decision_record_id"] = record.record_id if record else ""
        if result["decision"] is not None:
            action_label = TRADE_ACTION_LABELS.get(result["decision"].action, result["decision"].action)
            risk_level = (
                result["risk_result"].overall_risk_level.upper()
                if result["risk_result"] is not None else "-"
            )
            self._append_progress_step(
                f"子代理完成：{result['symbol_name']}({result['symbol_code']}) -> {action_label}，"
                f"置信度 {result['decision'].confidence:.0%}，风险 {risk_level}。"
            )
        else:
            self._append_progress_step(
                f"子代理完成：{result['symbol_name']}({result['symbol_code']})，但未能提取有效结构化决策。"
            )

        self._scan_completed_count += 1
        if not self._scan_queue and not self._scan_active_workers:
            self._scan_in_progress = False
            self.analyze_btn.setEnabled(True)
            self.stack.setCurrentIndex(2)
            if self._current_mode == DECISION_MODE_CANDIDATE_POOL_SCAN:
                scan_label = "候选池巡检"
            else:
                scan_label = "持仓巡检"
            self.decision_status_label.setText(f"✅ {scan_label}完成，共 {len(self._scan_results)} 只")
            self.decision_status_label.setStyleSheet("color: green; font-weight: bold;")
            self._append_progress_step(f"全部子代理已完成，本轮{scan_label}结束，结果已写入巡检汇总和决策记录。")
            self._finish_last_progress_card()
            self._refresh_history()
            self.result_tabs.setCurrentWidget(self.scan_table)
            if self._scan_results:
                self.scan_table.selectRow(0)
            self.scan_completed.emit({
                "mode": self._current_mode,
                "results": list(self._scan_results),
                "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "scan_run_id": self._active_scan_run_id,
                "scan_source": self._active_scan_source,
                "scheduled_task_id": self._active_scan_task_id,
            })
            self._clear_run_context_override()
            self._try_scan_notification()
            return

        self._launch_scan_subagents()

    def _on_analysis_finished(self):
        self.stack.setCurrentIndex(2)
        result = self._build_analysis_result(self._full_response, self._context_for_decision, self._current_scan_item)
        self.analyze_btn.setEnabled(True)
        if result["decision"] is not None:
            action_label = TRADE_ACTION_LABELS.get(result["decision"].action, result["decision"].action)
            risk_level = (
                result["risk_result"].overall_risk_level.upper()
                if result["risk_result"] is not None else "-"
            )
            self._append_progress_step(
                f"模型输出已解析完成：建议 {action_label}，置信度 {result['decision'].confidence:.0%}，风险 {risk_level}。"
            )
        else:
            self._append_progress_step("模型输出已返回，但未能解析出有效的结构化交易决策。")
        self._finish_last_progress_card()
        self._display_result(result, switch_to_details=True, emit_decision=True)
        self._clear_run_context_override()

    def _build_analysis_result(
        self,
        response_text: str,
        context: AgentRuntimeContext,
        scan_item: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        decision = TradeDecisionExtractor.extract(response_text)
        if decision is not None:
            decision = self._normalize_decision_for_mode(decision)
        broker_ctx = BrokerContext()
        account_panel = self._find_account_panel()
        if account_panel:
            broker_ctx = account_panel.get_broker_context()

        target_symbol_code = ""
        target_symbol_name = ""
        if scan_item:
            target_symbol_code = str((scan_item or {}).get("code", "") or context.symbol.code or "").strip()
            target_symbol_name = str((scan_item or {}).get("name", "") or context.symbol.name or "").strip()
        elif context.symbol.is_available:
            target_symbol_code = str(context.symbol.code or "").strip()
            target_symbol_name = str(context.symbol.name or "").strip()

        if decision is not None:
            if not decision.symbol_code and context.symbol.is_available:
                decision.symbol_code = context.symbol.code
            if not decision.symbol_name and context.symbol.name:
                decision.symbol_name = context.symbol.name
            if scan_item and target_symbol_code:
                extracted_code = str(decision.symbol_code or "").strip()
                if self._normalize_symbol_code(extracted_code) != self._normalize_symbol_code(target_symbol_code):
                    logger.warning(
                        "巡检结果标的代码与当前任务不一致，已强制校正: target=%s(%s) extracted=%s(%s)",
                        target_symbol_name or target_symbol_code,
                        target_symbol_code,
                        decision.symbol_name or extracted_code,
                        extracted_code,
                    )
                decision.symbol_code = target_symbol_code
                if target_symbol_name:
                    decision.symbol_name = target_symbol_name
            if decision.current_price <= 0 and context.symbol.latest_close > 0:
                decision.current_price = context.symbol.latest_close
            risk_result = self.risk_guard.evaluate(decision, broker_ctx)
        else:
            risk_result = None

        symbol_code = (
            decision.symbol_code if decision else target_symbol_code or context.symbol.code or (scan_item or {}).get("code", "")
        )
        symbol_name = (
            decision.symbol_name if decision else target_symbol_name or context.symbol.name or (scan_item or {}).get("name", "")
        )
        return {
            "response_text": response_text,
            "context": context,
            "decision": decision,
            "risk_result": risk_result,
            "scan_item": scan_item,
            "symbol_code": symbol_code,
            "symbol_name": symbol_name,
        }

    def _normalize_decision_for_mode(self, decision: TradeDecision) -> TradeDecision:
        action = str(getattr(decision, "action", "") or "").lower().strip()
        if self._current_mode == DECISION_MODE_CANDIDATE_POOL_SCAN:
            if action == TradeAction.HOLD.value:
                decision.action = TradeAction.WATCH.value
        elif action in (TradeAction.WATCH.value, TradeAction.REJECT.value):
            decision.action = TradeAction.HOLD.value
        return decision

    def _display_result(self, result: Dict[str, Any], *, switch_to_details: bool, emit_decision: bool):
        decision = result["decision"]
        self._render_response_text(result["response_text"], decision)
        risk_result = result["risk_result"]
        self._current_decision = decision
        self._current_risk_result = risk_result
        self._populate_decision_card(decision, risk_result)
        self._apply_action_state(decision, risk_result)
        if emit_decision and decision is not None:
            self.decision_ready.emit({
                "decision": decision,
                "risk_result": risk_result,
                "approved": False,
                "decision_record_id": "",
            })
        if switch_to_details:
            self.result_tabs.setCurrentWidget(self.decision_card_widget)

    _ACTION_COLORS = {
        "buy": "#4caf50", "add": "#4caf50",
        "sell": "#f44336", "reduce": "#ff9800",
        "hold": "#90caf9",
        "watch": "#90caf9",
        "reject": "#9e9e9e",
    }

    @classmethod
    def _decision_summary_html(cls, decision: Optional[TradeDecision]) -> str:
        if decision is None:
            return "<p style='color:#999;font-style:italic;'>未能提取到有效的结构化决策。</p>"

        action_color = cls._ACTION_COLORS.get(decision.action, "#90caf9")
        ret_pct = decision.expected_return_pct
        loss_pct = decision.max_loss_pct

        def _row(label: str, value: str, *, value_color: str = "") -> str:
            vc = f" style='color:{value_color};font-weight:bold;'" if value_color else ""
            return (
                f"<tr>"
                f"<td style='padding:4px 10px;color:#aaa;white-space:nowrap;'>{label}</td>"
                f"<td style='padding:4px 10px;'{vc}>{value}</td>"
                f"</tr>"
            )

        target_extra = f"<span style='color:#4caf50;font-size:0.9em;'>（预期 {ret_pct:+.2f}%）</span>" if ret_pct is not None else ""
        stop_extra = f"<span style='color:#f44336;font-size:0.9em;'>（最大亏损 {loss_pct:+.2f}%）</span>" if loss_pct is not None else ""

        rows = "".join([
            _row("操作建议", f"<span style='color:{action_color};font-size:1.1em;font-weight:bold;'>{decision.action_label}</span>"),
            _row("标的", f"{decision.symbol_name}（{decision.symbol_code}）"),
            _row("置信度", f"{decision.confidence:.0%}", value_color=action_color),
            _row("当前价", f"{decision.current_price:.2f}"),
            _row("目标价", f"{decision.target_price:.2f} {target_extra}"),
            _row("止损价", f"{decision.stop_loss_price:.2f} {stop_extra}"),
            _row("仓位建议", f"{decision.position_pct:.0%}"),
            _row("风险评分", f"{decision.risk_score:.2f}"),
            _row("时间维度", decision.horizon_label),
        ])
        if decision.reasoning:
            rows += _row("核心逻辑", decision.reasoning)

        return (
            f"<table cellspacing='0' style='border:1px solid #333;border-radius:4px;"
            f"margin:6px 0;width:100%;'>"
            f"<tbody>{rows}</tbody></table>"
        )

    _ANALYSIS_CSS = (
        "body{color:#d0d0d0;font-family:sans-serif;font-size:14px;}"
        "h1,h2,h3,h4{color:#e0e0e0;margin:12px 0 6px 0;}"
        "ul,ol{margin:4px 0 4px 18px;padding:0;}"
        "li{margin:2px 0;}"
        "table{border-collapse:collapse;}"
        "td,th{border-bottom:1px solid #2a2a2a;}"
    )

    def _render_response_text(self, response_text: str, decision: Optional[TradeDecision] = None):
        import re
        decision_html = self._decision_summary_html(decision)
        cleaned = re.sub(
            r"<trade_decision>\s*.*?\s*</trade_decision>",
            "{{DECISION_TABLE}}",
            response_text,
            flags=re.DOTALL,
        ).strip()
        try:
            import markdown as md_lib
            body_html = md_lib.markdown(cleaned, extensions=["fenced_code", "tables"])
        except Exception:
            body_html = f"<pre>{cleaned}</pre>"
        body_html = body_html.replace("{{DECISION_TABLE}}", decision_html)
        full_html = f"<html><head><style>{self._ANALYSIS_CSS}</style></head><body>{body_html}</body></html>"
        self.analysis_display.setHtml(full_html)

    def _apply_action_state(self, decision: Optional[TradeDecision], risk_result):
        if decision is None:
            self.approve_btn.setEnabled(False)
            self.reject_btn.setEnabled(False)
            self.decision_status_label.setText("⚠ 未能提取有效决策")
            self.decision_status_label.setStyleSheet("color: orange; font-weight: bold;")
            return
        if self._scan_in_progress:
            self.approve_btn.setEnabled(False)
            self.reject_btn.setEnabled(False)
            self.decision_status_label.setText(
                f"🔄 巡检进行中: {TRADE_ACTION_LABELS.get(decision.action, decision.action)}"
            )
            self.decision_status_label.setStyleSheet("color: #0078d4; font-weight: bold;")
            return
        if decision.is_actionable and risk_result and risk_result.passed:
            self.approve_btn.setEnabled(True)
            self.reject_btn.setEnabled(True)
            self.decision_status_label.setText("✅ 风控通过，可执行")
            self.decision_status_label.setStyleSheet("color: green; font-weight: bold;")
        elif decision.is_actionable:
            self.approve_btn.setEnabled(False)
            self.reject_btn.setEnabled(True)
            self.decision_status_label.setText("⛔ 风控未通过")
            self.decision_status_label.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.approve_btn.setEnabled(False)
            self.reject_btn.setEnabled(False)
            self.decision_status_label.setText(f"ℹ 建议: {TRADE_ACTION_LABELS.get(decision.action, decision.action)}")
            self.decision_status_label.setStyleSheet("color: #666; font-weight: bold;")

    def _populate_decision_card(self, decision: Optional[TradeDecision], risk_result):
        while self.decision_card_layout.count():
            child = self.decision_card_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if decision is None:
            lbl = QLabel("未提取到结构化决策。请查看 AI 分析报告内容。")
            lbl.setStyleSheet("color: #888; padding: 20px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.decision_card_layout.addWidget(lbl)
            return

        action_label = TRADE_ACTION_LABELS.get(decision.action, decision.action)

        # Title
        title = QLabel(f"📊 {action_label}  {decision.symbol_name}({decision.symbol_code})")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px 0;")
        self.decision_card_layout.addWidget(title)

        # Details form
        details = QGroupBox("决策参数")
        form = QFormLayout(details)
        form.setSpacing(4)
        form.addRow("操作:", QLabel(action_label))
        form.addRow("当前价:", QLabel(f"{decision.current_price:.2f}" if decision.current_price > 0 else "-"))
        form.addRow("目标价:", QLabel(f"{decision.target_price:.2f}" if decision.target_price > 0 else "-"))
        form.addRow("止损价:", QLabel(f"{decision.stop_loss_price:.2f}" if decision.stop_loss_price > 0 else "-"))

        ret = decision.expected_return_pct
        if ret is not None:
            color = "green" if ret > 0 else "red"
            form.addRow("预期收益:", QLabel(f"<span style='color:{color}'>{ret:+.2f}%</span>"))
        loss = decision.max_loss_pct
        if loss is not None:
            form.addRow("最大亏损:", QLabel(f"<span style='color:red'>{loss:.2f}%</span>"))

        form.addRow("置信度:", QLabel(f"{decision.confidence:.0%}"))
        form.addRow("建议仓位:", QLabel(f"{decision.position_pct:.0%}"))
        form.addRow("风险评分:", QLabel(f"{decision.risk_score:.2f}"))
        form.addRow("持有周期:", QLabel(decision.horizon_label))

        if decision.reasoning:
            r_lbl = QLabel(decision.reasoning)
            r_lbl.setWordWrap(True)
            form.addRow("理由:", r_lbl)
        if decision.bull_case:
            b_lbl = QLabel(decision.bull_case)
            b_lbl.setWordWrap(True)
            form.addRow("看多:", b_lbl)
        if decision.bear_case:
            br_lbl = QLabel(decision.bear_case)
            br_lbl.setWordWrap(True)
            form.addRow("看空:", br_lbl)
        if decision.invalidation:
            inv_lbl = QLabel(decision.invalidation)
            inv_lbl.setWordWrap(True)
            form.addRow("失效条件:", inv_lbl)

        self.decision_card_layout.addWidget(details)

        # Risk checks
        if risk_result:
            risk_group = QGroupBox("风控审核")
            risk_layout = QVBoxLayout(risk_group)
            icon = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(
                risk_result.overall_risk_level, "⚪"
            )
            risk_layout.addWidget(QLabel(f"{icon} 综合风险: {risk_result.overall_risk_level.upper()}"))
            for check in risk_result.checks:
                c_icon = "✅" if check.passed else ("⛔" if check.level == "block" else "⚠️")
                risk_layout.addWidget(QLabel(f"  {c_icon} {check.name}: {check.message}"))
            if risk_result.blocked_reasons:
                blk = QLabel("⛔ " + "; ".join(risk_result.blocked_reasons))
                blk.setStyleSheet("color: red; font-weight: bold;")
                blk.setWordWrap(True)
                risk_layout.addWidget(blk)
            self.decision_card_layout.addWidget(risk_group)

        self.decision_card_layout.addStretch()

    def _append_scan_result(self, result: Dict[str, Any]):
        row = self.scan_table.rowCount()
        self.scan_table.insertRow(row)
        self._scan_results.append(result)

        decision = result["decision"]
        risk_result = result["risk_result"]
        scan_item = result["scan_item"] or {}
        cost_price = float(scan_item.get("cost_price", 0) or 0)
        action_label = "解析失败" if decision is None else TRADE_ACTION_LABELS.get(decision.action, decision.action)
        confidence_text = "-" if decision is None else f"{decision.confidence:.0%}"
        current_price_text = "-" if decision is None or decision.current_price <= 0 else f"{decision.current_price:.2f}"
        cost_text = "-" if cost_price <= 0 else f"{cost_price:.2f}"
        risk_text = "-" if risk_result is None else risk_result.overall_risk_level.upper()
        status_text = "待查看"
        if decision is None:
            status_text = "解析失败"
        elif risk_result and not risk_result.passed and decision.is_actionable:
            status_text = "风控拦截"
        elif decision.is_actionable:
            status_text = "可执行"
        elif decision.action == TradeAction.WATCH.value:
            status_text = "候选观察"
        elif decision.action == TradeAction.REJECT.value:
            status_text = "剔除候选"
        else:
            status_text = "继续持有"

        values = [
            str(row + 1),
            result["symbol_code"],
            result["symbol_name"],
            action_label,
            confidence_text,
            current_price_text,
            cost_text,
            risk_text,
            status_text,
        ]
        for col, value in enumerate(values):
            self.scan_table.setItem(row, col, QTableWidgetItem(value))

    def _on_scan_selection_changed(self):
        row = self.scan_table.currentRow()
        if row < 0 or row >= len(self._scan_results):
            return
        result = self._scan_results[row]
        self._display_result(result, switch_to_details=False, emit_decision=True)
        self.result_tabs.setCurrentWidget(self.decision_card_widget)

    def _on_approve(self):
        if not self._current_decision or not self._current_risk_result:
            return
        self.approve_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)

        record = self.decision_tracker.save_decision(
            self._current_decision,
            self._current_risk_result,
            DecisionOutcome.APPROVED.value,
        )
        self._current_approved_record_id = record.record_id
        self.decision_status_label.setText("✅ 已批准 — 请在右侧下单面板确认执行")
        self.decision_status_label.setStyleSheet("color: green; font-weight: bold;")

        self.decision_ready.emit({
            "decision": self._current_decision,
            "risk_result": self._current_risk_result,
            "approved": True,
            "decision_record_id": record.record_id,
        })
        self._refresh_history()

    def _on_reject(self):
        if not self._current_decision or not self._current_risk_result:
            return
        self.approve_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)

        self.decision_tracker.save_decision(
            self._current_decision,
            self._current_risk_result,
            DecisionOutcome.REJECTED_BY_USER.value,
        )
        self.decision_status_label.setText("❌ 已驳回")
        self.decision_status_label.setStyleSheet("color: #888; font-weight: bold;")
        self._refresh_history()

    def _refresh_history(self):
        records = self.decision_tracker.query_recent(limit=50)
        self._history_records = records
        self.history_table.setRowCount(len(records))
        for row, rec in enumerate(records):
            d = rec.decision or {}
            risk = rec.risk_result or {}
            self.history_table.setItem(row, 0, QTableWidgetItem(rec.created_at))
            self.history_table.setItem(row, 1, QTableWidgetItem(f"{rec.symbol_name}({rec.symbol_code})"))
            self.history_table.setItem(row, 2, QTableWidgetItem(
                TRADE_ACTION_LABELS.get(d.get("action", ""), d.get("action", ""))
            ))
            self.history_table.setItem(row, 3, QTableWidgetItem(f"{d.get('confidence', 0):.0%}"))
            self.history_table.setItem(row, 4, QTableWidgetItem(
                f"{rec.entry_price:.2f}" if rec.entry_price > 0 else "-"
            ))
            pnl_item = QTableWidgetItem(f"{rec.actual_pnl_pct:+.2f}%" if rec.closed_at else "-")
            if rec.closed_at:
                pnl_item.setForeground(QBrush(QColor("#4caf50") if rec.actual_pnl_pct >= 0 else QColor("#f44336")))
            self.history_table.setItem(row, 5, pnl_item)
            pnl_amt = QTableWidgetItem(f"¥{rec.actual_pnl:+,.2f}" if rec.closed_at else "-")
            if rec.closed_at:
                pnl_amt.setForeground(QBrush(QColor("#4caf50") if rec.actual_pnl >= 0 else QColor("#f44336")))
            self.history_table.setItem(row, 6, pnl_amt)
            self.history_table.setItem(row, 7, QTableWidgetItem(
                risk.get("overall_risk_level", "-").upper()
            ))
            self.history_table.setItem(row, 8, QTableWidgetItem(rec.outcome))
        self._refresh_stats_bar()

    def _refresh_stats_bar(self):
        try:
            stats = self.decision_tracker.get_stats()
        except Exception:
            self.stats_bar.setText("统计数据加载失败")
            return
        total = stats.get("total_decisions", 0)
        executed = stats.get("executed_count", 0)
        closed = stats.get("closed_count", 0)
        win_rate = stats.get("win_rate", 0)
        avg_pnl = stats.get("avg_pnl_pct", 0)
        total_pnl = stats.get("total_pnl", 0)

        wr_color = "#4caf50" if win_rate >= 0.5 else "#f44336" if win_rate > 0 else "#888"
        pnl_color = "#4caf50" if total_pnl > 0 else "#f44336" if total_pnl < 0 else "#888"
        self.stats_bar.setText(
            f"📊 决策总数: <b>{total}</b> | "
            f"已执行: <b>{executed}</b> | "
            f"已平仓: <b>{closed}</b> | "
            f"胜率: <b style='color:{wr_color}'>{win_rate:.1%}</b> | "
            f"平均盈亏: <b>{avg_pnl:+.2f}%</b> | "
            f"累计盈亏: <b style='color:{pnl_color}'>¥{total_pnl:,.2f}</b>"
        )

    def _export_csv(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "导出决策CSV", f"decisions_{datetime.now().strftime('%Y%m%d')}.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        from pathlib import Path as _P
        count = self.decision_tracker.export_csv(_P(path))
        QMessageBox.information(self, "导出完成", f"已导出 {count} 条决策记录到\n{path}")

    def _export_html(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "导出复盘报告", f"decision_report_{datetime.now().strftime('%Y%m%d')}.html",
            "HTML files (*.html)",
        )
        if not path:
            return
        from pathlib import Path as _P
        count = self.decision_tracker.export_html_report(_P(path))
        QMessageBox.information(self, "导出完成", f"已导出 {count} 条决策复盘报告到\n{path}")
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_history_context_menu(self, pos):
        row = self.history_table.rowAt(pos.y())
        records = getattr(self, "_history_records", [])
        if row < 0 or row >= len(records):
            return
        rec = records[row]
        if rec.outcome != DecisionOutcome.EXECUTED.value or rec.closed_at:
            return
        action = (rec.decision or {}).get("action", "")
        if action not in ("buy", "add"):
            return

        from PyQt6.QtWidgets import QMenu, QInputDialog
        menu = QMenu(self)
        close_action = menu.addAction("📊 手动平仓（输入卖出价）")
        chosen = menu.exec(self.history_table.viewport().mapToGlobal(pos))
        if chosen != close_action:
            return

        price_str, ok = QInputDialog.getText(
            self, "手动平仓",
            f"请输入 {rec.symbol_name}({rec.symbol_code}) 的卖出价:",
        )
        if not ok or not price_str.strip():
            return
        try:
            exit_price = float(price_str.strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "请输入有效的价格数字")
            return
        if exit_price <= 0:
            return

        success = self.decision_tracker.close_position(rec.record_id, exit_price)
        if success:
            self._refresh_history()
            QMessageBox.information(
                self, "平仓完成",
                f"{rec.symbol_name} 已平仓，入场价 {rec.entry_price:.2f}，"
                f"出场价 {exit_price:.2f}"
            )
        else:
            QMessageBox.warning(self, "提示", "平仓失败，请检查记录")

    def _try_scan_notification(self):
        try:
            from services.ai_decision_notifier import notify_scan_complete
        except ImportError:
            try:
                from trading_app.services.ai_decision_notifier import notify_scan_complete
            except ImportError:
                return
        if self._current_mode == DECISION_MODE_CANDIDATE_POOL_SCAN:
            scan_type = "candidate_pool_scan"
        else:
            scan_type = "position_scan"
        try:
            notify_scan_complete(scan_type, self._scan_results, group_name="")
        except Exception as exc:
            logger.debug("Scan notification failed: %s", exc)

    def _find_account_panel(self) -> Optional[AccountPanel]:
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "account_panel"):
                return parent.account_panel
            parent = parent.parent() if hasattr(parent, "parent") and callable(parent.parent) else None
        return None

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_history()


# ───────────────────────────────────────────────────────────────────────────
#  Scheduler Settings Dialog
# ───────────────────────────────────────────────────────────────────────────
class SchedulerSettingsDialog(BaseSchedulerSettingsDialog):
    """Configure scheduled AI decision tasks."""

    def __init__(self, scheduler, parent=None):
        super().__init__(title="定时任务设置", min_width=560, initial_height=500, parent=parent)
        self.scheduler = scheduler
        self._setup_ui()

    def _setup_ui(self):
        self.content_layout.addWidget(
            self.make_note_label(
                "说明：修改后点击底部“保存并关闭”生效。AI 任务会在设定时间触发巡检，并按各自的自动执行开关决定是否继续下单。"
            )
        )

        tasks = self.scheduler.get_tasks()
        self._rows: Dict[str, Dict[str, Any]] = {}

        for tid, task in tasks.items():
            grp = QGroupBox(f"调度任务：{task.name}")
            grp_layout = QFormLayout(grp)
            grp_layout.setSpacing(6)
            runtime_display = self.scheduler.get_task_runtime_display(tid)

            enabled_cb = QCheckBox("启用任务")
            enabled_cb.setChecked(task.enabled)
            grp_layout.addRow("", enabled_cb)

            time_edit = QTimeEdit()
            try:
                h, m = map(int, task.time.split(":"))
                time_edit.setTime(QTime(h, m))
            except Exception:
                time_edit.setTime(QTime(9, 0))
            grp_layout.addRow("执行时间:", time_edit)

            notify_cb = QCheckBox("完成后发送通知")
            notify_cb.setChecked(task.notify_on_complete)
            grp_layout.addRow("", notify_cb)

            auto_execute_cb = QCheckBox("完成后自动执行交易")
            auto_execute_cb.setChecked(bool(getattr(task, "auto_execute", False)))
            grp_layout.addRow("", auto_execute_cb)

            last_run = QLabel(runtime_display.get("last_run", "") or task.last_run or "从未执行")
            last_run.setStyleSheet("color:#888;")
            grp_layout.addRow("最近执行:", last_run)

            last_result = QLabel(runtime_display.get("last_result", "") or task.last_result or "-")
            last_result.setWordWrap(True)
            last_result.setStyleSheet("color:#888;")
            grp_layout.addRow("最近结果:", last_result)

            run_now_btn = self.make_action_button("立即执行一次")
            run_now_btn.setFixedWidth(96)
            run_now_btn.clicked.connect(lambda _, t=tid: self._run_now(t))
            grp_layout.addRow("", run_now_btn)

            self.content_layout.addWidget(grp)
            self._rows[tid] = {
                "enabled": enabled_cb,
                "time": time_edit,
                "notify": notify_cb,
                "auto_execute": auto_execute_cb,
            }

        self.btn_save, self.btn_cancel = self.setup_footer(
            primary_text="保存并关闭",
            primary_handler=self._save,
            secondary_text="取消",
            secondary_handler=self.reject,
        )

    def _save(self):
        try:
            from services.ai_decision_scheduler import ScheduledAITask
        except ImportError:
            from trading_app.services.ai_decision_scheduler import ScheduledAITask

        for tid, widgets in self._rows.items():
            old = self.scheduler.get_tasks().get(tid)
            if not old:
                continue
            task = ScheduledAITask(
                task_id=tid,
                name=old.name,
                enabled=widgets["enabled"].isChecked(),
                time=widgets["time"].time().toString("HH:mm"),
                task_type=old.task_type or "ai_strategy_cycle",
                watchlist_group="",
                model_name=old.model_name,
                notify_on_complete=widgets["notify"].isChecked(),
                auto_execute=widgets["auto_execute"].isChecked(),
                last_run=old.last_run,
                last_result=old.last_result,
            )
            self.scheduler.add_or_update_task(task)
        QMessageBox.information(self, "提示", "定时任务配置已保存。")
        self.accept()

    def _run_now(self, task_id: str):
        self.scheduler.run_now(task_id)
        QMessageBox.information(self, "提示", "任务已触发，请查看主面板")


class AIStrategyConfigDialog(BaseStrategyConfigDialog):
    """Standalone configuration dialog for the AI strategy."""

    def __init__(self, account_panel: AccountPanel, parent=None):
        super().__init__(title="AI 策略配置", min_width=760, initial_height=660, parent=parent)
        self.account_panel = account_panel
        self.content_layout.addWidget(
            self.make_note_label("说明：配置弹窗默认只读。点击底部“解锁编辑”后，可修改并使用各分组内的保存按钮提交。")
        )
        self.content_layout.addWidget(self.account_panel._config_container)
        self.btn_close, self.btn_unlock = self.setup_footer(
            close_text="关闭",
            close_handler=self.reject,
            unlock_text="🔓 解锁编辑",
            unlock_handler=self._unlock_for_edit,
        )

    def prepare_for_open(self) -> None:
        self.account_panel._config_container.setVisible(True)
        self.account_panel._lock_config_panels()
        self.btn_unlock.setText("🔓 解锁编辑")
        self.btn_unlock.setEnabled(True)

    def _unlock_for_edit(self) -> None:
        if self.account_panel.request_unlock_config():
            self.btn_unlock.setText("✏️ 编辑中…")
            self.btn_unlock.setEnabled(False)

    def reject(self) -> None:
        self.account_panel._lock_config_panels()
        self.btn_unlock.setText("🔓 解锁编辑")
        self.btn_unlock.setEnabled(True)
        super().reject()


# ───────────────────────────────────────────────────────────────────────────
#  Main Window: AI Trade Decision Center
# ───────────────────────────────────────────────────────────────────────────
class AITradeDecisionPanel(QWidget):
    """Embeddable AI live strategy panel."""

    def __init__(
        self,
        context_provider=None,
        parent=None,
        *,
        symbol_name_resolver: Optional[Callable[[str], str]] = None,
        name_map: Optional[Dict[str, str]] = None,
        etf_name_map: Optional[Dict[str, str]] = None,
        shared_broker_panel=None,
        manage_startup: bool = True,
    ):
        super().__init__(parent)
        self.context_provider = context_provider
        self.symbol_name_resolver = symbol_name_resolver
        self.name_map = dict(name_map or {})
        self.etf_name_map = dict(etf_name_map or {})
        self.shared_broker_panel = shared_broker_panel
        self.manage_startup = bool(manage_startup)
        self._status_proxy = _StatusMessageProxy(self)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # Left: Account panel
        self.account_panel = AccountPanel(
            show_connection_panel=self.shared_broker_panel is None,
            shared_broker_panel=self.shared_broker_panel,
        )
        self.account_panel.setMinimumWidth(260)
        self.account_panel.setMaximumWidth(360)
        self.account_panel.scheduler_settings_requested.connect(self._open_scheduler_settings)
        self.account_panel.manual_order_requested.connect(self._open_order_dialog)

        # Center: Decision panel
        self.decision_panel = DecisionPanel(context_provider=context_provider)
        self.decision_panel.setMinimumWidth(500)

        # Detached: Order execution dialog panel
        self.order_panel = OrderExecutionPanel()
        self.order_dialog = QDialog(self)
        self.order_dialog.setWindowTitle("手动委托")
        self.order_dialog.resize(560, 680)
        order_dialog_layout = QVBoxLayout(self.order_dialog)
        order_dialog_layout.setContentsMargins(8, 8, 8, 8)
        order_dialog_layout.addWidget(self.order_panel)

        bottom_controls = QWidget(self)
        bottom_bar = QHBoxLayout(bottom_controls)
        bottom_bar.setContentsMargins(4, 2, 4, 2)

        self.shell = LiveStrategyShell(
            self._build_strategy_context(),
            self.account_panel,
            self.decision_panel,
            footer_panel=bottom_controls,
            parent=self,
        )
        self.shell.horizontal_splitter.setSizes([320, 980])
        self.strategy_trade_panel = self.shell.strategy_trade_panel
        main_layout.addWidget(self.shell)

        # ── Scheduler / Freshness ──
        try:
            from services.ai_decision_scheduler import AIDecisionScheduler
            from services.data_freshness_service import DataFreshnessGuard
            from services.qmt_startup_orchestrator import QmtStartupOrchestrator
        except ImportError:
            from trading_app.services.ai_decision_scheduler import AIDecisionScheduler
            from trading_app.services.data_freshness_service import DataFreshnessGuard
            from trading_app.services.qmt_startup_orchestrator import QmtStartupOrchestrator

        self.scheduler = AIDecisionScheduler(self)
        self.scheduler.ensure_defaults()
        self.scheduler.task_triggered.connect(self._on_scheduled_task)
        self.scheduler.task_log.connect(lambda msg: self.statusBar().showMessage(msg))

        self.freshness_guard = DataFreshnessGuard(self)
        self.freshness_guard.update_needed.connect(
            lambda cnt, msg: self.statusBar().showMessage(f"📡 {msg}，正在更新...")
        )
        self.freshness_guard.update_progress.connect(
            lambda c, t, m: self.statusBar().showMessage(f"📡 数据更新 {c}/{t}: {m}")
        )
        self.freshness_guard.update_finished.connect(
            lambda ok, msg: self.statusBar().showMessage(f"{'✅' if ok else '❌'} {msg}")
        )
        self.freshness_guard.update_finished.connect(self._on_freshness_finished)
        self.freshness_guard.xtquant_failed.connect(self._on_xtquant_failed)
        self.startup_orchestrator = None
        if self.manage_startup:
            self.startup_orchestrator = QmtStartupOrchestrator(self.account_panel.broker, self)
            self.startup_orchestrator.status_changed.connect(self._on_startup_status)
            self.startup_orchestrator.finished.connect(self._on_startup_finished)
        self.daily_auto_trade = get_daily_auto_trade_service()
        self.daily_auto_trade.status_changed.connect(self.statusBar().showMessage)
        self.daily_auto_trade.cycle_finished.connect(self._on_daily_auto_trade_finished)
        self.daily_auto_trade.reconcile_finished.connect(self._on_daily_reconcile_finished)
        self._pending_scheduled_auto_task: Optional[dict] = None
        self._reconcile_catchup_worker: Optional[_ReconcileCatchupWorker] = None
        self._paused_scheduler_task_ids: list[str] = []

        self._broker_svc = get_broker_session_service()
        self._broker_svc.trade_occurred.connect(self._on_broker_trade_callback)
        self._broker_svc.order_changed.connect(self._on_broker_order_callback)

        bottom_bar.addStretch()

        # Status bar
        self.statusBar().showMessage("就绪")

        # Wiring
        self.decision_panel.decision_ready.connect(self._on_decision_ready)
        self.decision_panel.scan_completed.connect(self._on_scan_completed)
        self.strategy_trade_panel.order_requested.connect(self._open_order_dialog_with_order)
        self.order_panel.order_executed.connect(self._on_order_executed)
        self._refresh_scheduler_status()

        expired = self.decision_panel.decision_tracker.expire_stale_decisions()
        if expired > 0:
            self.statusBar().showMessage(f"已自动标记 {expired} 条过期决策")
            self.decision_panel._refresh_history()

        if self.manage_startup:
            QTimer.singleShot(600, self._start_startup_orchestration)

    def _build_strategy_context(self) -> StrategyPanelContext:
        return StrategyPanelContext(
            strategy_id=AI_STOCK_STRATEGY_ID,
            strategy_name=AI_STOCK_STRATEGY_NAME,
            virtual_account_id=AI_STOCK_VIRTUAL_ACCOUNT_ID,
            owner_type="ai",
        )

    def statusBar(self):
        window = self.window()
        if isinstance(window, QMainWindow):
            return window.statusBar()
        return self._status_proxy

    def _on_decision_ready(self, payload: object):
        if isinstance(payload, dict):
            decision = payload.get("decision")
            risk_result = payload.get("risk_result")
            approved = bool(payload.get("approved", False))
            decision_record_id = str(payload.get("decision_record_id", "") or "")
        else:
            decision = payload
            risk_result = None
            approved = False
            decision_record_id = ""
        if decision is None:
            return
        self.order_panel.fill_from_decision(
            decision,
            risk_result=risk_result,
            approved=approved,
            decision_record_id=decision_record_id,
        )
        if approved:
            self._open_order_dialog()
        self.statusBar().showMessage(
            f"决策: {TRADE_ACTION_LABELS.get(decision.action, decision.action)} "
            f"{decision.symbol_name} | 置信度 {decision.confidence:.0%}"
        )

    def _open_order_dialog(self):
        self.order_dialog.show()
        self.order_dialog.raise_()
        self.order_dialog.activateWindow()

    def _open_order_dialog_with_order(self, code: str, direction: str, price: float):
        self.order_panel.fill_order(code, direction, price)
        self._open_order_dialog()

    def _on_order_executed(
        self,
        success: bool,
        filled_confirmed: bool,
        message: str,
        order_id: int = -1,
        price: float = 0.0,
    ):
        self.strategy_trade_panel.refresh_all()
        if success:
            prefix = "✅" if filled_confirmed else "⏳"
            self.statusBar().showMessage(f"{prefix} {message}")
            QTimer.singleShot(2000, self.account_panel.refresh)
            record_id = getattr(self.decision_panel, "_current_approved_record_id", "")
            tracker = self.decision_panel.decision_tracker
            decision = self.decision_panel._current_decision

            if record_id and order_id > 0:
                tracker.update_outcome(record_id, broker_order_id=order_id)

            if record_id and filled_confirmed:
                tracker.update_outcome(
                    record_id,
                    outcome=DecisionOutcome.EXECUTED.value,
                    broker_order_id=order_id,
                )
                self.decision_panel._current_approved_record_id = ""

                if decision and decision.action in ("sell", "reduce"):
                    closed_ids = tracker.auto_close_by_symbol(
                        decision.symbol_code,
                        price or decision.current_price,
                        broker_order_id=order_id,
                    )
                    if closed_ids:
                        self.statusBar().showMessage(
                            f"✅ {message} | 已自动平仓 {len(closed_ids)} 条买入记录"
                        )

                self.decision_panel._refresh_history()
        else:
            self.statusBar().showMessage(f"❌ {message}")
        QMessageBox.information(self, "下单结果", message)

    # ── Broker trade/order callback integration ──

    def _on_broker_trade_callback(self, trade_data: dict) -> None:
        code = trade_data.get("stock_code", "")
        price = trade_data.get("traded_price", "")
        vol = trade_data.get("traded_volume", "")
        self.statusBar().showMessage(f"📬 成交回报: {code} 价格={price} 数量={vol}")
        QTimer.singleShot(1500, self.account_panel.refresh)

    def _on_broker_order_callback(self, order_data: dict) -> None:
        status = order_data.get("order_status")
        code = order_data.get("stock_code", "")
        _STATUS_LABELS = {48: "未报", 50: "已报", 51: "部撤", 52: "已报待撤", 54: "已撤", 55: "部成", 56: "已成", 57: "废单"}
        label = _STATUS_LABELS.get(status, str(status))
        self.statusBar().showMessage(f"📋 委托回报: {code} 状态={label}")

    # ── Scheduler integration ──

    def _refresh_scheduler_status(self):
        tasks = self.scheduler.get_tasks()
        enabled = [t for t in tasks.values() if t.enabled]
        if enabled:
            primary = enabled[0]
            mode_label = "自动执行" if bool(getattr(primary, "auto_execute", False)) else "仅检查"
            time_text = str(getattr(primary, "time", "") or "").strip()
            summary = f"定时任务: {time_text} {mode_label}".strip()
            self.account_panel.set_scheduler_status(summary, "#16A34A")
        else:
            self.account_panel.set_scheduler_status("定时任务: 未启用", "#6B7B8D")

    def _open_scheduler_settings(self):
        dlg = SchedulerSettingsDialog(self.scheduler, parent=self)
        dlg.exec()
        self._refresh_scheduler_status()

    def _on_scheduled_task(self, task_id: str, task_config: dict):
        task_type = task_config.get("task_type", "ai_strategy_cycle")
        scheduled_run_context = build_decision_run_context(prefer_realtime=True)
        logger.info("AI交易中心收到定时任务: %s (%s)", task_id, task_type)
        self.statusBar().showMessage(f"⏰ 定时任务触发: {task_config.get('name', task_id)}，正在检查数据新鲜度...")
        self.decision_panel._set_run_context_override(scheduled_run_context)
        self._pending_scheduled_auto_task = {
            "task_id": task_id,
            "task_config": dict(task_config or {}),
            "expected_scan_run_id": "",
            "expected_scan_mode": "",
            "run_context": scheduled_run_context.to_dict(),
        }
        started, begin_msg = self.daily_auto_trade.begin_task(task_id, task_config)
        if not started:
            logger.info("定时任务 %s 未启动: %s", task_id, begin_msg)
            self.statusBar().showMessage(f"⏰ {begin_msg}")
            self.scheduler.mark_task_result(task_id, begin_msg, dispatch_status="skipped")
            self._pending_scheduled_auto_task = None
            self.decision_panel._clear_run_context_override()
            return

        logger.info("定时任务 %s 已进入自动任务编排", task_id)
        self.scheduler.mark_task_dispatch(task_id, "accepted", "定时任务已进入自动任务编排")
        if task_type == "ai_strategy_cycle":
            self.decision_panel.mode_combo.setCurrentIndex(
                self.decision_panel.mode_combo.findData(DECISION_MODE_POSITION_SCAN)
            )
        elif task_type == "position_scan":
            self.decision_panel.mode_combo.setCurrentIndex(
                self.decision_panel.mode_combo.findData(DECISION_MODE_POSITION_SCAN)
            )
        elif task_type == "candidate_pool_scan":
            self.decision_panel.mode_combo.setCurrentIndex(
                self.decision_panel.mode_combo.findData(DECISION_MODE_CANDIDATE_POOL_SCAN)
            )

        model_name = task_config.get("model_name", "")
        if model_name:
            idx = self.decision_panel.model_combo.findText(model_name)
            if idx >= 0:
                self.decision_panel.model_combo.setCurrentIndex(idx)

        current_model = self.decision_panel.model_combo.currentText()
        logger.info("定时任务 %s 使用模型: %s", task_id, current_model)
        model_cfg = self.decision_panel._resolve_model_config(show_dialog=False)
        if not model_cfg:
            self._finish_pending_scheduled_task(task_id, False, f"未配置可用的 AI 模型: {current_model}")
            return

        cycle_plan = None
        if task_type == "ai_strategy_cycle":
            cycle_plan = self._build_ai_strategy_cycle_plan()
            self._pending_scheduled_auto_task["cycle_plan"] = cycle_plan
            self._pending_scheduled_auto_task["cycle_results"] = []
            self._pending_scheduled_auto_task["cycle_index"] = 0
            self._pending_scheduled_auto_task["model_cfg"] = dict(model_cfg)

        codes = self._collect_codes_for_task(task_type, task_config, cycle_plan=cycle_plan)
        if not codes:
            self._finish_pending_scheduled_task(task_id, False, "当前任务没有可分析标的")
            return
        logger.info("定时任务 %s 准备校验数据: %d 只标的", task_id, len(codes))
        self.freshness_guard.ensure_fresh_then_run(
            codes,
            lambda task_type=task_type, model_cfg=model_cfg, task_id=task_id: self._run_scheduled_analysis(task_id, task_type, model_cfg),
            include_indices=True,
            prefer_realtime=True,
        )

    def _on_scan_completed(self, payload: object):
        if not isinstance(payload, dict):
            return
        if not self._pending_scheduled_auto_task:
            return
        pending = dict(self._pending_scheduled_auto_task)
        task_id = str(pending.get("task_id", "") or "")
        task_config = dict(pending.get("task_config", {}) or {})
        task_type = str(task_config.get("task_type", "") or "")
        expected_run_id = str(pending.get("expected_scan_run_id", "") or "")
        expected_mode = str(pending.get("expected_scan_mode", "") or "")
        payload_run_id = str(payload.get("scan_run_id", "") or "")
        payload_mode = str(payload.get("mode", "") or "")
        if not expected_run_id:
            logger.warning(
                "定时任务 %s 忽略未登记轮次的扫描完成事件: actual_run=%s mode=%s",
                task_id,
                payload_run_id,
                payload_mode,
            )
            return
        if expected_run_id and payload_run_id != expected_run_id:
            logger.warning(
                "定时任务 %s 忽略非当前轮次的扫描完成事件: expected_run=%s actual_run=%s mode=%s",
                task_id,
                expected_run_id,
                payload_run_id,
                payload_mode,
            )
            return
        if expected_mode and payload_mode != expected_mode:
            logger.warning(
                "定时任务 %s 忽略非当前阶段的扫描完成事件: expected_mode=%s actual_mode=%s run=%s",
                task_id,
                expected_mode,
                payload_mode,
                payload_run_id,
            )
            return
        if task_type == "ai_strategy_cycle":
            all_results = list(self._pending_scheduled_auto_task.get("cycle_results", []) or [])
            all_results.extend(list(payload.get("results", []) or []))
            self._pending_scheduled_auto_task["cycle_results"] = all_results
            self._pending_scheduled_auto_task["cycle_index"] = int(self._pending_scheduled_auto_task.get("cycle_index", 0)) + 1
            plan = dict(self._pending_scheduled_auto_task.get("cycle_plan", {}) or {})
            phases = list(plan.get("phases", []) or [])
            next_index = int(self._pending_scheduled_auto_task.get("cycle_index", 0))
            if next_index < len(phases):
                logger.info("定时任务 %s 进入下一阶段: %s", task_id, phases[next_index].get("label", ""))
                if self._start_next_ai_strategy_cycle_phase(task_id):
                    return
                return
            self._pending_scheduled_auto_task = None
            self.decision_panel._clear_run_context_override()
            logger.info("定时任务 %s 的总巡检已完成，准备进入自动执行编排", task_id)
            broker_context = self.account_panel.get_broker_context()
            self.daily_auto_trade.handle_scan_results(
                task_id,
                task_config,
                all_results,
                broker_context,
            )
            return

        self._pending_scheduled_auto_task = None
        self.decision_panel._clear_run_context_override()
        logger.info("定时任务 %s 的巡检已完成，准备进入自动执行编排", task_id)
        broker_context = self.account_panel.get_broker_context()
        self.daily_auto_trade.handle_scan_results(
            task_id,
            task_config,
            list(payload.get("results", []) or []),
            broker_context,
        )

    def _on_daily_auto_trade_finished(self, task_id: str, success: bool, message: str, summary: dict):
        logger.info("定时任务 %s 自动交易结束: success=%s message=%s", task_id, success, message)
        self.statusBar().showMessage(f"{'✅' if success else '❌'} {message}")
        planned = int(len(summary.get("planned", []) or []))
        executed = int(len(summary.get("executed", []) or []))
        if summary.get("skipped"):
            result_text = f"{message}（跳过）"
        else:
            result_text = f"{message}（计划 {planned} / 执行 {executed}）"
        if task_id:
            self.scheduler.mark_task_result(task_id, result_text, dispatch_status="completed" if success else "failed")
        QTimer.singleShot(200, self.account_panel.refresh)

    def _on_daily_reconcile_finished(self, success: bool, message: str):
        self.statusBar().showMessage(f"{'✅' if success else '❌'} {message}")
        QTimer.singleShot(200, self.account_panel.refresh)
        QTimer.singleShot(300, self.strategy_trade_panel.refresh_all)

    def _finish_pending_scheduled_task(self, task_id: str, success: bool, message: str):
        logger.info("定时任务 %s 结束: %s", task_id, message)
        self.daily_auto_trade.finish_task(task_id, success, message)
        self.scheduler.mark_task_result(task_id, message, dispatch_status="completed" if success else "failed")
        self.statusBar().showMessage(f"{'✅' if success else '❌'} {message}")
        self._pending_scheduled_auto_task = None
        self.decision_panel._clear_run_context_override()

    def _run_scheduled_analysis(self, task_id: str, task_type: str, model_cfg: dict):
        logger.info("定时任务 %s 通过数据校验，开始执行巡检", task_id)
        try:
            pending = dict(self._pending_scheduled_auto_task or {})
            self.decision_panel._set_run_context_override(pending.get("run_context"))
            if task_type == "ai_strategy_cycle":
                if self._pending_scheduled_auto_task is not None:
                    self._pending_scheduled_auto_task["model_cfg"] = dict(model_cfg)
                if self._start_next_ai_strategy_cycle_phase(task_id):
                    return
                self._finish_pending_scheduled_task(task_id, False, "每日AI策略总任务没有可执行的巡检阶段")
                return
            if task_type == "position_scan":
                run_id = self.decision_panel._start_position_scan(model_cfg, scan_source="scheduled", scheduled_task_id=task_id)
                if not run_id:
                    self._finish_pending_scheduled_task(task_id, False, "持仓巡检未能启动，可能仍有其他扫描在运行")
                    return
                if self._pending_scheduled_auto_task is not None:
                    self._pending_scheduled_auto_task["expected_scan_run_id"] = run_id
                    self._pending_scheduled_auto_task["expected_scan_mode"] = DECISION_MODE_POSITION_SCAN
                return
            if task_type == "candidate_pool_scan":
                items = self.decision_panel._load_candidate_pool_items(refresh=True)
                run_id = self.decision_panel._start_candidate_pool_scan(
                    model_cfg,
                    items,
                    scan_source="scheduled",
                    scheduled_task_id=task_id,
                )
                if not run_id:
                    self._finish_pending_scheduled_task(task_id, False, "候选池巡检未能启动，可能仍有其他扫描在运行")
                    return
                if self._pending_scheduled_auto_task is not None:
                    self._pending_scheduled_auto_task["expected_scan_run_id"] = run_id
                    self._pending_scheduled_auto_task["expected_scan_mode"] = DECISION_MODE_CANDIDATE_POOL_SCAN
                return
            self._finish_pending_scheduled_task(task_id, False, f"不支持的任务类型: {task_type}")
        except Exception as exc:
            logger.exception("定时任务 %s 启动巡检失败", task_id)
            self._finish_pending_scheduled_task(task_id, False, f"启动巡检失败: {exc}")

    def _build_ai_strategy_cycle_plan(self) -> dict:
        phases: list[dict] = []
        position_codes: list[str] = []
        held_codes: set[str] = set()
        try:
            positions = self.account_panel.get_live_positions()
            for position in positions:
                code = str(position.get("code", "") or "").strip()
                if not code:
                    continue
                position_codes.append(code)
                held_codes.add(code[-6:])
            if position_codes:
                phases.append({
                    "type": "position_scan",
                    "label": "持仓巡检",
                    "count": len(position_codes),
                })
        except Exception:
            positions = []

        candidate_items: list[dict] = []
        candidate_codes: list[str] = []
        try:
            raw_items = self.decision_panel._load_candidate_pool_items(refresh=True)
            for item in raw_items:
                code = str(item.get("symbol_code", "") or item.get("code", "") or "").strip()
                if not code or code[-6:] in held_codes:
                    continue
                candidate_items.append(item)
                candidate_codes.append(code)
            if candidate_items:
                phases.append({
                    "type": "candidate_pool_scan",
                    "label": "候选池巡检",
                    "count": len(candidate_items),
                    "items": candidate_items,
                })
        except Exception:
            pass

        return {
            "phases": phases,
            "codes": list(dict.fromkeys(position_codes + candidate_codes)),
        }

    def _start_next_ai_strategy_cycle_phase(self, task_id: str) -> bool:
        pending = self._pending_scheduled_auto_task
        if not pending:
            return False
        model_cfg = dict(pending.get("model_cfg", {}) or {})
        plan = dict(pending.get("cycle_plan", {}) or {})
        phases = list(plan.get("phases", []) or [])
        phase_index = int(pending.get("cycle_index", 0))
        if phase_index >= len(phases):
            return False
        phase = dict(phases[phase_index] or {})
        phase_type = str(phase.get("type", "") or "")
        phase_label = str(phase.get("label", phase_type) or phase_type)
        phase_count = int(phase.get("count", 0) or 0)
        self.statusBar().showMessage(f"⏰ 定时任务触发: {phase_label}，共 {phase_count} 只")
        logger.info("定时任务 %s 开始阶段 %s (%d 只)", task_id, phase_label, phase_count)
        if phase_type == "position_scan":
            self.decision_panel.mode_combo.setCurrentIndex(
                self.decision_panel.mode_combo.findData(DECISION_MODE_POSITION_SCAN)
            )
            run_id = self.decision_panel._start_position_scan(
                model_cfg,
                scan_source="scheduled",
                scheduled_task_id=task_id,
            )
            if not run_id:
                self._finish_pending_scheduled_task(task_id, False, f"{phase_label}未能启动，可能仍有其他扫描在运行")
                return False
            self._pending_scheduled_auto_task["expected_scan_run_id"] = run_id
            self._pending_scheduled_auto_task["expected_scan_mode"] = DECISION_MODE_POSITION_SCAN
            return True
        if phase_type == "candidate_pool_scan":
            self.decision_panel.mode_combo.setCurrentIndex(
                self.decision_panel.mode_combo.findData(DECISION_MODE_CANDIDATE_POOL_SCAN)
            )
            run_id = self.decision_panel._start_candidate_pool_scan(
                model_cfg,
                list(phase.get("items", []) or []),
                scan_source="scheduled",
                scheduled_task_id=task_id,
            )
            if not run_id:
                self._finish_pending_scheduled_task(task_id, False, f"{phase_label}未能启动，可能仍有其他扫描在运行")
                return False
            self._pending_scheduled_auto_task["expected_scan_run_id"] = run_id
            self._pending_scheduled_auto_task["expected_scan_mode"] = DECISION_MODE_CANDIDATE_POOL_SCAN
            return True
        self._finish_pending_scheduled_task(task_id, False, f"不支持的巡检阶段: {phase_type}")
        return False

    def _collect_codes_for_task(self, task_type: str, task_config: dict, cycle_plan: Optional[dict] = None) -> list:
        """Gather stock codes that a scheduled task will need."""
        codes: list[str] = []
        if task_type == "ai_strategy_cycle":
            codes = list((cycle_plan or {}).get("codes", []) or [])
        elif task_type == "position_scan":
            try:
                positions = self.account_panel.get_live_positions()
                codes = [str(p.get("code", "")) for p in positions if p.get("code")]
            except Exception:
                pass
        elif task_type == "candidate_pool_scan":
            try:
                codes = self.decision_panel.stock_pool_service.get_candidate_codes(
                    refresh=True,
                    run_context=self.decision_panel._get_effective_run_context().to_dict(),
                )
            except Exception:
                pass
        return list(dict.fromkeys([c for c in codes if c]))

    def _on_xtquant_failed(self, message: str):
        if self._pending_scheduled_auto_task:
            pending = dict(self._pending_scheduled_auto_task)
            task_id = str(pending.get("task_id", "") or "")
            if task_id:
                self._finish_pending_scheduled_task(task_id, False, f"数据校验失败: {message}")
        QMessageBox.warning(
            self,
            "miniQMT 数据异常",
            f"数据更新前的新鲜度验证失败：\n\n{message}\n\n"
            "最常见原因：miniQMT 客户端长时间未重启，导致数据缓存过期。\n\n"
            "请执行以下操作：\n"
            "1. 完全关闭 miniQMT 客户端\n"
            "2. 重新启动 miniQMT 并登录\n"
            "3. 等待行情连接就绪后重试\n\n"
            "本次定时任务将跳过，数据可能不是最新。",
        )

    def _on_freshness_finished(self, ok: bool, message: str):
        if ok:
            return
        if self._pending_scheduled_auto_task:
            pending = dict(self._pending_scheduled_auto_task)
            task_id = str(pending.get("task_id", "") or "")
            if task_id:
                self._finish_pending_scheduled_task(task_id, False, f"数据更新失败: {message}")

    def _start_startup_orchestration(self):
        if self.startup_orchestrator is None:
            return
        if self.startup_orchestrator.is_running:
            return
        started = self.startup_orchestrator.start()
        if started:
            self.account_panel.show_client_workflow_status("启动自检中...", success=None)

    def _on_startup_status(self, message: str):
        self.statusBar().showMessage(message)
        self.account_panel.show_client_workflow_status(message, success=None)

    def _on_startup_finished(self, success: bool, message: str):
        self.statusBar().showMessage(f"{'✅' if success else '❌'} {message}")
        self.account_panel.show_client_workflow_status(message, success=success)
        self.account_panel._refresh_client_status_safe()
        if success:
            QTimer.singleShot(800, self._try_reconcile_catchup_after_startup)

    def _try_reconcile_catchup_after_startup(self):
        should_run, reason = self.daily_auto_trade.should_run_reconcile_catchup()
        if not should_run:
            logger.info("启动后日终对账补漏未触发: %s", reason)
            return
        logger.info("启动后触发今日日终对账补漏")
        self.statusBar().showMessage("检测到今日日终对账缺失，正在自动补跑...")
        self._start_reconcile_catchup_worker()

    def _start_reconcile_catchup_worker(self):
        if self._reconcile_catchup_worker is not None and self._reconcile_catchup_worker.isRunning():
            return
        self._reconcile_catchup_worker = _ReconcileCatchupWorker(self.daily_auto_trade, self)
        self._reconcile_catchup_worker.finished_reconcile.connect(self._on_reconcile_catchup_worker_finished)
        self._reconcile_catchup_worker.failed_reconcile.connect(self._on_reconcile_catchup_worker_failed)
        self._reconcile_catchup_worker.finished.connect(self._cleanup_reconcile_catchup_worker)
        self._reconcile_catchup_worker.start()

    def _on_reconcile_catchup_worker_finished(self, success: bool, message: str):
        self._on_daily_reconcile_finished(success, message)

    def _on_reconcile_catchup_worker_failed(self, message: str):
        self._on_daily_reconcile_finished(False, f"启动补漏对账异常: {message}")

    def _cleanup_reconcile_catchup_worker(self):
        worker = self._reconcile_catchup_worker
        if worker is None:
            return
        worker.deleteLater()
        self._reconcile_catchup_worker = None

    def closeEvent(self, event):
        if self.startup_orchestrator is not None:
            try:
                self.startup_orchestrator.cancel()
            except Exception:
                pass
        super().closeEvent(event)

    def set_symbol(self, code: str, name: str = ""):
        self.decision_panel.set_symbol(code, name)

    def lookup_symbol_name(self, code: str) -> str:
        if callable(self.symbol_name_resolver):
            try:
                resolved = self.symbol_name_resolver(code)
                if resolved:
                    return str(resolved)
            except Exception:
                pass
        candidates = [code]
        plain_code = code.split(".")[0] if "." in code else code
        if plain_code not in candidates:
            candidates.append(plain_code)
        if "." not in code and plain_code:
            if plain_code.startswith(("5", "6", "9")):
                candidates.append(f"{plain_code}.SH")
            elif plain_code.startswith(("0", "1", "2", "3")):
                candidates.append(f"{plain_code}.SZ")
        for name_map in (self.name_map, self.etf_name_map):
            for candidate in candidates:
                if candidate in name_map and name_map.get(candidate):
                    return str(name_map.get(candidate))
        parent = self.parent()
        if parent is None:
            return ""
        for attr_name in ("name_map", "etf_name_map"):
            inherited_map = getattr(parent, attr_name, None)
            if not isinstance(inherited_map, dict):
                continue
            for candidate in candidates:
                if candidate in inherited_map and inherited_map.get(candidate):
                    return str(inherited_map.get(candidate))
        return ""

    def get_center_status_summary(self) -> dict:
        tasks = self.scheduler.get_tasks()
        enabled_tasks = [task for task in tasks.values() if bool(getattr(task, "enabled", False))]
        runtime_display = self.scheduler.get_task_runtime_display("daily_ai_strategy_cycle")
        return {
            "strategy_id": AI_STOCK_STRATEGY_ID,
            "strategy_name": AI_STOCK_STRATEGY_NAME,
            "scheduler_enabled_count": len(enabled_tasks),
            "scheduler_status_text": self.account_panel.lbl_scheduler_status.text(),
            "startup_running": bool(self.startup_orchestrator and self.startup_orchestrator.is_running),
            "last_run": str(runtime_display.get("last_run", "") or ""),
            "last_result": str(runtime_display.get("last_result", "") or ""),
            "positions_count": len(self.account_panel.get_live_positions()),
            "pending_task": bool(self._pending_scheduled_auto_task),
        }

    def get_center_task_summaries(self) -> list[dict]:
        results: list[dict] = []
        for task_id, task in self.scheduler.get_tasks().items():
            runtime_display = self.scheduler.get_task_runtime_display(task_id)
            results.append(
                {
                    "task_key": task_id,
                    "task_type": str(getattr(task, "task_type", "") or ""),
                    "title": str(getattr(task, "name", task_id) or task_id),
                    "status": "enabled" if bool(getattr(task, "enabled", False)) else "disabled",
                    "message": str(runtime_display.get("last_result", "") or ""),
                    "last_run": str(runtime_display.get("last_run", "") or ""),
                    "schedule_time": str(getattr(task, "time", "") or ""),
                    "next_mode": "auto_execute" if bool(getattr(task, "auto_execute", False)) else "scan_only",
                }
            )
        return results

    def pause_center_automation(self) -> str:
        enabled_ids = [
            task_id for task_id, task in self.scheduler.get_tasks().items()
            if bool(getattr(task, "enabled", False))
        ]
        # 幂等：只有首次暂停时才记录需要恢复的任务，避免重复调用丢失原始状态。
        if not self._paused_scheduler_task_ids:
            self._paused_scheduler_task_ids = list(enabled_ids)
        else:
            existing = set(self._paused_scheduler_task_ids)
            for task_id in enabled_ids:
                if task_id not in existing:
                    self._paused_scheduler_task_ids.append(task_id)
        paused_count = 0
        for task_id in enabled_ids:
            self.scheduler.toggle_task(task_id, False)
            paused_count += 1
        self._refresh_scheduler_status()
        if paused_count == 0:
            return "AI 自动调度已处于暂停状态"
        return f"已暂停 AI 自动调度 {paused_count} 个任务"

    def resume_center_automation(self) -> str:
        restored = 0
        for task_id in list(self._paused_scheduler_task_ids or []):
            if task_id in self.scheduler.get_tasks():
                self.scheduler.toggle_task(task_id, True)
                restored += 1
        self._paused_scheduler_task_ids = []
        self._refresh_scheduler_status()
        return f"已恢复 AI 自动调度 {restored} 个任务"

    def run_end_of_day_tasks(self, snapshot_date: str) -> StrategyEndOfDayResult:
        tracker = self.decision_panel.decision_tracker
        expired_count = tracker.expire_stale_decisions()
        recent_records = tracker.query_recent(limit=200)
        today_records = [rec for rec in recent_records if str(rec.created_at or "").startswith(snapshot_date)]
        executed_count = sum(1 for rec in today_records if rec.outcome in (DecisionOutcome.EXECUTED.value, DecisionOutcome.APPROVED.value))
        closed_count = sum(1 for rec in today_records if bool(rec.closed_at))
        rejected_count = sum(
            1
            for rec in today_records
            if rec.outcome in (
                DecisionOutcome.REJECTED_BY_RISK.value,
                DecisionOutcome.REJECTED_BY_USER.value,
                DecisionOutcome.EXECUTION_FAILED.value,
            )
        )
        stats = tracker.get_stats()
        message = f"决策复盘 {len(today_records)} 条，执行 {executed_count} 条，平仓 {closed_count} 条"
        if rejected_count:
            message += f"，未完成 {rejected_count} 条"
        if expired_count:
            message += f"，过期 {expired_count} 条"
        return StrategyEndOfDayResult(
            strategy_id=AI_STOCK_STRATEGY_ID,
            strategy_name=AI_STOCK_STRATEGY_NAME,
            success=True,
            message=message,
            details={
                "snapshot_date": snapshot_date,
                "today_records": len(today_records),
                "today_executed": executed_count,
                "today_closed": closed_count,
                "today_rejected": rejected_count,
                "expired_count": expired_count,
                "overall_stats": stats,
            },
        )

    def refresh_end_of_day_ui(self) -> None:
        """Refresh end-of-day related UI on the main thread only."""
        self.decision_panel._refresh_history()
        self.account_panel.refresh()
        self.strategy_trade_panel.refresh_all()


class AITradeDecisionWindow(QMainWindow):
    """Window wrapper for the embeddable AI live strategy panel."""

    def __init__(
        self,
        context_provider=None,
        parent=None,
        *,
        symbol_name_resolver: Optional[Callable[[str], str]] = None,
        name_map: Optional[Dict[str, str]] = None,
        etf_name_map: Optional[Dict[str, str]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("AI 交易决策中心")
        self.resize(1400, 850)
        self.name_map = dict(name_map or {})
        self.etf_name_map = dict(etf_name_map or {})

        self.panel = AITradeDecisionPanel(
            context_provider=context_provider,
            parent=self,
            symbol_name_resolver=symbol_name_resolver,
            name_map=self.name_map,
            etf_name_map=self.etf_name_map,
        )
        self.setCentralWidget(self.panel)

        # Preserve legacy attributes relied on by internal parent walking.
        self.account_panel = self.panel.account_panel
        self.decision_panel = self.panel.decision_panel
        self.order_panel = self.panel.order_panel
        self.strategy_trade_panel = self.panel.strategy_trade_panel

    def set_symbol(self, code: str, name: str = ""):
        self.panel.set_symbol(code, name)

    def lookup_symbol_name(self, code: str) -> str:
        return self.panel.lookup_symbol_name(code)
