"""AI 实盘决策 — 独立窗口

将 AI 实盘决策分析、统一执行、账户信息三大功能聚合在同一面板中，
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
    QMenu,
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
from common.execution_contract import OrderExecutionReport, StrategySignal
from common.live_strategy_shell import LiveStrategyShell
from common.market_data_policy import is_etf_like_code
from common.scheduler_dialog_base import BaseSchedulerSettingsDialog
from common.strategy_config_dialog_base import BaseStrategyConfigDialog
from common.strategy_panel_context import StrategyPanelContext

try:
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
        RiskCheckItem,
        RiskCheckResult,
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
    from trading_app.services.strategy_constants import (
        AI_STOCK_STRATEGY_ID,
        AI_STOCK_STRATEGY_NAME,
        AI_STOCK_VIRTUAL_ACCOUNT_ID,
        UNMANAGED_STRATEGY_ID,
        UNMANAGED_STRATEGY_NAME,
        UNMANAGED_VIRTUAL_ACCOUNT_ID,
    )
    from trading_app.services.strategy_registry_service import get_strategy_registry_service
    from trading_app.services.trade_execution_service import ExecutionRequest, get_trade_execution_service
    from trading_app.services.trade_record_service import TradeSource
    from trading_app.services.live_strategy_end_of_day_service import StrategyEndOfDayResult
    from trading_app.services.data_update_result import DataUpdateResult
    from trading_app.services.market_data_status_service import get_market_data_status_service
    from common.broker_session_service import get_broker_session_service
    from trading_app.watchlist_manager import WatchlistManager
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
        RiskCheckItem,
        RiskCheckResult,
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
    from trading_app.services.strategy_constants import (
        AI_STOCK_STRATEGY_ID,
        AI_STOCK_STRATEGY_NAME,
        AI_STOCK_VIRTUAL_ACCOUNT_ID,
        UNMANAGED_STRATEGY_ID,
        UNMANAGED_STRATEGY_NAME,
        UNMANAGED_VIRTUAL_ACCOUNT_ID,
    )
    from trading_app.services.strategy_registry_service import get_strategy_registry_service
    from trading_app.services.trade_execution_service import ExecutionRequest, get_trade_execution_service
    from trading_app.services.trade_record_service import TradeSource
    from trading_app.services.live_strategy_end_of_day_service import StrategyEndOfDayResult
    from trading_app.services.data_update_result import DataUpdateResult
    from trading_app.services.market_data_status_service import get_market_data_status_service
    from common.broker_session_service import get_broker_session_service
    from trading_app.watchlist_manager import WatchlistManager

from trading_app.widgets.strategy_risk_settings_panel import StrategyRiskSettingsPanel
from trading_app.widgets.ai_trade_decision.collapsible_step_card import CollapsibleStepCard
from trading_app.widgets.ai_trade_decision.constants import (
    DECISION_MODE_CANDIDATE_POOL_SCAN,
    DECISION_MODE_POSITION_SCAN,
    SCAN_SCOPE_AI_MANAGED,
    SCAN_SCOPE_LABELS,
    SCAN_SCOPE_UNMANAGED,
    SCAN_SUBAGENT_CONCURRENCY,
    SCAN_SUBAGENT_REQUEST_TIMEOUT_SECONDS,
    TASK_TYPE_AI_STRATEGY_CYCLE,
    TASK_TYPE_CANDIDATE_POOL_SCAN,
    TASK_TYPE_POSITION_SCAN,
    TASK_TYPE_UNMANAGED_POSITION_SCAN,
)
from trading_app.widgets.ai_trade_decision.helpers import (
    _StatusMessageProxy,
    _build_scheduled_scan_batch_record,
    _build_scan_status_text,
    _check_ai_live_market_data_ready,
    _get_chat_thread_class,
    _make_json_safe,
    _serialize_scan_result_for_record,
)
from trading_app.widgets.ai_trade_decision.order_execution_panel import OrderExecutionPanel
from trading_app.widgets.ai_trade_decision.workers import (
    _AccountRefreshWorker,
    _ClientActionWorker,
    _ClientStatusWorker,
    _ReconcileCatchupWorker,
)

logger = logging.getLogger(__name__)

class AccountPanel(QWidget):
    """Compact account + position summary panel."""

    scheduler_settings_requested = pyqtSignal()
    manual_order_requested = pyqtSignal()
    primary_action_requested = pyqtSignal(str)
    model_select_requested = pyqtSignal()

    def __init__(self, parent=None, *, show_connection_panel: bool = True, shared_broker_panel=None):
        super().__init__(parent)
        self.broker = get_broker_session_service()
        self.strategy_registry = get_strategy_registry_service()
        self.strategy_budget = get_strategy_budget_service()
        self.auto_trade_config_service = get_auto_trade_config_service()
        self.show_connection_panel = bool(show_connection_panel)
        self.shared_broker_panel = shared_broker_panel
        self.position_scope = SCAN_SCOPE_AI_MANAGED
        self.asset_group_title = "账户概览（AI实盘决策虚拟账户）"
        self.show_scheduler_controls = True
        self.show_config_controls = True
        self.show_manual_order_controls = True
        self.action_note_text = ""
        self.show_primary_action_controls = False
        self.show_candidate_pool_action = False
        self.primary_position_action_text = "持仓巡检"
        self.primary_candidate_action_text = "候选池巡检"
        self.current_model_display = "-"
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
        self.asset_group = QGroupBox(self.asset_group_title)
        asset_form = QFormLayout(self.asset_group)
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
        layout.addWidget(self.asset_group)

        self.action_group = QGroupBox("操作")
        self.action_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        action_layout = QVBoxLayout(self.action_group)
        action_layout.setContentsMargins(8, 8, 8, 8)
        action_layout.setSpacing(6)
        action_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        utility_btn_min_width = 112
        utility_btn_height = 30
        manual_btn_height = 30

        self.settings_label = QLabel("设置")
        self.settings_label.setStyleSheet(section_title_style)
        action_layout.addWidget(self.settings_label)

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
        self.btn_toggle_config.setToolTip("打开 AI 实盘决策配置弹窗（默认只读）")
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

        # AI 实盘决策风控（声明式 schema 自动渲染；与 ETF Tab 共用 StrategyRiskSettingsPanel）
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
            logger.error("初始化 AI 实盘决策风控面板失败: %s", exc, exc_info=True)

        self.risk_policy_panel: Optional[StrategyRiskSettingsPanel] = None
        if configurable_policy is not None:
            self.risk_policy_panel = StrategyRiskSettingsPanel(
                policy=configurable_policy,
                title="AI 实盘决策风控（网关统一）",
            )
            container_layout.addWidget(self.risk_policy_panel)

        self._lock_config_panels()

        self.sep_settings = QFrame()
        self.sep_settings.setFrameShape(QFrame.Shape.HLine)
        self.sep_settings.setStyleSheet("color:#3c3c3c;")
        action_layout.addWidget(self.sep_settings)

        self.primary_label = QLabel("主操作")
        self.primary_label.setStyleSheet(section_title_style)
        action_layout.addWidget(self.primary_label)

        self.lbl_current_model = QLabel("当前模型: -")
        self.lbl_current_model.setStyleSheet("color:#6B7280;font-size:11px;")
        self.lbl_current_model.setWordWrap(True)
        action_layout.addWidget(self.lbl_current_model)

        self.btn_select_model = QPushButton("切换模型")
        self.btn_select_model.setMinimumHeight(utility_btn_height)
        self.btn_select_model.setStyleSheet(
            "QPushButton{background:#334155;color:#ffffff;padding:6px 10px;"
            "border:1px solid #475569;border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#475569;}"
        )
        self.btn_select_model.clicked.connect(lambda: self.model_select_requested.emit())
        action_layout.addWidget(self.btn_select_model)

        self.btn_primary_position_scan = QPushButton(self.primary_position_action_text)
        self.btn_primary_position_scan.setMinimumHeight(manual_btn_height)
        self.btn_primary_position_scan.setStyleSheet(
            "QPushButton{background:#2563EB;color:#ffffff;padding:6px 10px;"
            "border-radius:4px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#1D4ED8;}"
        )
        self.btn_primary_position_scan.clicked.connect(
            lambda: self.primary_action_requested.emit("position_scan")
        )
        action_layout.addWidget(self.btn_primary_position_scan)

        self.btn_primary_candidate_pool_scan = QPushButton(self.primary_candidate_action_text)
        self.btn_primary_candidate_pool_scan.setMinimumHeight(manual_btn_height)
        self.btn_primary_candidate_pool_scan.setStyleSheet(
            "QPushButton{background:#7C3AED;color:#ffffff;padding:6px 10px;"
            "border-radius:4px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#6D28D9;}"
        )
        self.btn_primary_candidate_pool_scan.clicked.connect(
            lambda: self.primary_action_requested.emit("candidate_pool_scan")
        )
        action_layout.addWidget(self.btn_primary_candidate_pool_scan)

        self.sep_primary = QFrame()
        self.sep_primary.setFrameShape(QFrame.Shape.HLine)
        self.sep_primary.setStyleSheet("color:#3c3c3c;")
        action_layout.addWidget(self.sep_primary)

        self.manual_label = QLabel("手动干预")
        self.manual_label.setStyleSheet(section_title_style)
        action_layout.addWidget(self.manual_label)

        self.btn_manual_order = QPushButton("手动委托")
        self.btn_manual_order.clicked.connect(lambda: self.manual_order_requested.emit())
        self.btn_manual_order.setMinimumHeight(manual_btn_height)
        self.btn_manual_order.setStyleSheet(
            "QPushButton{background:#2d2d2d;color:#ffffff;padding:6px 10px;"
            "border:1px solid #3c3c3c;border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#3c3c3c;}"
        )
        action_layout.addWidget(self.btn_manual_order)

        self.action_note_label = QLabel(self.action_note_text)
        self.action_note_label.setWordWrap(True)
        self.action_note_label.setStyleSheet("color:#94A3B8;font-size:11px;")
        self.action_note_label.setVisible(bool(self.action_note_text))
        action_layout.addWidget(self.action_note_label)
        layout.addWidget(self.action_group)
        layout.addStretch()

        self.broker.connection_changed.connect(self._on_connection_changed)
        if self.broker.is_connected:
            self._on_connection_changed(True, "已连接")
        self._load_trade_config()
        self._apply_action_section_visibility()

    def configure_scope(
        self,
        *,
        position_scope: str,
        asset_group_title: str = "",
        show_scheduler_controls: bool = True,
        show_config_controls: bool = True,
        show_manual_order_controls: bool = True,
        action_note_text: str = "",
    ) -> None:
        self.position_scope = str(position_scope or SCAN_SCOPE_AI_MANAGED)
        self.asset_group_title = str(asset_group_title or self.asset_group_title)
        self.show_scheduler_controls = bool(show_scheduler_controls)
        self.show_config_controls = bool(show_config_controls)
        self.show_manual_order_controls = bool(show_manual_order_controls)
        self.action_note_text = str(action_note_text or "")
        if hasattr(self, "asset_group"):
            self.asset_group.setTitle(self.asset_group_title)
        if hasattr(self, "action_note_label"):
            self.action_note_label.setText(self.action_note_text)
            self.action_note_label.setVisible(bool(self.action_note_text))
        self._apply_action_section_visibility()

    def configure_primary_actions(
        self,
        *,
        show_controls: bool,
        show_candidate_pool: bool,
        position_text: str = "持仓巡检",
        candidate_text: str = "候选池巡检",
    ) -> None:
        self.show_primary_action_controls = bool(show_controls)
        self.show_candidate_pool_action = bool(show_candidate_pool)
        self.primary_position_action_text = str(position_text or "持仓巡检")
        self.primary_candidate_action_text = str(candidate_text or "候选池巡检")
        if hasattr(self, "btn_primary_position_scan"):
            self.btn_primary_position_scan.setText(self.primary_position_action_text)
        if hasattr(self, "btn_primary_candidate_pool_scan"):
            self.btn_primary_candidate_pool_scan.setText(self.primary_candidate_action_text)
        self._apply_action_section_visibility()

    def set_current_model_display(self, model_name: str) -> None:
        self.current_model_display = str(model_name or "-")
        if hasattr(self, "lbl_current_model"):
            self.lbl_current_model.setText(f"当前模型: {self.current_model_display}")

    def _apply_action_section_visibility(self) -> None:
        show_settings = bool(self.show_scheduler_controls or self.show_config_controls)
        show_primary = bool(self.show_primary_action_controls)
        show_manual = bool(self.show_manual_order_controls)
        self.settings_label.setVisible(show_settings)
        self.lbl_scheduler_status.setVisible(self.show_scheduler_controls)
        self.btn_open_schedule.setVisible(self.show_scheduler_controls)
        self.btn_toggle_config.setVisible(self.show_config_controls)
        self.sep_settings.setVisible(show_settings and (show_primary or show_manual))
        self.primary_label.setVisible(show_primary)
        self.lbl_current_model.setVisible(show_primary)
        self.btn_select_model.setVisible(show_primary)
        self.btn_primary_position_scan.setVisible(show_primary)
        self.btn_primary_candidate_pool_scan.setVisible(show_primary and self.show_candidate_pool_action)
        self.sep_primary.setVisible(show_primary and show_manual)
        self.manual_label.setVisible(show_manual)
        self.btn_manual_order.setVisible(show_manual)
        self.action_group.setVisible(show_settings or show_primary or show_manual or bool(self.action_note_text))

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
            relevant_positions = positions
            if self.position_scope == SCAN_SCOPE_AI_MANAGED:
                relevant_positions = self._filter_ai_strategy_positions(positions)
            self._update_assets(asset, relevant_positions)
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
            if self.position_scope == SCAN_SCOPE_UNMANAGED:
                live_positions = self._build_unmanaged_live_positions_from_records(asset, positions)
                account = self.strategy_budget.build_account_snapshot(
                    UNMANAGED_STRATEGY_ID,
                    strategy_name=UNMANAGED_STRATEGY_NAME,
                    virtual_account_id=UNMANAGED_VIRTUAL_ACCOUNT_ID,
                    real_total_asset=float(self._record_value(asset, "total_asset", default=0.0) or 0.0),
                    live_positions=[
                        {
                            "stock_code": item.get("code", "") or "",
                            "name": item.get("name", "") or "",
                            "volume": int(item.get("volume", 0) or 0),
                            "market_value": float(item.get("market_value", 0.0) or 0.0),
                        }
                        for item in live_positions
                    ],
                )
            else:
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

    def refresh_shared_setting_hint(self) -> None:
        """保留空实现，供统一入口刷新时复用。"""
        return

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
        if self.position_scope == SCAN_SCOPE_UNMANAGED:
            return self.get_unmanaged_broker_context()
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

    def get_unmanaged_broker_context(
        self,
        positions: Optional[List[Dict[str, Any]]] = None,
    ) -> BrokerContext:
        if not self.broker.is_connected:
            return BrokerContext()
        positions = list(positions or self.get_unmanaged_live_positions())
        try:
            asset = self.broker.query_stock_asset()
        except Exception:
            return BrokerContext(connected=True)
        snapshot = self.strategy_budget.build_account_snapshot(
            UNMANAGED_STRATEGY_ID,
            strategy_name=UNMANAGED_STRATEGY_NAME,
            virtual_account_id=UNMANAGED_VIRTUAL_ACCOUNT_ID,
            real_total_asset=float(self._record_value(asset, "total_asset", default=0.0) or 0.0),
            live_positions=[
                {
                    "stock_code": item.get("code", "") or "",
                    "name": item.get("name", "") or "",
                    "volume": int(item.get("volume", 0) or 0),
                    "market_value": float(item.get("market_value", 0.0) or 0.0),
                }
                for item in positions
            ],
        )
        top = [
            {
                "code": item.get("code", "") or "",
                "volume": int(item.get("volume", 0) or 0),
                "cost_price": float(item.get("cost_price", 0.0) or 0.0),
                "market_value": float(item.get("market_value", 0.0) or 0.0),
            }
            for item in positions
            if item.get("code")
        ]
        return BrokerContext(
            connected=True,
            account_id=getattr(self.broker, "_last_config", {}).get("account", ""),
            total_asset=float(snapshot.get("total_asset", 0.0) or 0.0),
            available_cash=float(snapshot.get("available_cash", 0.0) or 0.0),
            position_count=len(positions),
            top_positions=top,
        )

    def get_live_positions(self) -> List[Dict[str, Any]]:
        if self.position_scope == SCAN_SCOPE_UNMANAGED:
            return self.get_unmanaged_live_positions()
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

    def get_unmanaged_live_positions(self) -> List[Dict[str, Any]]:
        if not self.broker.is_connected:
            return []
        try:
            asset = self.broker.query_stock_asset()
            broker_positions = self.broker.query_stock_positions() or []
        except Exception:
            return []
        return self._build_unmanaged_live_positions_from_records(asset, broker_positions)

    def _build_unmanaged_live_positions_from_records(self, asset: Any, broker_positions: Any) -> List[Dict[str, Any]]:
        if not asset:
            return []

        broker_live_positions: List[Dict[str, Any]] = []
        broker_reconcile_positions: List[Dict[str, Any]] = []
        live_meta: Dict[str, Dict[str, Any]] = {}
        for pos in broker_positions:
            code = self._plain_code(self._record_value(pos, "stock_code", default="") or "")
            volume = int(self._record_value(pos, "volume", default=0) or 0)
            if not code or volume <= 0:
                continue
            item = {
                "stock_code": code,
                "market_value": float(self._record_value(pos, "market_value", default=0.0) or 0.0),
                "volume": volume,
                "name": self._resolve_symbol_name(code, self._record_value(pos, "stock_name", default="") or ""),
            }
            broker_live_positions.append(item)
            live_meta[code] = {
                "name": item["name"],
                "can_use_volume": int(self._record_value(pos, "can_use_volume", default=volume) or volume),
            }
            broker_reconcile_positions.append(
                {
                    "stock_code": code,
                    "volume": volume,
                    "open_price": float(self._record_value(pos, "open_price", default=0.0) or 0.0),
                }
            )

        try:
            self.strategy_budget.reconcile_unmanaged_with_broker(
                broker_cash=float(
                    self._record_value(
                        asset,
                        "cash",
                        default=self._record_value(asset, "available_cash", default=0.0),
                    )
                    or 0.0
                ),
                broker_positions=broker_reconcile_positions,
            )
        except Exception:
            logger.exception("对账未管理账户持仓失败")
            return []

        rows = self.strategy_budget.get_positions_view(
            UNMANAGED_STRATEGY_ID,
            strategy_name=UNMANAGED_STRATEGY_NAME,
            virtual_account_id=UNMANAGED_VIRTUAL_ACCOUNT_ID,
            real_total_asset=float(self._record_value(asset, "total_asset", default=0.0) or 0.0),
            live_positions=broker_live_positions,
        )
        results: List[Dict[str, Any]] = []
        for row in rows:
            code = self._plain_code(str(row.get("stock_code", "") or ""))
            if not code:
                continue
            meta = live_meta.get(code, {})
            results.append(
                {
                    "code": code,
                    "name": meta.get("name") or row.get("stock_name") or self._resolve_symbol_name(code),
                    "volume": int(row.get("quantity", 0) or 0),
                    "can_use_volume": int(meta.get("can_use_volume", row.get("quantity", 0)) or 0),
                    "cost_price": float(row.get("avg_cost", 0.0) or 0.0),
                    "market_value": float(row.get("market_value", 0.0) or 0.0),
                    "profit_rate": float(row.get("unrealized_pnl_pct", 0.0) or 0.0) / 100.0,
                    "scan_scope": SCAN_SCOPE_UNMANAGED,
                }
            )
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

class DecisionPanel(QWidget):
    """AI trade decision analysis and display panel."""

    decision_ready = pyqtSignal(object)  # TradeDecision
    scan_completed = pyqtSignal(object)
    market_view_requested = pyqtSignal(str, str)

    def __init__(
        self,
        context_provider=None,
        parent=None,
        *,
        allow_candidate_pool_scan: bool = True,
        position_scan_label: str = "持仓巡检",
        position_scan_hint: str = "持仓巡检: 自动读取当前券商持仓，逐只生成持有/加仓/减仓/卖出决策",
    ):
        super().__init__(parent)
        self.context_provider = context_provider
        self.allow_candidate_pool_scan = bool(allow_candidate_pool_scan)
        self.position_scan_label = str(position_scan_label or "持仓巡检")
        self.position_scan_hint = str(position_scan_hint or "")
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
        self._scheduled_scan_records: List[Dict[str, Any]] = []
        self._scheduled_scan_record_items: List[Dict[str, Any]] = []
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
        self._active_scan_scope: str = SCAN_SCOPE_AI_MANAGED
        self._active_scan_label: str = ""
        self._active_scan_allow_auto_execute: bool = True
        self._active_scan_broker_context: Optional[BrokerContext] = None
        self._run_context_override: Optional[DecisionRunContext] = None
        self._stream_started = False
        self._progress_cards: List[CollapsibleStepCard] = []
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
        self.top_controls_widget = QWidget(self)
        top_row = QHBoxLayout(self.top_controls_widget)
        top_row.setContentsMargins(0, 0, 0, 0)
        self.mode_label = QLabel("模式:")
        top_row.addWidget(self.mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(self.position_scan_label, DECISION_MODE_POSITION_SCAN)
        if self.allow_candidate_pool_scan:
            self.mode_combo.addItem("候选池巡检", DECISION_MODE_CANDIDATE_POOL_SCAN)
        self.mode_combo.setFixedWidth(120)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_row.addWidget(self.mode_combo)

        self.symbol_label = QLabel("标的:")
        top_row.addWidget(self.symbol_label)
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("输入代码，如 000001.SZ（留空则用主窗口当前标的）")
        self.symbol_input.setFixedWidth(240)
        self.symbol_input.setVisible(False)
        top_row.addWidget(self.symbol_input)

        self.watchlist_group_combo = QComboBox()
        self.watchlist_group_combo.setFixedWidth(140)
        self.watchlist_group_combo.setVisible(False)
        top_row.addWidget(self.watchlist_group_combo)

        self.mode_hint_label = QLabel(self.position_scan_hint)
        self.mode_hint_label.setStyleSheet("color: #666;")
        top_row.addWidget(self.mode_hint_label)

        self.model_label = QLabel("模型:")
        top_row.addWidget(self.model_label)
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
        layout.addWidget(self.top_controls_widget)

        # -- Stacked: placeholder vs result --
        self.stack = QStackedWidget()

        # Page 0: placeholder
        mode_placeholder = (
            "当前支持“持仓巡检”和“候选池巡检”两种模式。"
            if self.allow_candidate_pool_scan
            else "当前仅提供未管理持仓巡检模式。"
        )
        placeholder = QLabel(
            "点击「开始巡检」开始分析当前策略任务\n\n"
            + mode_placeholder
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
        self.scan_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scan_table.customContextMenuRequested.connect(
            lambda pos: self._on_scan_result_context_menu(self.scan_table, pos, self._scan_results)
        )
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

        # Tab 4: Scheduled scan records
        self.scheduled_scan_records_widget = QWidget()
        scheduled_layout = QVBoxLayout(self.scheduled_scan_records_widget)
        scheduled_layout.setContentsMargins(0, 0, 0, 0)
        scheduled_layout.setSpacing(6)

        self.scheduled_scan_summary_label = QLabel("暂无定时巡检记录")
        self.scheduled_scan_summary_label.setStyleSheet("color: #888;")
        scheduled_layout.addWidget(self.scheduled_scan_summary_label)

        scheduled_splitter = QSplitter(Qt.Orientation.Vertical)
        scheduled_splitter.setChildrenCollapsible(False)
        scheduled_splitter.setHandleWidth(12)

        batch_host = QWidget()
        batch_layout = QVBoxLayout(batch_host)
        batch_layout.setContentsMargins(0, 0, 0, 0)
        batch_layout.setSpacing(4)
        batch_layout.addWidget(QLabel("批次列表"))
        self.scheduled_scan_batches_table = QTableWidget(0, 7)
        self.scheduled_scan_batches_table.setHorizontalHeaderLabels(
            ["完成时间", "任务", "巡检", "总数", "可操作", "风控", "说明"]
        )
        self.scheduled_scan_batches_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.scheduled_scan_batches_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.scheduled_scan_batches_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.scheduled_scan_batches_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.scheduled_scan_batches_table.verticalHeader().setVisible(False)
        self.scheduled_scan_batches_table.setAlternatingRowColors(True)
        self.scheduled_scan_batches_table.setStyleSheet(self.scan_table.styleSheet())
        self.scheduled_scan_batches_table.itemSelectionChanged.connect(
            self._on_scheduled_scan_batch_selection_changed
        )
        batch_layout.addWidget(self.scheduled_scan_batches_table, stretch=1)
        scheduled_splitter.addWidget(batch_host)

        detail_host = QWidget()
        detail_layout = QVBoxLayout(detail_host)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(4)
        self.scheduled_scan_detail_label = QLabel("选择一组定时巡检记录后，可在下方查看明细")
        self.scheduled_scan_detail_label.setStyleSheet("color: #888;")
        detail_layout.addWidget(self.scheduled_scan_detail_label)
        self.scheduled_scan_detail_table = QTableWidget(0, 9)
        self.scheduled_scan_detail_table.setHorizontalHeaderLabels(
            ["序号", "代码", "名称", "操作", "置信度", "现价", "成本", "风控", "状态"]
        )
        self.scheduled_scan_detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.scheduled_scan_detail_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.scheduled_scan_detail_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.scheduled_scan_detail_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.scheduled_scan_detail_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scheduled_scan_detail_table.customContextMenuRequested.connect(
            lambda pos: self._on_scan_result_context_menu(
                self.scheduled_scan_detail_table,
                pos,
                self._scheduled_scan_record_items,
            )
        )
        self.scheduled_scan_detail_table.verticalHeader().setVisible(False)
        self.scheduled_scan_detail_table.setAlternatingRowColors(True)
        self.scheduled_scan_detail_table.setStyleSheet(self.scan_table.styleSheet())
        self.scheduled_scan_detail_table.itemSelectionChanged.connect(
            self._on_scheduled_scan_detail_selection_changed
        )
        detail_layout.addWidget(self.scheduled_scan_detail_table, stretch=1)
        scheduled_splitter.addWidget(detail_host)
        scheduled_splitter.setSizes([180, 260])
        scheduled_layout.addWidget(scheduled_splitter, stretch=1)
        self.result_tabs.addTab(self.scheduled_scan_records_widget, "定时记录")

        # Tab 5: Decision card
        self.decision_card_widget = QWidget()
        self.decision_card_layout = QVBoxLayout(self.decision_card_widget)
        self.decision_card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.result_tabs.addTab(self.decision_card_widget, "决策详情")

        # Tab 6: Decision history + stats
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

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.decision_status_label = QLabel("")
        action_row.addWidget(self.decision_status_label)
        result_layout.addLayout(action_row)

        self.stack.addWidget(result_widget)
        layout.addWidget(self.stack, stretch=1)
        self._on_mode_changed()

    def set_symbol(self, code: str, name: str = ""):
        self.symbol_input.setText(code)

    def set_top_controls_visible(self, visible: bool) -> None:
        if hasattr(self, "top_controls_widget"):
            self.top_controls_widget.setVisible(bool(visible))

    def get_current_model_name(self) -> str:
        return str(self.model_combo.currentText() or "")

    def prompt_select_model(self, parent: Optional[QWidget] = None) -> str:
        model_names = [self.model_combo.itemText(i) for i in range(self.model_combo.count())]
        if not model_names:
            QMessageBox.warning(parent or self, "提示", "当前没有可用模型")
            return ""
        try:
            from PyQt6.QtWidgets import QInputDialog
        except ImportError:
            return ""
        current = self.get_current_model_name()
        current_index = max(self.model_combo.currentIndex(), 0)
        selected, ok = QInputDialog.getItem(
            parent or self,
            "选择模型",
            "请选择巡检使用的模型:",
            model_names,
            current_index,
            False,
        )
        if not ok or not str(selected or "").strip():
            return current
        idx = self.model_combo.findText(str(selected))
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        return self.get_current_model_name()

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
            DECISION_MODE_POSITION_SCAN: self.position_scan_hint,
            DECISION_MODE_CANDIDATE_POOL_SCAN: "候选池巡检: 先按量化规则生成今日候选池，再交给AI逐只评估买入机会",
        }
        self.mode_hint_label.setText(hints.get(self._current_mode, ""))
        btn_texts = {
            DECISION_MODE_POSITION_SCAN: f"🔎 开始{self.position_scan_label}",
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
            from trading_app.services.data_freshness_service import check_parquet_freshness

        unique_codes = []
        seen_codes = set()
        for code in codes:
            normalized = str(code or "").strip().split(".", 1)[0]
            if not normalized or normalized in seen_codes:
                continue
            seen_codes.add(normalized)
            unique_codes.append(normalized)

        if not unique_codes:
            proceed_callback()
            return

        stale_items = []
        for code in unique_codes:
            fresh, info = check_parquet_freshness(code)
            if not fresh:
                stale_items.append((code, info))

        if not stale_items:
            proceed_callback()
            return

        stale_preview = "\n".join(f"  {c}: 最新 {d}" for c, d in stale_items[:8])
        if len(stale_items) > 8:
            stale_preview += f"\n  ... 还有 {len(stale_items) - 8} 只"

        trade_window = self._find_trade_window()
        if not trade_window or not hasattr(trade_window, "freshness_guard"):
            QMessageBox.critical(
                self,
                "本地数据未更新",
                f"检测到 {len(stale_items)} 只标的的 K 线数据不是最新的：\n\n"
                f"{stale_preview}\n\n"
                "当前环境未连接 DataFreshnessGuard，无法安全自动更新。为避免实盘策略使用过期数据，本次分析已阻断。",
            )
            return

        dlg = QMessageBox(self)
        dlg.setWindowTitle("本地数据未更新")
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setText(
            f"检测到 {len(stale_items)} 只标的的 K 线数据不是最新的：\n\n"
            f"{stale_preview}\n\n"
                "实盘策略中枢不允许使用过期 K 线继续分析，请先完成数据更新。"
        )
        btn_update = dlg.addButton("检测 miniQMT 并更新数据", QMessageBox.ButtonRole.AcceptRole)
        btn_cancel = dlg.addButton("取消", QMessageBox.ButtonRole.DestructiveRole)
        dlg.setDefaultButton(btn_update)
        dlg.exec()

        clicked = dlg.clickedButton()
        if clicked == btn_cancel:
            return

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
            unique_codes,
            proceed_callback,
            include_indices=True,
            prefer_realtime=True,
            require_minute_freshness=False,
        )

    def _start_single_decision(
        self,
        context: AgentRuntimeContext,
        model_cfg: Dict[str, str],
        *,
        user_prompt: str | None = None,
        scan_item: Optional[Dict[str, Any]] = None,
    ):
        self._active_scan_scope = SCAN_SCOPE_AI_MANAGED
        self._active_scan_label = ""
        self._active_scan_allow_auto_execute = True
        self._active_scan_broker_context = None
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
        items: Optional[List[Dict[str, Any]]] = None,
        scan_scope: str = "",
        scan_label: str = "",
        allow_auto_execute: bool = True,
        broker_context: Optional[BrokerContext] = None,
    ):
        if self._run_context_override is None:
            self._set_run_context_override(None)
        account_panel = self._find_account_panel()
        if account_panel is None:
            QMessageBox.warning(self, "提示", "未找到账户面板")
            return ""
        resolved_scope = str(
            scan_scope
            or getattr(account_panel, "position_scope", "")
            or SCAN_SCOPE_AI_MANAGED
        )
        positions = [dict(item) for item in (items or [])]
        if not positions:
            positions = account_panel.get_live_positions()
        positions = [
            {
                **dict(item),
                "scan_scope": str(item.get("scan_scope") or resolved_scope or SCAN_SCOPE_AI_MANAGED),
            }
            for item in positions
        ]
        if not positions:
            QMessageBox.warning(self, "提示", "当前无可巡检持仓，请先连接券商并确认持仓数据")
            return ""
        if broker_context is None and account_panel is not None:
            try:
                broker_context = account_panel.get_broker_context()
            except Exception:
                broker_context = None
        effective_label = scan_label or (
            self.position_scan_label if resolved_scope == SCAN_SCOPE_UNMANAGED else self.position_scan_label
        )

        scan_run_id = self._begin_scan_session(
            items=positions,
            scan_source=scan_source,
            scheduled_task_id=scheduled_task_id,
            scan_scope=resolved_scope,
            scan_label=effective_label,
            allow_auto_execute=allow_auto_execute,
            broker_context=broker_context,
        )
        if not scan_run_id:
            return ""
        self._active_model_cfg = model_cfg
        self.scan_table.setRowCount(0)
        self.analysis_display.clear()
        self._populate_decision_card(None, None)
        self.stack.setCurrentIndex(1)
        self.progress_label.setText(f"准备开始{effective_label}，共 {len(positions)} 只持仓...")
        self._set_progress_steps(
            "执行步骤概要",
            [
                f"接收{effective_label}请求，本轮共识别到 {len(positions)} 只有效持仓。",
                f"选择并行子代理模式处理，最大并发数设为 {SCAN_SUBAGENT_CONCURRENCY}。",
                "每只持仓都会单独完成：上下文构建 -> 证据采集 -> 模型推理 -> 决策提取 -> 风控评估。",
                "本轮仅生成巡检建议，不会直接发起新增买入。" if not allow_auto_execute else "巡检汇总表会在每只股票完成后实时追加结果。",
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
        scan_scope: str = SCAN_SCOPE_AI_MANAGED,
        scan_label: str = "",
        allow_auto_execute: bool = True,
        broker_context: Optional[BrokerContext] = None,
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
        self._active_scan_scope = str(scan_scope or SCAN_SCOPE_AI_MANAGED)
        self._active_scan_label = str(scan_label or "")
        self._active_scan_allow_auto_execute = bool(allow_auto_execute)
        self._active_scan_broker_context = broker_context
        return self._active_scan_run_id

    def _launch_scan_subagents(self):
        is_candidate_pool = self._current_mode == DECISION_MODE_CANDIDATE_POOL_SCAN
        while self._scan_queue and len(self._scan_active_workers) < SCAN_SUBAGENT_CONCURRENCY:
            item = self._scan_queue.pop(0)
            context = self._build_runtime_context_for_symbol(item["code"], item["name"])
            if self._active_scan_broker_context is not None:
                context.broker = self._active_scan_broker_context
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
            mode_label = self._active_scan_label or "持仓巡检"
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
            f"- 持仓来源: {SCAN_SCOPE_LABELS.get(str(position.get('scan_scope') or self._active_scan_scope), '当前账户持仓')}",
            f"- 当前持仓数量: {volume} 股，可卖数量: {can_use} 股",
            f"- 持仓成本价: {cost:.3f}" if cost > 0 else "- 持仓成本价: 未知",
            f"- 当前持仓盈亏: {profit_rate:+.2f}%",
            f"- 当前持仓市值: ¥{market_value:,.2f}",
            "",
            "请重点判断：继续持有、加仓、减仓、卖出、还是继续观察。",
            "如果建议卖出或减仓，请明确给出触发依据；如果建议继续持有，也要说明需要继续跟踪的风险信号。",
        ]
        if str(position.get("scan_scope") or self._active_scan_scope) == SCAN_SCOPE_UNMANAGED:
            extra_lines.extend(
                [
                    "",
                    "这是未管理账户的持仓巡检，本轮只输出持仓建议，不考虑候选池，也不考虑新开仓。",
                ]
            )
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
                scan_label = self._active_scan_label or "持仓巡检"
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
                "scan_scope": self._active_scan_scope,
                "scan_label": self._active_scan_label,
                "allow_auto_execute": self._active_scan_allow_auto_execute,
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
        broker_ctx = self._active_scan_broker_context or BrokerContext()
        if self._active_scan_broker_context is None:
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
            "scan_scope": str((scan_item or {}).get("scan_scope", "") or self._active_scan_scope),
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
                "decision_record_id": str(result.get("decision_record_id", "") or ""),
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
            self.decision_status_label.setText("⚠ 未能提取有效决策")
            self.decision_status_label.setStyleSheet("color: orange; font-weight: bold;")
            return
        if self._scan_in_progress:
            self.decision_status_label.setText(
                f"🔄 巡检进行中: {TRADE_ACTION_LABELS.get(decision.action, decision.action)}"
            )
            self.decision_status_label.setStyleSheet("color: #0078d4; font-weight: bold;")
            return
        if decision.is_actionable and risk_result and risk_result.passed:
            self.decision_status_label.setText("✅ 风控通过，可执行")
            self.decision_status_label.setStyleSheet("color: green; font-weight: bold;")
        elif decision.is_actionable:
            self.decision_status_label.setText("⛔ 风控未通过")
            self.decision_status_label.setStyleSheet("color: red; font-weight: bold;")
        else:
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

        self._populate_scan_result_row(self.scan_table, row, result)

    def _build_scan_result_row_values(self, result: Dict[str, Any], row: int) -> List[str]:
        decision = result["decision"]
        risk_result = result["risk_result"]
        scan_item = result["scan_item"] or {}
        cost_price = float(scan_item.get("cost_price", 0) or 0)
        action_label = "解析失败" if decision is None else TRADE_ACTION_LABELS.get(decision.action, decision.action)
        confidence_text = "-" if decision is None else f"{decision.confidence:.0%}"
        current_price_text = "-" if decision is None or decision.current_price <= 0 else f"{decision.current_price:.2f}"
        cost_text = "-" if cost_price <= 0 else f"{cost_price:.2f}"
        risk_text = "-" if risk_result is None else risk_result.overall_risk_level.upper()
        status_text = _build_scan_status_text(decision, risk_result)

        return [
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

    def _populate_scan_result_row(self, table: QTableWidget, row: int, result: Dict[str, Any]) -> None:
        values = self._build_scan_result_row_values(result, row)
        for col, value in enumerate(values):
            table.setItem(row, col, QTableWidgetItem(value))

    def _render_scan_result_table(self, table: QTableWidget, results: List[Dict[str, Any]]) -> None:
        table.setRowCount(0)
        for row, result in enumerate(list(results or [])):
            table.insertRow(row)
            self._populate_scan_result_row(table, row, result)

    def _on_scan_selection_changed(self):
        row = self.scan_table.currentRow()
        if row < 0 or row >= len(self._scan_results):
            return
        result = self._scan_results[row]
        self._display_result(result, switch_to_details=False, emit_decision=True)
        self.result_tabs.setCurrentWidget(self.decision_card_widget)

    def _on_scan_result_context_menu(self, table: QTableWidget, pos, results: List[Dict[str, Any]]) -> None:
        row = table.rowAt(pos.y())
        if row < 0 or row >= len(results or []):
            return
        result = dict(results[row] or {})
        code = str(result.get("symbol_code", "") or "").strip()
        name = str(result.get("symbol_name", "") or "").strip()
        if not code:
            return
        menu = QMenu(self)
        view_action = menu.addAction(f"查看K线 {name}({code})" if name else f"查看K线 {code}")
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        if chosen == view_action:
            self.market_view_requested.emit(code, name)

    @staticmethod
    def _deserialize_risk_result(payload: Dict[str, Any]) -> Optional[RiskCheckResult]:
        if not isinstance(payload, dict) or not payload:
            return None
        checks = []
        for item in list(payload.get("checks", []) or []):
            if not isinstance(item, dict):
                continue
            checks.append(
                RiskCheckItem(
                    name=str(item.get("name", "") or ""),
                    passed=bool(item.get("passed", False)),
                    level=str(item.get("level", "info") or "info"),
                    message=str(item.get("message", "") or ""),
                )
            )
        return RiskCheckResult(
            passed=bool(payload.get("passed", False)),
            checks=checks,
            overall_risk_level=str(payload.get("overall_risk_level", "low") or "low"),
            warnings=[str(item) for item in list(payload.get("warnings", []) or []) if str(item)],
            blocked_reasons=[str(item) for item in list(payload.get("blocked_reasons", []) or []) if str(item)],
        )

    def _build_runtime_scan_result_from_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        decision_payload = dict(record.get("decision", {}) or {})
        risk_payload = dict(record.get("risk_result", {}) or {})
        decision = TradeDecision.from_dict(decision_payload) if decision_payload else None
        if decision is not None:
            if not decision.symbol_code:
                decision.symbol_code = str(record.get("symbol_code", "") or "")
            if not decision.symbol_name:
                decision.symbol_name = str(record.get("symbol_name", "") or "")
        return {
            "symbol_code": str(record.get("symbol_code", "") or (decision.symbol_code if decision else "")),
            "symbol_name": str(record.get("symbol_name", "") or (decision.symbol_name if decision else "")),
            "decision": decision,
            "risk_result": self._deserialize_risk_result(risk_payload),
            "scan_item": dict(record.get("scan_item", {}) or {}),
            "response_text": str(record.get("response_text", "") or ""),
            "decision_record_id": str(record.get("decision_record_id", "") or ""),
        }

    def set_scheduled_scan_records(self, records: List[Dict[str, Any]], *, focus_latest: bool = False) -> None:
        self._scheduled_scan_records = list(records or [])
        self.scheduled_scan_batches_table.setRowCount(0)
        self._scheduled_scan_record_items = []
        self.scheduled_scan_detail_table.setRowCount(0)
        if not self._scheduled_scan_records:
            self.scheduled_scan_summary_label.setText("暂无定时巡检记录")
            self.scheduled_scan_detail_label.setText("选择一组定时巡检记录后，可在下方查看明细")
            return

        self.scheduled_scan_summary_label.setText(
            f"已记录 {len(self._scheduled_scan_records)} 组最近定时巡检结果，选择批次后可查看对应明细。"
        )
        for row, record in enumerate(self._scheduled_scan_records):
            self.scheduled_scan_batches_table.insertRow(row)
            values = [
                str(record.get("completed_at", "") or "-"),
                str(record.get("task_name", "") or "-"),
                str(record.get("scan_label", "") or "-"),
                str(int(record.get("scan_total", 0) or 0)),
                str(int(record.get("actionable", 0) or 0)),
                str(int(record.get("risk_blocked", 0) or 0)),
                str(record.get("summary_text", "") or "-"),
            ]
            for col, value in enumerate(values):
                self.scheduled_scan_batches_table.setItem(row, col, QTableWidgetItem(value))
        self.scheduled_scan_batches_table.selectRow(0)
        if focus_latest:
            self.result_tabs.setCurrentWidget(self.scheduled_scan_records_widget)

    def _on_scheduled_scan_batch_selection_changed(self) -> None:
        row = self.scheduled_scan_batches_table.currentRow()
        if row < 0 or row >= len(self._scheduled_scan_records):
            self._scheduled_scan_record_items = []
            self.scheduled_scan_detail_label.setText("选择一组定时巡检记录后，可在下方查看明细")
            self.scheduled_scan_detail_table.setRowCount(0)
            return
        record = self._scheduled_scan_records[row]
        self._scheduled_scan_record_items = [
            self._build_runtime_scan_result_from_record(item)
            for item in list(record.get("results", []) or [])
            if isinstance(item, dict)
        ]
        self.scheduled_scan_detail_label.setText(
            f"{record.get('scan_label', '定时巡检')} | 共 {len(self._scheduled_scan_record_items)} 只 | "
            f"完成于 {record.get('completed_at', '-')}"
        )
        self._render_scan_result_table(self.scheduled_scan_detail_table, self._scheduled_scan_record_items)
        if self._scheduled_scan_record_items:
            self.scheduled_scan_detail_table.selectRow(0)

    def _on_scheduled_scan_detail_selection_changed(self) -> None:
        row = self.scheduled_scan_detail_table.currentRow()
        if row < 0 or row >= len(self._scheduled_scan_record_items):
            return
        result = self._scheduled_scan_record_items[row]
        self._display_result(result, switch_to_details=False, emit_decision=False)

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
        menu = QMenu(self)
        view_action = menu.addAction(f"查看K线 {rec.symbol_name}({rec.symbol_code})")
        close_action = None
        if rec.outcome == DecisionOutcome.EXECUTED.value and not rec.closed_at:
            action = (rec.decision or {}).get("action", "")
            if action in ("buy", "add"):
                close_action = menu.addAction("📊 手动平仓（输入卖出价）")
        chosen = menu.exec(self.history_table.viewport().mapToGlobal(pos))
        if chosen == view_action:
            self.market_view_requested.emit(str(rec.symbol_code or ""), str(rec.symbol_name or ""))
            return
        if chosen != close_action or close_action is None:
            return
        action = (rec.decision or {}).get("action", "")
        if action not in ("buy", "add"):
            return

        from PyQt6.QtWidgets import QInputDialog
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
            from trading_app.services.ai_decision_notifier import notify_scan_complete
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

    def __init__(
        self,
        scheduler,
        parent=None,
        *,
        visible_task_ids: Optional[List[str]] = None,
        dialog_title: str = "定时任务设置",
    ):
        super().__init__(title=dialog_title, min_width=560, initial_height=500, parent=parent)
        self.scheduler = scheduler
        self.visible_task_ids = list(visible_task_ids or [])
        self._setup_ui()

    def _setup_ui(self):
        self.content_layout.addWidget(
            self.make_note_label(
                "说明：修改后点击底部“保存并关闭”生效。AI 任务会在设定时间触发巡检，并按各自的自动执行开关决定是否继续下单。"
            )
        )

        tasks = self.scheduler.get_tasks()
        if self.visible_task_ids:
            tasks = {tid: task for tid, task in tasks.items() if tid in self.visible_task_ids}
        self._rows: Dict[str, Dict[str, Any]] = {}

        for tid, task in tasks.items():
            grp = QGroupBox(f"调度任务：{task.name}")
            grp_layout = QFormLayout(grp)
            grp_layout.setSpacing(6)
            runtime_display = self.scheduler.get_task_runtime_display(tid)
            scan_only_task = str(getattr(task, "task_type", "") or "") == TASK_TYPE_UNMANAGED_POSITION_SCAN

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
            if scan_only_task:
                auto_execute_cb.setChecked(False)
                auto_execute_cb.setEnabled(False)
                auto_execute_cb.setToolTip("未管理账户巡检仅生成建议，不支持自动执行")
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
            from trading_app.services.ai_decision_scheduler import ScheduledAITask
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
                auto_execute=False if (old.task_type or "") == TASK_TYPE_UNMANAGED_POSITION_SCAN else widgets["auto_execute"].isChecked(),
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
    """Standalone configuration dialog for AI live decisions."""

    def __init__(self, account_panel: AccountPanel, parent=None):
        super().__init__(title="AI 实盘决策配置", min_width=760, initial_height=660, parent=parent)
        self.account_panel = account_panel
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

    market_view_requested = pyqtSignal(str, str)

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
        self.account_panel.configure_primary_actions(
            show_controls=True,
            show_candidate_pool=True,
            position_text="持仓巡检",
            candidate_text="候选池巡检",
        )
        self.account_panel.scheduler_settings_requested.connect(self._open_scheduler_settings)
        self.account_panel.primary_action_requested.connect(self._on_account_primary_action_requested)
        self.account_panel.model_select_requested.connect(self._on_account_model_select_requested)
        self.account_panel.manual_order_requested.connect(self._open_order_dialog)

        # Center: Decision panel
        self.decision_panel = DecisionPanel(context_provider=context_provider)
        self.decision_panel.setMinimumWidth(500)
        self.decision_panel.set_top_controls_visible(False)
        self.decision_panel.model_combo.currentTextChanged.connect(self.account_panel.set_current_model_display)
        self.decision_panel.market_view_requested.connect(self.market_view_requested)
        self.account_panel.set_current_model_display(self.decision_panel.get_current_model_name())

        # Detached: Order execution dialog panel
        self.order_panel = OrderExecutionPanel(
            strategy_context=self._build_strategy_context(),
            symbol_name_resolver=self.lookup_symbol_name,
        )
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
            from trading_app.services.ai_decision_scheduler import AIDecisionScheduler
            from trading_app.services.data_freshness_service import DataFreshnessGuard
            from trading_app.services.qmt_startup_orchestrator import QmtStartupOrchestrator
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
        self.freshness_guard.update_finished.connect(self._show_freshness_status)
        self.freshness_guard.result_signal.connect(self._on_freshness_result)
        self.freshness_guard.status_notice.connect(self._on_freshness_notice)
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
        self.strategy_trade_panel.market_view_requested.connect(self.market_view_requested)
        self.order_panel.order_executed.connect(self._on_order_executed)
        self._refresh_scheduler_status()
        self._refresh_scheduled_scan_records()

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
            decision_record_id = str(payload.get("decision_record_id", "") or "")
        else:
            decision = payload
            risk_result = None
            decision_record_id = ""
        if decision is None:
            return
        self.order_panel.fill_from_decision(
            decision,
            risk_result=risk_result,
            decision_record_id=decision_record_id,
        )
        self.statusBar().showMessage(
            f"决策: {TRADE_ACTION_LABELS.get(decision.action, decision.action)} "
            f"{decision.symbol_name} | 置信度 {decision.confidence:.0%}"
        )

    def _on_account_primary_action_requested(self, action_key: str) -> None:
        if action_key == "candidate_pool_scan":
            codes = self.decision_panel._collect_scan_codes_for_freshness("candidate_pool")
            idx = self.decision_panel.mode_combo.findData(DECISION_MODE_CANDIDATE_POOL_SCAN)
        else:
            codes = self.decision_panel._collect_scan_codes_for_freshness("position")
            idx = self.decision_panel.mode_combo.findData(DECISION_MODE_POSITION_SCAN)
        ok, reason = _check_ai_live_market_data_ready(codes)
        if not ok:
            message = f"实盘策略已阻断: {reason}"
            self.statusBar().showMessage(f"⛔ {message}")
            QMessageBox.warning(self, "行情数据未就绪", message)
            return
        if idx >= 0:
            self.decision_panel.mode_combo.setCurrentIndex(idx)
        self.decision_panel._on_analyze_clicked()

    def _on_account_model_select_requested(self) -> None:
        selected = self.decision_panel.prompt_select_model(self)
        if selected:
            self.account_panel.set_current_model_display(selected)

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
            decision_ctx = getattr(self.order_panel, "_decision_context", {}) or {}
            record_id = str(decision_ctx.get("decision_record_id", "") or "")
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
        task = self.scheduler.get_tasks().get("daily_ai_strategy_cycle")
        if task and bool(getattr(task, "enabled", False)):
            mode_label = "自动执行" if bool(getattr(task, "auto_execute", False)) else "仅检查"
            time_text = str(getattr(task, "time", "") or "").strip()
            summary = f"定时任务: {time_text} {mode_label}".strip()
            self.account_panel.set_scheduler_status(summary, "#16A34A")
        else:
            self.account_panel.set_scheduler_status("定时任务: 未启用", "#6B7B8D")

    def _refresh_scheduled_scan_records(self, *, focus_latest: bool = False) -> None:
        records: list[dict] = []
        task_id = "daily_ai_strategy_cycle"
        task = self.scheduler.get_tasks().get(task_id)
        latest_state = self.daily_auto_trade.get_latest_task_state(task_id)
        for record in list(latest_state.get("scheduled_scan_batches", []) or []):
            if not isinstance(record, dict):
                continue
            item = dict(record)
            item.setdefault("task_id", task_id)
            item.setdefault("task_name", str(getattr(task, "name", task_id) or task_id))
            records.append(item)
        records.sort(key=lambda item: str(item.get("completed_at", "") or ""), reverse=True)
        self.decision_panel.set_scheduled_scan_records(records, focus_latest=focus_latest)

    def _notify_unmanaged_panel_scan_records_updated(self, *, focus_latest: bool = False) -> None:
        parent = self.parent()
        while parent is not None:
            candidate = getattr(parent, "unmanaged_panel", None)
            if candidate is not None and hasattr(candidate, "_refresh_scheduled_scan_records"):
                try:
                    candidate._refresh_scheduled_scan_records(focus_latest=focus_latest)
                except Exception:
                    logger.debug("刷新未管理持仓定时记录面板失败", exc_info=True)
                return
            parent = parent.parent() if hasattr(parent, "parent") and callable(parent.parent) else None

    def _resolve_unmanaged_panel(self) -> Optional[QWidget]:
        parent = self.parent()
        while parent is not None:
            candidate = getattr(parent, "unmanaged_panel", None)
            if candidate is not None and hasattr(candidate, "decision_panel"):
                return candidate
            parent = parent.parent() if hasattr(parent, "parent") and callable(parent.parent) else None
        return None

    def _get_scheduled_target_decision_panel(self, task_type: str):
        if str(task_type or "") == TASK_TYPE_UNMANAGED_POSITION_SCAN:
            unmanaged_panel = self._resolve_unmanaged_panel()
            if unmanaged_panel is not None:
                decision_panel = getattr(unmanaged_panel, "decision_panel", None)
                if decision_panel is not None:
                    return decision_panel
        return self.decision_panel

    def _clear_scheduled_target_run_context(self, task_type: str) -> None:
        target_panel = self._get_scheduled_target_decision_panel(task_type)
        if hasattr(target_panel, "_clear_run_context_override"):
            target_panel._clear_run_context_override()

    def _refresh_scheduled_target_account_panel(self, task_type: str) -> None:
        if str(task_type or "") == TASK_TYPE_UNMANAGED_POSITION_SCAN:
            unmanaged_panel = self._resolve_unmanaged_panel()
            target_account_panel = getattr(unmanaged_panel, "account_panel", None) if unmanaged_panel is not None else None
            if target_account_panel is not None and hasattr(target_account_panel, "refresh"):
                QTimer.singleShot(200, target_account_panel.refresh)
                return
        QTimer.singleShot(200, self.account_panel.refresh)

    def _ensure_scheduled_target_signal_bridge(self, task_type: str) -> None:
        target_panel = self._get_scheduled_target_decision_panel(task_type)
        if target_panel is self.decision_panel:
            return
        if getattr(target_panel, "_scheduled_scan_bridge_connected", False):
            return
        target_panel.scan_completed.connect(self._on_scan_completed)
        setattr(target_panel, "_scheduled_scan_bridge_connected", True)

    def _append_scheduled_scan_batch(
        self,
        task_id: str,
        task_config: dict,
        payload: Dict[str, Any],
        *,
        focus_unmanaged_panel: bool = False,
    ) -> None:
        if not task_id:
            return
        latest_state = self.daily_auto_trade.get_task_state_for_day(task_id)
        batches = [
            dict(item)
            for item in list(latest_state.get("scheduled_scan_batches", []) or [])
            if isinstance(item, dict)
        ]
        record = _build_scheduled_scan_batch_record(
            task_id,
            str(task_config.get("name", task_id) or task_id),
            payload,
        )
        record_key = (
            str(record.get("scan_run_id", "") or ""),
            str(record.get("scan_label", "") or ""),
        )
        filtered = []
        for item in batches:
            item_key = (
                str(item.get("scan_run_id", "") or ""),
                str(item.get("scan_label", "") or ""),
            )
            if item_key == record_key and any(item_key):
                continue
            filtered.append(item)
        filtered.append(record)
        filtered.sort(key=lambda item: str(item.get("completed_at", "") or ""), reverse=True)
        filtered = filtered[:12]
        self.daily_auto_trade.update_task_state_for_day(
            task_id,
            scheduled_scan_batches=filtered,
            latest_scan_batch=record,
        )
        self._refresh_scheduled_scan_records()
        if task_id == "daily_unmanaged_position_scan":
            self._notify_unmanaged_panel_scan_records_updated(focus_latest=focus_unmanaged_panel)

    def _open_scheduler_settings(self):
        dlg = SchedulerSettingsDialog(
            self.scheduler,
            parent=self,
            visible_task_ids=["daily_ai_strategy_cycle"],
            dialog_title="AI实盘决策定时任务设置",
        )
        dlg.exec()
        self._refresh_scheduler_status()

    def _build_unmanaged_scan_bundle(self) -> tuple[list[dict], BrokerContext]:
        positions = self.account_panel.get_unmanaged_live_positions()
        broker_context = self.account_panel.get_unmanaged_broker_context(positions)
        return positions, broker_context

    def run_unmanaged_position_scan_now(self) -> str:
        self.decision_panel.mode_combo.setCurrentIndex(
            self.decision_panel.mode_combo.findData(DECISION_MODE_POSITION_SCAN)
        )
        model_cfg = self.decision_panel._resolve_model_config(show_dialog=True)
        if not model_cfg:
            return "未配置可用的 AI 模型"
        positions, broker_context = self._build_unmanaged_scan_bundle()
        if not positions:
            QMessageBox.information(self, "提示", "未管理账户当前无可巡检持仓")
            return "未管理账户当前无可巡检持仓"
        codes = [str(item.get("code", "") or "") for item in positions if item.get("code")]
        ok, reason = _check_ai_live_market_data_ready(codes)
        if not ok:
            message = f"实盘策略已阻断: {reason}"
            self.statusBar().showMessage(f"⛔ {message}")
            QMessageBox.warning(self, "行情数据未就绪", message)
            return message
        self.decision_panel._run_with_freshness_check(
            codes,
            lambda: self.decision_panel._start_position_scan(
                model_cfg,
                scan_source="manual",
                scheduled_task_id="",
                items=positions,
                scan_scope=SCAN_SCOPE_UNMANAGED,
                scan_label="未管理持仓巡检",
                allow_auto_execute=False,
                broker_context=broker_context,
            ),
        )
        return "已触发未管理持仓巡检"

    def _on_scheduled_task(self, task_id: str, task_config: dict):
        task_type = task_config.get("task_type", TASK_TYPE_AI_STRATEGY_CYCLE)
        target_decision_panel = self._get_scheduled_target_decision_panel(task_type)
        self._ensure_scheduled_target_signal_bridge(task_type)
        scheduled_run_context = build_decision_run_context(prefer_realtime=True)
        logger.info("AI交易中心收到定时任务: %s (%s)", task_id, task_type)
        self.statusBar().showMessage(f"⏰ 定时任务触发: {task_config.get('name', task_id)}，正在检查数据新鲜度...")
        target_decision_panel._set_run_context_override(scheduled_run_context)
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
            self._clear_scheduled_target_run_context(task_type)
            return

        logger.info("定时任务 %s 已进入自动任务编排", task_id)
        self.scheduler.mark_task_dispatch(task_id, "accepted", "定时任务已进入自动任务编排")
        if task_type == TASK_TYPE_AI_STRATEGY_CYCLE:
            target_decision_panel.mode_combo.setCurrentIndex(
                target_decision_panel.mode_combo.findData(DECISION_MODE_POSITION_SCAN)
            )
        elif task_type in (TASK_TYPE_POSITION_SCAN, TASK_TYPE_UNMANAGED_POSITION_SCAN):
            target_decision_panel.mode_combo.setCurrentIndex(
                target_decision_panel.mode_combo.findData(DECISION_MODE_POSITION_SCAN)
            )
        elif task_type == TASK_TYPE_CANDIDATE_POOL_SCAN:
            target_decision_panel.mode_combo.setCurrentIndex(
                target_decision_panel.mode_combo.findData(DECISION_MODE_CANDIDATE_POOL_SCAN)
            )

        model_name = task_config.get("model_name", "")
        if model_name:
            idx = target_decision_panel.model_combo.findText(model_name)
            if idx >= 0:
                target_decision_panel.model_combo.setCurrentIndex(idx)

        current_model = target_decision_panel.model_combo.currentText()
        logger.info("定时任务 %s 使用模型: %s", task_id, current_model)
        model_cfg = target_decision_panel._resolve_model_config(show_dialog=False)
        if not model_cfg:
            self._finish_pending_scheduled_task(task_id, False, f"未配置可用的 AI 模型: {current_model}")
            return

        cycle_plan = None
        if task_type == TASK_TYPE_AI_STRATEGY_CYCLE:
            cycle_plan = self._build_ai_strategy_cycle_plan()
            self._pending_scheduled_auto_task["cycle_plan"] = cycle_plan
            self._pending_scheduled_auto_task["cycle_results"] = []
            self._pending_scheduled_auto_task["cycle_index"] = 0
            self._pending_scheduled_auto_task["model_cfg"] = dict(model_cfg)

        codes = self._collect_codes_for_task(task_type, task_config, cycle_plan=cycle_plan)
        if not codes:
            self._finish_pending_scheduled_task(task_id, False, "当前任务没有可分析标的")
            return
        ok, reason = _check_ai_live_market_data_ready(codes)
        if not ok:
            self._finish_pending_scheduled_task(task_id, False, f"行情数据未就绪: {reason}")
            self.statusBar().showMessage(f"⛔ 实盘策略已阻断: {reason}")
            return
        logger.info("定时任务 %s 准备校验数据: %d 只标的", task_id, len(codes))
        self.freshness_guard.ensure_fresh_then_run(
            codes,
            lambda task_type=task_type, model_cfg=model_cfg, task_id=task_id: self._run_scheduled_analysis(task_id, task_type, model_cfg),
            include_indices=True,
            prefer_realtime=True,
            require_minute_freshness=False,
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
        self._append_scheduled_scan_batch(
            task_id,
            task_config,
            payload,
            focus_unmanaged_panel=(task_type == TASK_TYPE_UNMANAGED_POSITION_SCAN),
        )
        if task_type == TASK_TYPE_AI_STRATEGY_CYCLE:
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

        if task_type == TASK_TYPE_UNMANAGED_POSITION_SCAN:
            self._finish_scan_only_task(
                task_id,
                task_config,
                list(payload.get("results", []) or []),
                reason="未管理持仓巡检仅生成建议，不进入自动交易编排",
            )
            return

        self._pending_scheduled_auto_task = None
        self._clear_scheduled_target_run_context(task_type)
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
        pending = dict(self._pending_scheduled_auto_task or {})
        task_type = str(dict(pending.get("task_config", {}) or {}).get("task_type", "") or "")
        logger.info("定时任务 %s 结束: %s", task_id, message)
        self.daily_auto_trade.finish_task(task_id, success, message)
        self.scheduler.mark_task_result(task_id, message, dispatch_status="completed" if success else "failed")
        self.statusBar().showMessage(f"{'✅' if success else '❌'} {message}")
        self._pending_scheduled_auto_task = None
        self._clear_scheduled_target_run_context(task_type)

    def _finish_scan_only_task(
        self,
        task_id: str,
        task_config: dict,
        scan_results: list[dict],
        *,
        reason: str,
    ) -> None:
        task_type = str(task_config.get("task_type", "") or "")
        actionable = 0
        blocked = 0
        for item in scan_results:
            decision = item.get("decision")
            risk_result = item.get("risk_result")
            if decision is not None and getattr(decision, "is_actionable", False):
                actionable += 1
            if risk_result is not None and not getattr(risk_result, "passed", True):
                blocked += 1
        message = (
            f"{task_config.get('name', task_id)}完成，共 {len(scan_results)} 只，"
            f"可操作建议 {actionable} 只，风控拦截 {blocked} 只"
        )
        summary = {
            "planned": [],
            "executed": [],
            "skipped": True,
            "reason": reason,
            "scan_total": len(scan_results),
            "actionable": actionable,
            "risk_blocked": blocked,
        }
        logger.info("定时任务 %s 以 scan-only 方式结束: %s", task_id, message)
        self.daily_auto_trade.finish_task(task_id, True, message, summary=summary)
        self.scheduler.mark_task_result(task_id, message, dispatch_status="completed")
        self.statusBar().showMessage(f"✅ {message}")
        self._pending_scheduled_auto_task = None
        self._clear_scheduled_target_run_context(task_type)
        self._refresh_scheduled_target_account_panel(task_type)

    def _run_scheduled_analysis(self, task_id: str, task_type: str, model_cfg: dict):
        logger.info("定时任务 %s 通过数据校验，开始执行巡检", task_id)
        try:
            pending = dict(self._pending_scheduled_auto_task or {})
            target_decision_panel = self._get_scheduled_target_decision_panel(task_type)
            self._ensure_scheduled_target_signal_bridge(task_type)
            target_decision_panel._set_run_context_override(pending.get("run_context"))
            if task_type == TASK_TYPE_AI_STRATEGY_CYCLE:
                if self._pending_scheduled_auto_task is not None:
                    self._pending_scheduled_auto_task["model_cfg"] = dict(model_cfg)
                if self._start_next_ai_strategy_cycle_phase(task_id):
                    return
                self._finish_pending_scheduled_task(task_id, False, "每日AI实盘决策任务没有可执行的巡检阶段")
                return
            if task_type == TASK_TYPE_POSITION_SCAN:
                run_id = target_decision_panel._start_position_scan(
                    model_cfg,
                    scan_source="scheduled",
                    scheduled_task_id=task_id,
                )
                if not run_id:
                    self._finish_pending_scheduled_task(task_id, False, "持仓巡检未能启动，可能仍有其他扫描在运行")
                    return
                if self._pending_scheduled_auto_task is not None:
                    self._pending_scheduled_auto_task["expected_scan_run_id"] = run_id
                    self._pending_scheduled_auto_task["expected_scan_mode"] = DECISION_MODE_POSITION_SCAN
                return
            if task_type == TASK_TYPE_UNMANAGED_POSITION_SCAN:
                positions, broker_context = self._build_unmanaged_scan_bundle()
                run_id = target_decision_panel._start_position_scan(
                    model_cfg,
                    scan_source="scheduled",
                    scheduled_task_id=task_id,
                    items=positions,
                    scan_scope=SCAN_SCOPE_UNMANAGED,
                    scan_label="未管理持仓巡检",
                    allow_auto_execute=False,
                    broker_context=broker_context,
                )
                if not run_id:
                    self._finish_pending_scheduled_task(task_id, False, "未管理持仓巡检未能启动，可能仍有其他扫描在运行")
                    return
                if self._pending_scheduled_auto_task is not None:
                    self._pending_scheduled_auto_task["expected_scan_run_id"] = run_id
                    self._pending_scheduled_auto_task["expected_scan_mode"] = DECISION_MODE_POSITION_SCAN
                return
            if task_type == TASK_TYPE_CANDIDATE_POOL_SCAN:
                items = target_decision_panel._load_candidate_pool_items(refresh=True)
                run_id = target_decision_panel._start_candidate_pool_scan(
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
        if task_type == TASK_TYPE_AI_STRATEGY_CYCLE:
            codes = list((cycle_plan or {}).get("codes", []) or [])
        elif task_type == TASK_TYPE_POSITION_SCAN:
            try:
                positions = self.account_panel.get_live_positions()
                codes = [str(p.get("code", "")) for p in positions if p.get("code")]
            except Exception:
                pass
        elif task_type == TASK_TYPE_UNMANAGED_POSITION_SCAN:
            try:
                positions = self.account_panel.get_unmanaged_live_positions()
                codes = [str(p.get("code", "")) for p in positions if p.get("code")]
            except Exception:
                pass
        elif task_type == TASK_TYPE_CANDIDATE_POOL_SCAN:
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
            "可能原因包括：实时行情未刷新、盘口不可用、日线拉取失败，或 miniQMT 会话异常。\n\n"
            "请执行以下操作：\n"
            "1. 先确认 miniQMT 已登录且行情在刷新\n"
            "2. 若实时行情/盘口长期无更新，再完全关闭并重启 miniQMT\n"
            "3. 等待行情连接就绪后重试\n\n"
            "本次定时任务将跳过，数据可能不是最新。",
        )

    def _show_freshness_status(self, ok: bool, message: str):
        prefix = "✅" if ok else "❌"
        if ok and ("告警" in message or "有告警" in message):
            prefix = "⚠"
        self.statusBar().showMessage(f"{prefix} {message}")

    def _on_freshness_result(self, result: object):
        if not isinstance(result, DataUpdateResult):
            return
        prefix = "✅" if result.ok else "❌"
        if result.ok and result.has_failures:
            prefix = "⚠"
        self.statusBar().showMessage(f"{prefix} {result.to_ui_message()}")

    def _on_freshness_notice(self, level: str, message: str):
        prefix_map = {
            "info": "📡",
            "success": "✅",
            "warning": "⚠",
            "error": "❌",
        }
        self.statusBar().showMessage(f"{prefix_map.get(level, '📡')} {message}")

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

    @staticmethod
    def _normalize_symbol_code(code: str) -> str:
        return str(code or "").split(".")[0].strip().upper()

    def get_center_status_summary(self) -> dict:
        runtime_display = self.scheduler.get_task_runtime_display("daily_ai_strategy_cycle")
        ai_task = self.scheduler.get_tasks().get("daily_ai_strategy_cycle")
        return {
            "strategy_id": AI_STOCK_STRATEGY_ID,
            "strategy_name": AI_STOCK_STRATEGY_NAME,
            "scheduler_enabled_count": 1 if bool(ai_task and getattr(ai_task, "enabled", False)) else 0,
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

    def get_center_task_summary(self, task_id: str) -> dict:
        for item in self.get_center_task_summaries():
            if str(item.get("task_key", "") or "") == str(task_id or ""):
                return item
        return {}

    def generate_live_signals(self, payload: Optional[dict] = None) -> list[StrategySignal]:
        """Expose latest AI stock decisions as unified live strategy signals."""
        payload = dict(payload or {})
        raw_results = payload.get("results")
        if raw_results is None:
            raw_results = list(getattr(self.decision_panel, "_scan_results", []) or [])
        signals: list[StrategySignal] = []
        seen_ids: set[str] = set()
        for result in list(raw_results or []):
            signal = self._build_signal_from_scan_result(dict(result or {}), payload=payload)
            if signal is None:
                continue
            if signal.signal_id in seen_ids:
                continue
            seen_ids.add(signal.signal_id)
            signals.append(signal)
        if signals:
            return signals
        decision = getattr(self.decision_panel, "_current_decision", None)
        risk_result = getattr(self.decision_panel, "_current_risk_result", None)
        current_signal = self._build_signal_from_decision(
            decision,
            risk_result=risk_result,
            decision_record_id="",
            payload=payload,
        )
        return [current_signal] if current_signal is not None else []

    def execute_live_signals(
        self,
        signals: list[StrategySignal],
        *,
        execution_service=None,
        stock_name_map: Optional[dict[str, str]] = None,
    ) -> list[OrderExecutionReport]:
        """Execute AI stock StrategySignal outputs and update decision tracking."""
        service = execution_service or get_trade_execution_service()
        reports: list[OrderExecutionReport] = []
        names = dict(stock_name_map or {})
        for signal in list(signals or []):
            if signal is None or signal.action == "hold":
                continue
            quantity = int(signal.metadata.get("quantity", 0) or signal.target_quantity or 0)
            if quantity <= 0:
                continue
            price = float(signal.price or 0.0)
            if price <= 0:
                continue
            plain_code = self._normalize_symbol_code(signal.symbol)
            stock_name = names.get(plain_code) or names.get(signal.symbol) or self.lookup_symbol_name(plain_code) or plain_code
            decision = self._decision_from_signal(signal, stock_name=stock_name)
            risk_result = self._risk_from_signal(signal)
            decision_record_id = str(signal.metadata.get("decision_record_id", "") or "")
            result = service.execute(
                ExecutionRequest(
                    stock_code=plain_code,
                    stock_name=stock_name,
                    order_type=23 if signal.action == "buy" else 24,
                    order_volume=quantity,
                    price_type=int(signal.metadata.get("price_type", 5) or 5),
                    price=price,
                    source=TradeSource.AI_AGENT.value,
                    trigger=str(signal.metadata.get("trigger", "strategy_center") or "strategy_center"),
                    strategy_name=AI_STOCK_STRATEGY_NAME,
                    strategy_id=AI_STOCK_STRATEGY_ID,
                    virtual_account_id=AI_STOCK_VIRTUAL_ACCOUNT_ID,
                    intent_id=signal.signal_id or decision_record_id,
                    remark=signal.reason or "AI股票策略输出",
                    decision=decision,
                    risk_result=risk_result,
                    decision_record_id=decision_record_id,
                    require_approval=False,
                    approved=True,
                    metadata=dict(signal.metadata or {}),
                )
            )
            report = OrderExecutionReport.from_live_execution_result(result, intent=None, fills=[])
            reports.append(report)
            self._sync_signal_execution_outcome(signal, result, decision)
        if reports:
            self.strategy_trade_panel.refresh_all()
            QTimer.singleShot(200, self.account_panel.refresh)
            self.decision_panel._refresh_history()
        return reports

    def _build_signal_from_scan_result(self, result: dict, *, payload: dict) -> Optional[StrategySignal]:
        return self._build_signal_from_decision(
            result.get("decision"),
            risk_result=result.get("risk_result"),
            decision_record_id=str(result.get("decision_record_id", "") or ""),
            payload=payload,
            scan_result=result,
        )

    def _build_signal_from_decision(
        self,
        decision: Optional[TradeDecision],
        *,
        risk_result: Optional[RiskCheckResult] = None,
        decision_record_id: str = "",
        payload: Optional[dict] = None,
        scan_result: Optional[dict] = None,
    ) -> Optional[StrategySignal]:
        if decision is None or not getattr(decision, "is_actionable", False):
            return None
        if risk_result is not None and not bool(getattr(risk_result, "passed", False)):
            return None
        code = self._normalize_symbol_code(getattr(decision, "symbol_code", "") or "")
        price = float(getattr(decision, "current_price", 0.0) or 0.0)
        if not code or price <= 0:
            return None
        action = str(getattr(decision, "action", "") or "").lower().strip()
        side = "buy" if action in (TradeAction.BUY.value, TradeAction.ADD.value) else "sell"
        quantity = get_trade_execution_service().estimate_volume_for_decision(decision)
        quantity = max(int(quantity or 0), 0)
        if quantity <= 0:
            return None
        signal_id = decision_record_id or f"ai_{code}_{uuid4().hex[:10]}"
        payload = dict(payload or {})
        scan_result = dict(scan_result or {})
        metadata = {
            "source": TradeSource.AI_AGENT.value,
            "trigger": str(payload.get("trigger", "strategy_center") or "strategy_center"),
            "virtual_account_id": AI_STOCK_VIRTUAL_ACCOUNT_ID,
            "quantity": quantity,
            "quantity_mode": "delta",
            "price_type": 5,
            "decision_record_id": decision_record_id,
            "decision": decision.to_dict(),
            "risk_result": risk_result.to_dict() if risk_result is not None else {},
            "scan_scope": str(scan_result.get("scan_scope", "") or payload.get("scan_scope", "") or ""),
            "scan_run_id": str(payload.get("scan_run_id", "") or scan_result.get("scan_run_id", "") or ""),
            "scheduled_task_id": str(payload.get("scheduled_task_id", "") or scan_result.get("scheduled_task_id", "") or ""),
        }
        return StrategySignal(
            symbol=code,
            action=side,
            signal_id=signal_id,
            strategy_id=AI_STOCK_STRATEGY_ID,
            strategy_name=AI_STOCK_STRATEGY_NAME,
            strength=float(getattr(decision, "confidence", 0.0) or 0.0),
            price=price,
            reason=getattr(decision, "reasoning", "") or getattr(decision, "invalidation", "") or "AI股票策略输出",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            metadata=metadata,
        )

    @staticmethod
    def _decision_from_signal(signal: StrategySignal, *, stock_name: str = "") -> Optional[TradeDecision]:
        payload = dict(signal.metadata.get("decision", {}) or {})
        if payload:
            return TradeDecision.from_dict(payload)
        return TradeDecision(
            action=TradeAction.BUY.value if signal.action == "buy" else TradeAction.SELL.value,
            symbol_code=signal.symbol,
            symbol_name=stock_name or signal.symbol,
            confidence=float(signal.strength or 0.0),
            current_price=float(signal.price or 0.0),
            reasoning=signal.reason,
        )

    @staticmethod
    def _risk_from_signal(signal: StrategySignal) -> Optional[RiskCheckResult]:
        payload = dict(signal.metadata.get("risk_result", {}) or {})
        if not payload:
            return None
        try:
            return RiskCheckResult(
                passed=bool(payload.get("passed", False)),
                overall_risk_level=str(payload.get("overall_risk_level", "low") or "low"),
                warnings=list(payload.get("warnings", []) or []),
                blocked_reasons=list(payload.get("blocked_reasons", []) or []),
                checks=[RiskCheckItem(**dict(item or {})) for item in list(payload.get("checks", []) or [])],
            )
        except Exception:
            return None

    def _sync_signal_execution_outcome(self, signal: StrategySignal, result, decision: Optional[TradeDecision]) -> None:
        record_id = str(signal.metadata.get("decision_record_id", "") or "")
        if not record_id:
            return
        tracker = self.decision_panel.decision_tracker
        if result.success and result.broker_order_id > 0:
            tracker.update_outcome(record_id, broker_order_id=result.broker_order_id)
        if result.success and result.filled_confirmed:
            tracker.update_outcome(
                record_id,
                outcome=DecisionOutcome.EXECUTED.value,
                broker_order_id=result.broker_order_id,
            )
            if decision is not None and decision.action in (TradeAction.SELL.value, TradeAction.REDUCE.value):
                tracker.auto_close_by_symbol(
                    decision.symbol_code,
                    float(signal.price or getattr(decision, "current_price", 0.0) or 0.0),
                    broker_order_id=result.broker_order_id,
                )
        elif not result.success:
            tracker.update_outcome(record_id, outcome=DecisionOutcome.EXECUTION_FAILED.value)

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


class UnmanagedPositionPanel(QWidget):
    """Embeddable panel for unmanaged holdings review."""

    market_view_requested = pyqtSignal(str, str)

    def __init__(
        self,
        context_provider=None,
        parent=None,
        *,
        symbol_name_resolver: Optional[Callable[[str], str]] = None,
        name_map: Optional[Dict[str, str]] = None,
        etf_name_map: Optional[Dict[str, str]] = None,
        shared_broker_panel=None,
    ):
        super().__init__(parent)
        self.context_provider = context_provider
        self.symbol_name_resolver = symbol_name_resolver
        self.name_map = dict(name_map or {})
        self.etf_name_map = dict(etf_name_map or {})
        self.shared_broker_panel = shared_broker_panel
        self._status_proxy = _StatusMessageProxy(self)
        self.order_panel = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)

        self.account_panel = AccountPanel(
            show_connection_panel=self.shared_broker_panel is None,
            shared_broker_panel=self.shared_broker_panel,
        )
        self.account_panel.configure_scope(
            position_scope=SCAN_SCOPE_UNMANAGED,
            asset_group_title="账户概览（未管理账户）",
            show_scheduler_controls=True,
            show_config_controls=False,
            show_manual_order_controls=True,
        )
        self.account_panel.setMinimumWidth(260)
        self.account_panel.setMaximumWidth(360)
        self.account_panel.configure_primary_actions(
            show_controls=True,
            show_candidate_pool=False,
            position_text="持仓巡检",
        )
        self.account_panel.scheduler_settings_requested.connect(self._open_scheduler_settings)
        self.account_panel.primary_action_requested.connect(self._on_account_primary_action_requested)
        self.account_panel.model_select_requested.connect(self._on_account_model_select_requested)
        self.account_panel.manual_order_requested.connect(self._open_order_dialog)

        self.decision_panel = DecisionPanel(
            context_provider=context_provider,
            allow_candidate_pool_scan=False,
            position_scan_label="未管理持仓巡检",
            position_scan_hint="未管理持仓巡检: 自动读取未管理账户当前持仓，逐只生成持有/加仓/减仓/卖出建议",
        )
        self.decision_panel.setMinimumWidth(500)
        self.decision_panel.set_top_controls_visible(False)
        self.decision_panel.model_combo.currentTextChanged.connect(self.account_panel.set_current_model_display)
        self.decision_panel.market_view_requested.connect(self.market_view_requested)
        self.account_panel.set_current_model_display(self.decision_panel.get_current_model_name())

        self.order_panel = OrderExecutionPanel(
            strategy_context=self._build_strategy_context(),
            symbol_name_resolver=self.lookup_symbol_name,
        )
        self.order_dialog = QDialog(self)
        self.order_dialog.setWindowTitle("手动委托")
        self.order_dialog.resize(560, 680)
        order_dialog_layout = QVBoxLayout(self.order_dialog)
        order_dialog_layout.setContentsMargins(8, 8, 8, 8)
        order_dialog_layout.addWidget(self.order_panel)

        self.shell = LiveStrategyShell(
            self._build_strategy_context(),
            self.account_panel,
            self.decision_panel,
            parent=self,
        )
        self.shell.horizontal_splitter.setSizes([320, 980])
        self.strategy_trade_panel = self.shell.strategy_trade_panel
        main_layout.addWidget(self.shell)
        self.decision_panel.decision_ready.connect(self._on_decision_ready)
        self.strategy_trade_panel.order_requested.connect(self._open_order_dialog_with_order)
        self.strategy_trade_panel.market_view_requested.connect(self.market_view_requested)
        self.order_panel.order_executed.connect(self._on_order_executed)
        self._refresh_scheduler_status()
        self._refresh_scheduled_scan_records(focus_latest=True)
        self.statusBar().showMessage("就绪")

    def _build_strategy_context(self) -> StrategyPanelContext:
        return StrategyPanelContext(
            strategy_id=UNMANAGED_STRATEGY_ID,
            strategy_name=UNMANAGED_STRATEGY_NAME,
            virtual_account_id=UNMANAGED_VIRTUAL_ACCOUNT_ID,
            owner_type="unmanaged",
        )

    def statusBar(self):
        window = self.window()
        if isinstance(window, QMainWindow):
            return window.statusBar()
        return self._status_proxy

    def set_symbol(self, code: str, name: str = ""):
        self.decision_panel.set_symbol(code, name)

    def _resolve_shared_ai_panel(self) -> Optional[QWidget]:
        parent = self.parent()
        while parent is not None:
            candidate = getattr(parent, "ai_panel", None)
            if candidate is not None and hasattr(candidate, "scheduler"):
                return candidate
            parent = parent.parent() if hasattr(parent, "parent") and callable(parent.parent) else None
        return None

    @property
    def freshness_guard(self):
        ai_panel = self._resolve_shared_ai_panel()
        if ai_panel is not None:
            return getattr(ai_panel, "freshness_guard", None)
        return None

    def _refresh_scheduler_status(self) -> None:
        ai_panel = self._resolve_shared_ai_panel()
        if ai_panel is None:
            self.account_panel.set_scheduler_status("定时任务: 未接入", "#6B7B8D")
            return
        task = ai_panel.scheduler.get_tasks().get("daily_unmanaged_position_scan")
        if task and bool(getattr(task, "enabled", False)):
            time_text = str(getattr(task, "time", "") or "").strip()
            self.account_panel.set_scheduler_status(f"定时任务: {time_text} 仅检查", "#16A34A")
            return
        self.account_panel.set_scheduler_status("定时任务: 未启用", "#6B7B8D")

    def _refresh_scheduled_scan_records(self, *, focus_latest: bool = False) -> None:
        latest_state = get_daily_auto_trade_service().get_latest_task_state("daily_unmanaged_position_scan")
        records = []
        for record in list(latest_state.get("scheduled_scan_batches", []) or []):
            if not isinstance(record, dict):
                continue
            item = dict(record)
            item.setdefault("task_id", "daily_unmanaged_position_scan")
            item.setdefault("task_name", "未管理持仓AI巡检")
            records.append(item)
        records.sort(key=lambda item: str(item.get("completed_at", "") or ""), reverse=True)
        self.decision_panel.set_scheduled_scan_records(records, focus_latest=focus_latest)

    def _open_scheduler_settings(self) -> None:
        ai_panel = self._resolve_shared_ai_panel()
        if ai_panel is None:
            QMessageBox.information(self, "提示", "当前未接入 AI 调度中心，无法打开定时任务设置")
            return
        dlg = SchedulerSettingsDialog(
            ai_panel.scheduler,
            parent=self,
            visible_task_ids=["daily_unmanaged_position_scan"],
            dialog_title="未管理持仓定时任务设置",
        )
        dlg.exec()
        try:
            ai_panel._refresh_scheduler_status()
        except Exception:
            pass
        self._refresh_scheduler_status()

    def _on_decision_ready(self, payload: object):
        if isinstance(payload, dict):
            decision = payload.get("decision")
            risk_result = payload.get("risk_result")
            decision_record_id = str(payload.get("decision_record_id", "") or "")
        else:
            decision = payload
            risk_result = None
            decision_record_id = ""
        if decision is None:
            return
        self.order_panel.fill_from_decision(
            decision,
            risk_result=risk_result,
            decision_record_id=decision_record_id,
        )
        self.statusBar().showMessage(
            f"决策: {TRADE_ACTION_LABELS.get(decision.action, decision.action)} "
            f"{decision.symbol_name} | 置信度 {decision.confidence:.0%}"
        )

    def _on_account_primary_action_requested(self, action_key: str) -> None:
        codes = self.decision_panel._collect_scan_codes_for_freshness("position")
        ok, reason = _check_ai_live_market_data_ready(codes)
        if not ok:
            message = f"实盘策略已阻断: {reason}"
            self.statusBar().showMessage(f"⛔ {message}")
            QMessageBox.warning(self, "行情数据未就绪", message)
            return
        idx = self.decision_panel.mode_combo.findData(DECISION_MODE_POSITION_SCAN)
        if idx >= 0:
            self.decision_panel.mode_combo.setCurrentIndex(idx)
        self.decision_panel._on_analyze_clicked()

    def _on_account_model_select_requested(self) -> None:
        selected = self.decision_panel.prompt_select_model(self)
        if selected:
            self.account_panel.set_current_model_display(selected)

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
        self.account_panel.refresh()
        if success:
            prefix = "✅" if filled_confirmed else "⏳"
            self.statusBar().showMessage(f"{prefix} {message}")
        else:
            self.statusBar().showMessage(f"❌ {message}")
        QMessageBox.information(self, "下单结果", message)

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
