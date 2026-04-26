from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QEvent, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFontMetrics, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from common.broker_connection_panel import BrokerConnectionPanel
from trading_app.services.auto_trade_config_service import get_auto_trade_config_service
from trading_app.services.live_strategy_end_of_day_service import LiveStrategyEndOfDayService
from trading_app.services.live_strategy_logging import get_live_strategy_log_path
from trading_app.services.live_strategy_center import (
    AlertEventService,
    HubStateService,
    LiveStrategyHubController,
    LiveStrategyPlugin,
    LiveStrategyPluginRegistry,
    LiveStrategyPortfolioService,
    LiveStrategyTaskSpec,
    PanelLiveStrategyAdapter,
    TaskOrchestratorService,
    get_live_strategy_center_storage,
)
from trading_app.services.live_strategy_center.builtin_portfolio_plugins import (
    create_ai_stock_portfolio_provider,
    create_etf_rotation_portfolio_provider,
)
from trading_app.services.live_strategy_center.builtin_unmanaged_plugin import (
    create_unmanaged_position_review_plugin,
)
from trading_app.services.qmt_startup_orchestrator import QmtStartupOrchestrator
from trading_app.services.strategy_constants import AI_STOCK_STRATEGY_ID
from trading_app.widgets.live_strategy_account_settings_dialog import LiveStrategyAccountSettingsDialog
from trading_app.widgets.live_strategy_alert_center_widget import LiveStrategyAlertCenterWidget
from trading_app.widgets.live_strategy_exception_order_widget import LiveStrategyExceptionOrderWidget
from trading_app.widgets.live_log_viewer_widget import LiveLogViewerWidget
from trading_app.widgets.live_strategy_performance_widget import LiveStrategyPerformanceWidget
from trading_app.widgets.live_strategy_status_bar_widget import LiveStrategyStatusBarWidget
from trading_app.widgets.live_strategy_task_center_widget import LiveStrategyTaskCenterWidget
from trading_app.widgets.ai_trade_decision_widget import AITradeDecisionPanel, UnmanagedPositionPanel
from live_rotation.widget import ETFRotationLiveWidget
from live_rotation.holiday_calendar import is_trading_day


class _OverviewCard(QFrame):
    """Small reusable card used by the hub overview dashboard."""

    def __init__(self, title: str, action_text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            "QFrame { background:#1f1f1f; border:1px solid #3a3a3a; border-radius:8px; }"
            "QLabel { border:none; background:transparent; }"
            "QPushButton { background:#0078d4; color:#ffffff; border:none; border-radius:4px; padding:6px 12px; }"
            "QPushButton:hover { background:#1688dd; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight:bold;font-size:14px;color:#f3f4f6;")
        layout.addWidget(self.title_label)

        self.body_label = QLabel("-")
        self.body_label.setWordWrap(True)
        self.body_label.setStyleSheet("color:#d1d5db;font-size:12px;line-height:150%;")
        layout.addWidget(self.body_label, 1)

        self.action_btn = QPushButton(action_text)
        self.action_btn.setVisible(bool(action_text))
        self.action_btn.setFixedHeight(32)
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.action_btn, 0, Qt.AlignmentFlag.AlignRight)

    def set_body(self, lines: list[str] | tuple[str, ...] | str) -> None:
        if isinstance(lines, str):
            text = lines
        else:
            text = "\n".join(str(item) for item in lines if str(item or "").strip())
        self.body_label.setText(text or "-")


class _LiveStrategyOverviewWidget(QWidget):
    """Dashboard page for the live strategy center."""

    navigate_requested = pyqtSignal(str)
    account_settings_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        header = QLabel("实盘策略中心总览")
        header.setStyleSheet("font-size:18px;font-weight:bold;color:#f3f4f6;")
        outer.addWidget(header)

        hint = QLabel("从这里快速确认连接、策略、风险、任务、收益和日终状态。")
        hint.setStyleSheet("color:#9ca3af;font-size:12px;")
        outer.addWidget(hint)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        outer.addLayout(grid, 1)

        self.connectivity_card = _OverviewCard("连接与执行", "账户设置", self)
        self.connectivity_card.action_btn.clicked.connect(self.account_settings_requested.emit)
        grid.addWidget(self.connectivity_card, 0, 0)

        self.strategy_card = _OverviewCard("策略运行", "查看策略", self)
        self.strategy_card.action_btn.clicked.connect(lambda: self.navigate_requested.emit("ai"))
        grid.addWidget(self.strategy_card, 0, 1)

        self.risk_card = _OverviewCard("持仓与风险", "处理风险", self)
        self.risk_card.action_btn.clicked.connect(lambda: self.navigate_requested.emit("unmanaged"))
        grid.addWidget(self.risk_card, 1, 0)

        self.task_card = _OverviewCard("任务调度", "查看任务", self)
        self.task_card.action_btn.clicked.connect(lambda: self.navigate_requested.emit("tasks"))
        grid.addWidget(self.task_card, 1, 1)

        self.performance_card = _OverviewCard("收益绩效", "查看收益", self)
        self.performance_card.action_btn.clicked.connect(lambda: self.navigate_requested.emit("performance"))
        grid.addWidget(self.performance_card, 2, 0)

        self.log_card = _OverviewCard("运行与日终", "查看日志", self)
        self.log_card.action_btn.clicked.connect(lambda: self.navigate_requested.emit("logs"))
        grid.addWidget(self.log_card, 2, 1)

        for column in range(2):
            grid.setColumnStretch(column, 1)
        outer.addStretch(0)

    def refresh_view(self, state: dict) -> None:
        state = dict(state or {})
        self._refresh_connectivity(state)
        self._refresh_strategies(state)
        self._refresh_risk(state)
        self._refresh_tasks(state)
        self._refresh_performance(state)
        self._refresh_runtime(state)

    def _refresh_connectivity(self, state: dict) -> None:
        broker_connected = bool(state.get("broker_connected", False))
        qmt_running = bool(state.get("qmt_running", False))
        startup_running = bool(state.get("startup_running", False))
        mode = str(state.get("auto_trade_mode", "off") or "off")
        manual_enabled = bool(state.get("manual_orders_enabled", True))
        require_trading_time = bool(state.get("require_trading_time", True))
        self.connectivity_card.set_body([
            f"券商连接：{'已连接' if broker_connected else '未连接'}",
            f"QMT状态：{'运行中' if qmt_running else '未就绪'}",
            f"启动自检：{'进行中' if startup_running else '空闲'}",
            f"统一执行模式：{mode}",
            f"手动委托：{'开启' if manual_enabled else '关闭'}",
            f"交易时段闸：{'开启' if require_trading_time else '关闭'}",
        ])

    def _refresh_strategies(self, state: dict) -> None:
        rows = list(state.get("strategy_statuses", []) or [])
        lines: list[str] = []
        for item in rows:
            row = dict(item or {})
            name = str(row.get("strategy_name") or row.get("strategy_id") or "未命名策略")
            paused = bool(row.get("automation_paused", False))
            status = str(row.get("status") or row.get("state") or "运行中")
            lines.append(f"{name}：{'已暂停' if paused else status}")
        if not lines:
            lines.append("暂无策略状态")
        self.strategy_card.set_body(lines)

    def _refresh_risk(self, state: dict) -> None:
        risk_summary = dict(state.get("risk_summary", {}) or {})
        alert_counts = dict(state.get("alert_counts", {}) or {})
        open_alerts = int(alert_counts.get("open", 0) or 0)
        exception_count = int(state.get("exception_order_count", 0) or 0)
        items = list(risk_summary.get("items", []) or [])
        lines = [
            str(risk_summary.get("label", "风控: -") or "风控: -"),
            f"未处理告警：{open_alerts}",
            f"异常订单：{exception_count}",
        ]
        lines.extend(str(item) for item in items[:4])
        self.risk_card.set_body(lines)

    def _refresh_tasks(self, state: dict) -> None:
        tasks = list(state.get("tasks", []) or [])
        counts: dict[str, int] = {}
        for item in tasks:
            row = dict(item or {})
            status = str(row.get("status", "unknown") or "unknown").strip().lower()
            counts[status] = counts.get(status, 0) + 1
        running = counts.get("running", 0)
        failed = counts.get("failed", 0)
        completed = counts.get("completed", 0) + counts.get("success", 0)
        pending = max(len(tasks) - running - failed - completed, 0)
        self.task_card.set_body([
            f"任务总数：{len(tasks)}",
            f"运行中：{running}",
            f"待处理：{pending}",
            f"已完成：{completed}",
            f"失败：{failed}",
        ])

    def _refresh_performance(self, state: dict) -> None:
        self.performance_card.set_body([
            "收益中心已接入策略账户、持仓行和日终快照。",
            "点击查看 AI、ETF 与未管理账户的收益归属。",
            f"最近状态更新时间：{state.get('updated_at', '-')}",
        ])

    def _refresh_runtime(self, state: dict) -> None:
        eod_state = dict(state.get("eod_state", {}) or {})
        eod_status = str(eod_state.get("status", "idle") or "idle")
        eod_error = str(eod_state.get("last_error", "") or "")
        center_paused = bool(state.get("center_automation_paused", False))
        lines = [
            f"今日日终：{eod_status}",
            f"中心自动化：{'已暂停' if center_paused else '正常'}",
            f"最近状态更新时间：{state.get('updated_at', '-')}",
        ]
        if eod_error:
            lines.append(f"日终错误：{eod_error}")
        self.log_card.set_body(lines)


class _EndOfDayWorker(QThread):
    finished_cycle = pyqtSignal(str, bool, str, object)
    failed_cycle = pyqtSignal(str, str)

    def __init__(self, service: LiveStrategyEndOfDayService, mode: str, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.mode = mode

    def run(self) -> None:
        try:
            if self.mode == "manual":
                success, message, payload = self.service.run_manual_cycle()
            elif self.mode == "catchup":
                success, message = self.service.run_catchup_if_needed()
                payload = {}
            else:
                raise ValueError(f"unsupported end-of-day mode: {self.mode}")
            self.finished_cycle.emit(self.mode, success, message, payload)
        except Exception as exc:
            self.failed_cycle.emit(self.mode, str(exc))


class LiveStrategyHubWidget(QWidget):
    """Unified live strategy workspace with grouped navigation."""

    TAB_OVERVIEW = "overview"
    TAB_ALERTS = "alerts"
    TAB_TASKS = "tasks"
    TAB_EXCEPTIONS = "exceptions"
    TAB_PERFORMANCE = "performance"
    TAB_AI = "ai"
    TAB_UNMANAGED = "unmanaged"
    TAB_ETF = "etf"
    TAB_LOGS = "logs"
    status_changed = pyqtSignal(str)

    def __init__(
        self,
        parent=None,
        *,
        context_provider=None,
        symbol_name_resolver: Optional[Callable[[str], str]] = None,
        name_map: Optional[Dict[str, str]] = None,
        etf_name_map: Optional[Dict[str, str]] = None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.broker_panel = BrokerConnectionPanel(self)

        eod_bar = QWidget(self)
        eod_layout = QHBoxLayout(eod_bar)
        eod_layout.setContentsMargins(0, 0, 0, 0)
        eod_layout.setSpacing(4)
        self.eod_status_label = QLabel("")
        self.eod_status_label.setFixedWidth(160)
        self.eod_status_label.setWordWrap(False)
        self.eod_status_label.setStyleSheet("color:#888;font-size:12px;")
        eod_layout.addWidget(self.eod_status_label, 1)
        self.run_eod_btn = QPushButton("执行日终")
        self.run_eod_btn.setFixedHeight(32)
        self.run_eod_btn.clicked.connect(self._run_end_of_day_cycle)
        eod_layout.addWidget(self.run_eod_btn)
        self._set_eod_text("日终: 待命")
        self.broker_panel.set_trailing_widget(eod_bar)
        # 状态栏接管展示后，原先的券商面板/日终按钮行不再上屏，但内部对象仍需保留
        # （broker 服务、eod_status_label、run_eod_btn 被其它方法引用）。
        self.broker_panel.hide()

        self.status_bar_widget = LiveStrategyStatusBarWidget(self)
        layout.addWidget(self.status_bar_widget)

        self.nav_tree = QTreeWidget(self)
        self.nav_tree.setHeaderHidden(True)
        self.nav_tree.setIndentation(10)
        self.nav_tree.setFixedWidth(118)
        self.nav_tree.setStyleSheet(
            "QTreeWidget { background:#151515; color:#d1d5db; border:1px solid #2b2b2b; border-radius:4px; }"
            "QTreeWidget::branch { background:#151515; }"
            "QTreeWidget::item { height:26px; padding:1px 4px; }"
            "QTreeWidget::item:hover { background:#242424; color:#ffffff; border-radius:3px; }"
            "QTreeWidget::item:selected { background:#0078d4; color:#ffffff; border-radius:3px; }"
        )
        self.nav_tree.currentItemChanged.connect(self._on_navigation_item_changed)
        self.page_stack = QStackedWidget(self)
        self.page_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._nav_items: dict[str, QTreeWidgetItem] = {}
        self._page_index_by_key: dict[str, int] = {}
        self._page_titles: dict[str, str] = {}
        self._suppress_nav_signal = False

        self.ai_panel = AITradeDecisionPanel(
            context_provider=context_provider,
            parent=self,
            symbol_name_resolver=symbol_name_resolver,
            name_map=name_map,
            etf_name_map=etf_name_map,
            shared_broker_panel=self.broker_panel,
            manage_startup=False,
        )
        self.etf_panel = ETFRotationLiveWidget(
            parent=self,
            broker_panel=self.broker_panel,
            manage_startup=False,
        )
        self.unmanaged_panel = UnmanagedPositionPanel(
            context_provider=context_provider,
            parent=self,
            symbol_name_resolver=symbol_name_resolver,
            name_map=name_map,
            etf_name_map=etf_name_map,
            shared_broker_panel=self.broker_panel,
        )

        self.ai_strategy_adapter = PanelLiveStrategyAdapter.from_panel(
            self.ai_panel,
            strategy_id=AI_STOCK_STRATEGY_ID,
            strategy_name="AI交易中心",
            automation_paused_provider=lambda: bool(getattr(self.ai_panel, "_paused_scheduler_task_ids", []) or []),
        )
        self.etf_strategy_adapter = PanelLiveStrategyAdapter.from_panel(
            self.etf_panel,
            automation_paused_provider=lambda: getattr(self.etf_panel, "_center_auto_pause_snapshot", None) is not None,
            rotation_pool_provider=self._get_etf_rotation_pool,
        )
        self.strategy_plugin_registry = LiveStrategyPluginRegistry()
        self._register_builtin_strategy_plugins()
        self.strategy_adapters = self.strategy_plugin_registry.adapters()
        self.portfolio_service = LiveStrategyPortfolioService(
            strategy_adapters=self.strategy_adapters,
            portfolio_providers=self.strategy_plugin_registry.portfolio_providers(),
            symbol_name_resolver=symbol_name_resolver,
        )

        self.end_of_day_service = LiveStrategyEndOfDayService(parent=self, rotation_etf_pool=[])
        for adapter in self.strategy_adapters:
            self.end_of_day_service.register_strategy(adapter.strategy_id, adapter.strategy_name, adapter.run_end_of_day)
        self.end_of_day_service.status_changed.connect(self._on_status_message)
        self.end_of_day_service.cycle_finished.connect(self._on_end_of_day_finished)
        self._eod_worker: Optional[_EndOfDayWorker] = None

        self.startup_orchestrator = QmtStartupOrchestrator(self.broker_panel.broker, self)
        self.startup_orchestrator.status_changed.connect(self._on_startup_status)
        self.startup_orchestrator.finished.connect(self._on_startup_finished)
        self._morning_freshness_timer = QTimer(self)
        self._morning_freshness_timer.setSingleShot(True)
        self._morning_freshness_timer.timeout.connect(self._run_morning_freshness_check)
        self._schedule_next_morning_freshness_check()

        self.center_storage = get_live_strategy_center_storage()
        self.alert_event_service = AlertEventService(self.center_storage, self)
        self.task_orchestrator_service = TaskOrchestratorService(self.center_storage, self)
        self.hub_state_service = HubStateService(self)
        self._auto_trade_config_service = get_auto_trade_config_service()
        self.hub_controller = LiveStrategyHubController(
            task_service=self.task_orchestrator_service,
            hub_state_service=self.hub_state_service,
            eod_service=self.end_of_day_service,
            strategy_adapters=self.strategy_adapters,
            startup_orchestrator=self.startup_orchestrator,
            parent=self,
        )
        self.hub_controller.sync_rotation_pool()

        self.alert_event_service.connect_broker_service(self.broker_panel.broker)
        self.alert_event_service.connect_qmt_startup(self.startup_orchestrator)
        self.alert_event_service.connect_end_of_day(self.end_of_day_service)
        self.alert_event_service.connect_ai_panel(self.ai_panel)
        self.alert_event_service.connect_etf_panel(self.etf_panel)

        self.alert_center_widget = LiveStrategyAlertCenterWidget(self.alert_event_service, self)
        self.task_center_widget = LiveStrategyTaskCenterWidget(self.task_orchestrator_service, self)
        self.exception_order_widget = LiveStrategyExceptionOrderWidget(
            self.broker_panel.broker,
            self,
        )
        self.performance_widget = LiveStrategyPerformanceWidget(
            self.portfolio_service,
            ai_panel=self.ai_panel,
            etf_panel=self.etf_panel,
            parent=self,
        )
        log_path = get_live_strategy_log_path()
        self.log_viewer = LiveLogViewerWidget(log_path, self)
        self.overview_widget = _LiveStrategyOverviewWidget(self)

        self._tab_widgets = {
            self.TAB_OVERVIEW: self.overview_widget,
            self.TAB_ALERTS: self.alert_center_widget,
            self.TAB_TASKS: self.task_center_widget,
            self.TAB_EXCEPTIONS: self.exception_order_widget,
            self.TAB_PERFORMANCE: self.performance_widget,
            self.TAB_LOGS: self.log_viewer,
        }
        self._page_titles = {
            self.TAB_OVERVIEW: "总览",
            self.TAB_ALERTS: "事件中心",
            self.TAB_TASKS: "任务中心",
            self.TAB_EXCEPTIONS: "异常订单",
            self.TAB_PERFORMANCE: "收益中心",
            self.TAB_LOGS: "运行日志",
        }
        for tab_key, tab_title, tab_widget in self.strategy_plugin_registry.tab_specs():
            self._tab_widgets[tab_key] = tab_widget
            self._page_titles[tab_key] = tab_title
        self._setup_navigation_workspace(layout)

        self.overview_widget.navigate_requested.connect(self.switch_to_tab)
        self.overview_widget.account_settings_requested.connect(self._open_account_settings_dialog)
        self.alert_center_widget.navigate_requested.connect(self.switch_to_tab)
        self.exception_order_widget.navigate_requested.connect(self.switch_to_tab)
        self.status_bar_widget.navigate_requested.connect(self.switch_to_tab)
        self.status_bar_widget.mode_change_requested.connect(self._set_auto_trade_mode)
        self.status_bar_widget.automation_toggle_requested.connect(self._toggle_center_automation)
        self.status_bar_widget.account_settings_requested.connect(self._open_account_settings_dialog)

        self.hub_controller.register_center_tasks(
            startup_action=self._start_startup_orchestration,
            morning_freshness_action=self._run_morning_freshness_check,
            end_of_day_action=self._run_end_of_day_cycle,
            startup_message_provider=lambda: self.broker_panel.client_status_label.text()
            if hasattr(self.broker_panel, "client_status_label") else "",
            strategy_task_specs=self.strategy_plugin_registry.task_specs(),
        )
        self.hub_state_service.bind(
            broker_service=self.broker_panel.broker,
            startup_orchestrator=self.startup_orchestrator,
            eod_service=self.end_of_day_service,
            alert_service=self.alert_event_service,
            task_service=self.task_orchestrator_service,
            strategy_adapters=self.strategy_adapters,
        )
        self.hub_state_service.state_changed.connect(self.status_bar_widget.refresh_view)
        self.hub_state_service.state_changed.connect(self.overview_widget.refresh_view)
        try:
            self.hub_state_service.refresh_state()
        except Exception:
            pass

        QTimer.singleShot(600, self._start_startup_orchestration)
        QTimer.singleShot(1200, self._refresh_center_public_views)

    def _register_builtin_strategy_plugins(self) -> None:
        self.strategy_plugin_registry.register(
            LiveStrategyPlugin(
                plugin_id=self.ai_strategy_adapter.strategy_id,
                plugin_name=self.ai_strategy_adapter.strategy_name,
                adapter=self.ai_strategy_adapter,
                widget=self.ai_panel,
                tab_key=self.TAB_AI,
                tab_title="AI策略",
                task_specs=(
                    LiveStrategyTaskSpec(
                        task_key="daily_ai_strategy_cycle",
                        task_type="ai",
                        title="每日 AI 策略总任务",
                        provider=self._task_provider_ai_scheduler,
                        strategy_id=self.ai_strategy_adapter.strategy_id,
                        strategy_name=self.ai_strategy_adapter.strategy_name,
                        actions={
                            "立即执行": lambda: self._run_strategy_action(
                                lambda: self.ai_panel.scheduler.run_now("daily_ai_strategy_cycle"),
                                "已触发 AI 定时任务",
                            ),
                            "暂停调度": self.ai_strategy_adapter.pause_automation,
                            "恢复调度": self.ai_strategy_adapter.resume_automation,
                        },
                        order=10,
                    ),
                ),
                portfolio_providers=(
                    create_ai_stock_portfolio_provider(self.ai_panel, order=10),
                ),
                order=10,
            )
        )
        self.strategy_plugin_registry.register(
            create_unmanaged_position_review_plugin(
                self.unmanaged_panel,
                tab_key=self.TAB_UNMANAGED,
                task_provider=self._task_provider_unmanaged_ai_scheduler,
                run_scan_action=lambda: self._run_strategy_action(
                    lambda: self.ai_panel.scheduler.run_now("daily_unmanaged_position_scan"),
                    "已触发未管理持仓 AI 巡检",
                ),
                order=20,
            )
        )
        self.strategy_plugin_registry.register(
            LiveStrategyPlugin(
                plugin_id=self.etf_strategy_adapter.strategy_id,
                plugin_name=self.etf_strategy_adapter.strategy_name,
                adapter=self.etf_strategy_adapter,
                widget=self.etf_panel,
                tab_key=self.TAB_ETF,
                tab_title="ETF轮动",
                task_specs=(
                    LiveStrategyTaskSpec(
                        task_key="etf_rotation_auto_check",
                        task_type="etf",
                        title="ETF 自动轮动检查",
                        provider=self._task_provider_etf_rotation,
                        strategy_id=self.etf_strategy_adapter.strategy_id,
                        strategy_name=self.etf_strategy_adapter.strategy_name,
                        actions={
                            "仅检查信号": lambda: self._run_strategy_action(
                                lambda: self.etf_panel.engine.run_signal_check(auto_execute=False),
                                "已触发 ETF 信号检查",
                            ),
                            "检查并执行": lambda: self._run_strategy_action(
                                lambda: self.etf_panel.engine.run_signal_check(auto_execute=True),
                                "已触发 ETF 信号检查并执行",
                            ),
                            "暂停调度": self.etf_strategy_adapter.pause_automation,
                            "恢复调度": self.etf_strategy_adapter.resume_automation,
                        },
                        order=10,
                    ),
                ),
                portfolio_providers=(
                    create_etf_rotation_portfolio_provider(
                        self.etf_panel,
                        self.etf_strategy_adapter,
                        order=10,
                    ),
                ),
                order=30,
            )
        )

    def _setup_navigation_workspace(self, root_layout: QVBoxLayout) -> None:
        content = QWidget(self)
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)
        content_layout.addWidget(self.nav_tree)
        content_layout.addWidget(self.page_stack, 1)
        root_layout.addWidget(content, 1)

        self._add_pages_to_stack()
        self._build_navigation_tree()
        self._select_navigation_item(self.TAB_OVERVIEW)

    def _add_pages_to_stack(self) -> None:
        preferred_order = [
            self.TAB_OVERVIEW,
            self.TAB_AI,
            self.TAB_ETF,
            self.TAB_UNMANAGED,
            self.TAB_EXCEPTIONS,
            self.TAB_ALERTS,
            self.TAB_TASKS,
            self.TAB_PERFORMANCE,
            self.TAB_LOGS,
        ]
        for key in preferred_order:
            self._add_page_to_stack(key)
        for key in list(self._tab_widgets.keys()):
            self._add_page_to_stack(key)

    def _add_page_to_stack(self, key: str) -> None:
        normalized = str(key or "").strip()
        if not normalized or normalized in self._page_index_by_key:
            return
        widget = self._tab_widgets.get(normalized)
        if widget is None:
            return
        self._page_index_by_key[normalized] = self.page_stack.addWidget(widget)

    def _build_navigation_tree(self) -> None:
        self.nav_tree.clear()
        self._nav_items.clear()

        self._add_nav_root("总览", self.TAB_OVERVIEW)

        strategy_root = self._add_nav_root("策略运行", self.TAB_AI)
        self._add_nav_child(strategy_root, "AI策略", self.TAB_AI)
        self._add_nav_child(strategy_root, "ETF轮动", self.TAB_ETF)
        self._add_nav_child(strategy_root, "未管理持仓", self.TAB_UNMANAGED)
        for key in self._extra_strategy_page_keys():
            self._add_nav_child(strategy_root, self._page_titles.get(key, key), key)

        self._add_nav_root("异常订单", self.TAB_EXCEPTIONS)
        self._add_nav_root("事件中心", self.TAB_ALERTS)
        self._add_nav_root("任务中心", self.TAB_TASKS)
        self._add_nav_root("收益中心", self.TAB_PERFORMANCE)
        self._add_nav_root("运行日志", self.TAB_LOGS)

        self.nav_tree.expandAll()

    def _extra_strategy_page_keys(self) -> list[str]:
        grouped = {
            self.TAB_OVERVIEW,
            self.TAB_AI,
            self.TAB_ETF,
            self.TAB_UNMANAGED,
            self.TAB_EXCEPTIONS,
            self.TAB_ALERTS,
            self.TAB_TASKS,
            self.TAB_PERFORMANCE,
            self.TAB_LOGS,
        }
        return [key for key in self._tab_widgets.keys() if key not in grouped]

    def _add_nav_root(self, title: str, key: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        self.nav_tree.addTopLevelItem(item)
        self._nav_items.setdefault(key, item)
        return item

    def _add_nav_child(self, parent: QTreeWidgetItem, title: str, key: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        parent.addChild(item)
        self._nav_items[key] = item
        return item



    def _on_navigation_item_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if self._suppress_nav_signal or current is None:
            return
        key = str(current.data(0, Qt.ItemDataRole.UserRole) or "").strip()
        if key:
            self.switch_to_tab(key)

    def _select_navigation_item(self, key: str) -> None:
        item = self._nav_items.get(str(key or "").strip())
        if item is None or self.nav_tree.currentItem() is item:
            return
        self._suppress_nav_signal = True
        try:
            self.nav_tree.setCurrentItem(item)
        finally:
            self._suppress_nav_signal = False

    @staticmethod
    def _run_strategy_action(callback: Callable[[], None], message: str) -> str:
        callback()
        return message

    def _task_provider_ai_scheduler(self) -> dict:
        try:
            rows = self.ai_strategy_adapter.get_task_summaries()
        except Exception:
            rows = []
        return dict(rows[0] if rows else {})

    def _task_provider_unmanaged_ai_scheduler(self) -> dict:
        try:
            return dict(self.ai_strategy_adapter.get_task_summary("daily_unmanaged_position_scan") or {})
        except Exception:
            return {}

    def _task_provider_etf_rotation(self) -> dict:
        try:
            rows = self.etf_strategy_adapter.get_task_summaries()
        except Exception:
            rows = []
        return dict(rows[0] if rows else {})

    def switch_to_tab(self, tab_name: str) -> None:
        normalized = str(tab_name or "").strip().lower()
        alias_map = {
            "overview": self.TAB_OVERVIEW,
            "home": self.TAB_OVERVIEW,
            "dashboard": self.TAB_OVERVIEW,
            "alert": self.TAB_ALERTS,
            "alerts": self.TAB_ALERTS,
            "tasks": self.TAB_TASKS,
            "task": self.TAB_TASKS,
            "exceptions": self.TAB_EXCEPTIONS,
            "orders": self.TAB_EXCEPTIONS,
            "performance": self.TAB_PERFORMANCE,
            "logs": self.TAB_LOGS,
            "log": self.TAB_LOGS,
            "ai": self.TAB_AI,
            "unmanaged": self.TAB_UNMANAGED,
            "etf": self.TAB_ETF,
        }
        target_key = alias_map.get(normalized, normalized if normalized in self._tab_widgets else self.TAB_AI)
        index = self._page_index_by_key.get(target_key)
        if index is not None:
            self.page_stack.setCurrentIndex(index)
            self._select_navigation_item(target_key)
            if target_key == self.TAB_LOGS:
                self.log_viewer.refresh_log()

    def set_symbol(self, code: str, name: str = "") -> None:
        self.switch_to_tab(self.TAB_AI)
        self.ai_panel.set_symbol(code, name)

    def _refresh_center_public_views(self) -> str:
        return self.hub_controller.refresh_public_views([
            self.alert_center_widget.refresh_events,
            self.exception_order_widget.refresh_orders,
            self.performance_widget.refresh_view,
        ])

    def _set_auto_trade_mode(self, mode: str) -> str:
        target_mode = str(mode or "off").strip().lower()
        current_cfg = self._auto_trade_config_service.get_config()
        current_mode = str(current_cfg.auto_trade_mode or "off").strip().lower()
        if target_mode == current_mode:
            return f"统一执行模式保持为 {current_mode}"
        if target_mode == "live" and not self._confirm_switch_to_live_mode():
            self.hub_controller.refresh_state()
            return "已取消切换到实盘模式"
        cfg = self._auto_trade_config_service.update_config(auto_trade_mode=target_mode)
        self.hub_controller.refresh_state()
        message = f"统一执行模式已切换为 {cfg.auto_trade_mode}"
        self.status_changed.emit(message)
        return message

    def _confirm_switch_to_live_mode(self) -> bool:
        state = self.hub_state_service.get_state()
        broker_connected = bool(state.get("broker_connected", False))
        qmt_running = bool(state.get("qmt_running", False))
        manual_enabled = bool(state.get("manual_orders_enabled", True))
        require_trading_time = bool(state.get("require_trading_time", True))
        alert_counts = dict(state.get("alert_counts", {}) or {})
        open_alerts = int(alert_counts.get("open", 0) or 0)
        exception_count = int(state.get("exception_order_count", 0) or 0)
        risk_summary = dict(state.get("risk_summary", {}) or {})
        risk_tooltip = str(risk_summary.get("tooltip", "") or "")
        details = [
            f"券商连接：{'已连接' if broker_connected else '未连接'}",
            f"QMT状态：{'运行中' if qmt_running else '未就绪'}",
            f"手动委托：{'开启' if manual_enabled else '关闭'}",
            f"交易时段闸：{'开启' if require_trading_time else '关闭'}",
            f"未处理告警：{open_alerts}",
            f"异常订单：{exception_count}",
        ]
        if risk_tooltip:
            details.append("")
            details.append(risk_tooltip)
        text = "即将切换到实盘模式，系统可能向券商提交真实委托。\n\n" + "\n".join(details) + "\n\n确认继续？"
        result = QMessageBox.question(
            self,
            "确认切换到实盘模式",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    def _toggle_center_automation(self, resume: bool) -> str:
        message = self.hub_controller.toggle_center_automation(resume)
        if message:
            self.status_changed.emit(message)
        return message

    def _open_account_settings_dialog(self) -> None:
        """Show the shared account-level gateway settings dialog.

        打开后修改的是 ``AutoTradeConfig``，对所有策略（AI + ETF + 其他条件单）
        生效；关闭后立刻刷新 hub 状态，避免状态栏与新配置不一致。
        """
        dialog = LiveStrategyAccountSettingsDialog(self, service=self._auto_trade_config_service)
        if dialog.exec():
            self.hub_controller.refresh_state()

    def _start_startup_orchestration(self) -> None:
        if self.startup_orchestrator.is_running:
            return
        started = self.startup_orchestrator.start()
        if started:
            self.broker_panel.show_client_workflow_status("启动自检中...", success=None)
            self.task_orchestrator_service.record_runtime(
                "startup_check",
                status="running",
                message="启动自检中...",
                trigger="manual_or_auto",
                started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

    def _schedule_next_morning_freshness_check(self) -> None:
        """Schedule one trading-day-only 09:35 market-data freshness check."""
        now = datetime.now()
        target = now.replace(hour=9, minute=35, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        while not is_trading_day(target.date()):
            target += timedelta(days=1)
        delay_ms = max(int((target - now).total_seconds() * 1000), 1000)
        self._morning_freshness_timer.start(delay_ms)

    def _run_morning_freshness_check(self) -> None:
        self._schedule_next_morning_freshness_check()
        self.task_orchestrator_service.record_runtime(
            "morning_freshness",
            status="running",
            message="盘中新鲜度检查已开始",
            trigger="timer_or_manual",
            started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        if self.startup_orchestrator.is_running:
            self.broker_panel.show_client_workflow_status("09:35 盘中数据检查跳过：当前自检进行中", success=None)
            self.task_orchestrator_service.record_runtime(
                "morning_freshness",
                status="skipped",
                message="当前自检进行中，跳过盘中新鲜度检查",
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            return
        started = self.startup_orchestrator.start()
        if started:
            self.broker_panel.show_client_workflow_status("09:35 盘中数据检查中...", success=None)

    def _on_startup_status(self, message: str) -> None:
        self.broker_panel.show_client_workflow_status(message, success=None)

    def _on_startup_finished(self, success: bool, message: str) -> None:
        self.broker_panel.show_client_workflow_status(message, success=success)
        self.broker_panel.refresh_client_status()
        self.task_orchestrator_service.record_runtime(
            "startup_check",
            status="completed" if success else "failed",
            message=message,
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        if success:
            QTimer.singleShot(800, self._try_end_of_day_catchup)

    def _run_end_of_day_cycle(self) -> None:
        self._sync_rotation_pool()
        self.task_orchestrator_service.record_runtime(
            "end_of_day_cycle",
            status="running",
            message="开始执行统一日终流程",
            trigger="manual",
            started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._start_end_of_day_worker("manual")

    def _try_end_of_day_catchup(self) -> None:
        self._sync_rotation_pool()
        self.task_orchestrator_service.record_runtime(
            "end_of_day_cycle",
            status="running",
            message="检查并补跑缺失的日终流程",
            trigger="catchup",
            started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._start_end_of_day_worker("catchup")

    def _sync_rotation_pool(self) -> None:
        """Push the latest ETF rotation pool into the EOD service."""
        self.hub_controller.sync_rotation_pool()

    def _get_etf_rotation_pool(self) -> list[str]:
        engine = getattr(self.etf_panel, "engine", None)
        config = getattr(engine, "config", None)
        return list(getattr(config, "etf_pool", []) or [])

    def _start_end_of_day_worker(self, mode: str) -> None:
        if self._eod_worker is not None and self._eod_worker.isRunning():
            return
        self.run_eod_btn.setEnabled(False)
        if mode == "catchup":
            self._on_status_message("检查并补跑缺失的日终流程...")
        else:
            self._on_status_message("开始执行统一日终流程...")
        self._eod_worker = _EndOfDayWorker(self.end_of_day_service, mode, self)
        self._eod_worker.finished_cycle.connect(self._on_end_of_day_worker_finished)
        self._eod_worker.failed_cycle.connect(self._on_end_of_day_worker_failed)
        self._eod_worker.finished.connect(self._cleanup_end_of_day_worker)
        self._eod_worker.start()

    def _on_end_of_day_worker_finished(self, mode: str, success: bool, message: str, payload: object) -> None:
        if mode == "catchup" and not success:
            self._on_status_message(message)
            self.run_eod_btn.setEnabled(True)
            cycle_state = self.end_of_day_service._get_cycle_state()  # noqa: SLF001
            overall_status = str(cycle_state.get("status", "") or "").strip().lower()
            runtime_status = "completed" if overall_status == "completed" else ("failed" if overall_status == "failed" else "skipped")
            self.task_orchestrator_service.record_runtime(
                "end_of_day_cycle",
                status=runtime_status,
                message=message,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            return
        if mode == "catchup" and success:
            self._on_status_message(f"🔁 {message}")
            return
        if not success:
            self.run_eod_btn.setEnabled(True)
            self._on_status_message(f"❌ {message}")

    def _on_end_of_day_worker_failed(self, _mode: str, message: str) -> None:
        self.run_eod_btn.setEnabled(True)
        self._on_status_message(f"❌ 日终线程异常: {message}")
        self.task_orchestrator_service.record_runtime(
            "end_of_day_cycle",
            status="failed",
            message=message,
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _cleanup_end_of_day_worker(self) -> None:
        worker = self._eod_worker
        if worker is None:
            return
        worker.deleteLater()
        self._eod_worker = None

    def _on_end_of_day_finished(self, success: bool, message: str, _payload: dict) -> None:
        self.run_eod_btn.setEnabled(True)
        self.hub_controller.refresh_strategies_after_eod()
        if success:
            self._finalize_day_snapshots()
        color = "#4caf50" if success else "#d9534f"
        self.eod_status_label.setStyleSheet(f"color:{color};font-size:12px;")
        self._set_eod_text(f"日终: {message}")
        self.status_changed.emit(message)
        self.task_orchestrator_service.record_runtime(
            "end_of_day_cycle",
            status="completed" if success else "failed",
            message=message,
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._refresh_center_public_views()

    def _finalize_day_snapshots(self) -> None:
        """日终成功后统一固化全部策略快照（供 PnL 曲线/回测使用）。"""
        try:
            self.portfolio_service.finalize_day_snapshots(remark="日终统一快照")
        except Exception:
            pass

    def _on_status_message(self, message: str) -> None:
        self.eod_status_label.setStyleSheet("color:#888;font-size:12px;")
        self._set_eod_text(f"日终: {message}")
        self.status_changed.emit(message)

    def _set_eod_text(self, text: str) -> None:
        self.eod_status_label.setToolTip(text)
        metrics = QFontMetrics(self.eod_status_label.font())
        self.eod_status_label.setText(
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, self.eod_status_label.width())
        )

    def closeEvent(self, event) -> None:
        try:
            self.startup_orchestrator.cancel()
        except Exception:
            pass
        try:
            self._morning_freshness_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)


class LiveStrategyHubWindow(QMainWindow):
    """Window wrapper for the unified live strategy workspace."""

    def __init__(
        self,
        parent=None,
        *,
        context_provider=None,
        symbol_name_resolver: Optional[Callable[[str], str]] = None,
        name_map: Optional[Dict[str, str]] = None,
        etf_name_map: Optional[Dict[str, str]] = None,
        initial_tab: str = LiveStrategyHubWidget.TAB_OVERVIEW,
    ):
        super().__init__(parent)
        self.setWindowTitle("实盘策略中心")
        self.resize(1480, 900)
        self.setMinimumSize(1150, 800)
        self._allow_close = False
        self._tray_notice_shown = False
        self._tray_icon: Optional[QSystemTrayIcon] = None

        self.workspace = LiveStrategyHubWidget(
            self,
            context_provider=context_provider,
            symbol_name_resolver=symbol_name_resolver,
            name_map=name_map,
            etf_name_map=etf_name_map,
        )
        self.setCentralWidget(self.workspace)
        self.workspace.switch_to_tab(initial_tab)
        self.workspace.status_changed.connect(self.statusBar().showMessage)
        self.statusBar().showMessage("就绪")
        self._setup_window_icon()
        self._setup_system_tray()

    def switch_to_tab(self, tab_name: str) -> None:
        self.workspace.switch_to_tab(tab_name)

    def set_symbol(self, code: str, name: str = "") -> None:
        self.workspace.set_symbol(code, name)

    def _setup_window_icon(self) -> None:
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "icon.jpeg")
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
            if not icon.isNull():
                self.setWindowIcon(icon)
                app = QApplication.instance()
                if app is not None:
                    app.setWindowIcon(icon)

    def _setup_system_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray_icon = QSystemTrayIcon(self.windowIcon(), self)
        tray_icon.setToolTip("实盘策略中心")

        menu = QMenu(self)
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self._show_from_tray)
        menu.addAction(show_action)

        show_alert_action = QAction("打开事件中心", self)
        show_alert_action.triggered.connect(lambda: self._show_tab_from_tray(self.workspace.TAB_ALERTS))
        menu.addAction(show_alert_action)

        show_log_action = QAction("显示运行日志", self)
        show_log_action.triggered.connect(self._show_logs_from_tray)
        menu.addAction(show_log_action)

        hide_action = QAction("隐藏到托盘", self)
        hide_action.triggered.connect(self._hide_to_tray)
        menu.addAction(hide_action)

        menu.addSeparator()

        run_eod_action = QAction("执行日终", self)
        run_eod_action.triggered.connect(self.workspace._run_end_of_day_cycle)
        menu.addAction(run_eod_action)

        menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self._exit_from_tray)
        menu.addAction(exit_action)

        tray_icon.setContextMenu(menu)
        tray_icon.activated.connect(self._on_tray_activated)
        tray_icon.show()
        self._tray_icon = tray_icon

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            if self.isVisible() and not self.isMinimized():
                self._hide_to_tray()
            else:
                self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _show_logs_from_tray(self) -> None:
        self._show_tab_from_tray(self.workspace.TAB_LOGS)

    def _show_tab_from_tray(self, tab_name: str) -> None:
        self._show_from_tray()
        self.workspace.switch_to_tab(tab_name)

    def _hide_to_tray(self) -> None:
        if self._tray_icon is None:
            self.hide()
            return
        self.hide()
        if not self._tray_notice_shown:
            self._tray_icon.showMessage(
                "实盘策略中心",
                "程序已隐藏到系统托盘，可从右下角托盘图标恢复。",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            self._tray_notice_shown = True

    def _exit_from_tray(self) -> None:
        self._allow_close = True
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self.close()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        tray_icon = getattr(self, "_tray_icon", None)
        if tray_icon is None:
            return
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self._hide_to_tray)

    def closeEvent(self, event) -> None:
        tray_icon = getattr(self, "_tray_icon", None)
        allow_close = bool(getattr(self, "_allow_close", False))
        if allow_close or tray_icon is None:
            if tray_icon is not None:
                tray_icon.hide()
            super().closeEvent(event)
            return
        event.ignore()
        self._hide_to_tray()
