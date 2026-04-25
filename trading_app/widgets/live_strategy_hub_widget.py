from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QEvent, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFontMetrics, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTabWidget,
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
    LiveStrategyPortfolioService,
    PanelLiveStrategyAdapter,
    TaskOrchestratorService,
    get_live_strategy_center_storage,
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
    """Unified live strategy workspace with AI and ETF tabs."""

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
        self.run_eod_btn.setFixedHeight(22)
        self.run_eod_btn.clicked.connect(self._run_end_of_day_cycle)
        eod_layout.addWidget(self.run_eod_btn)
        self._set_eod_text("日终: 待命")
        self.broker_panel.set_trailing_widget(eod_bar)
        # 状态栏接管展示后，原先的券商面板/日终按钮行不再上屏，但内部对象仍需保留
        # （broker 服务、eod_status_label、run_eod_btn 被其它方法引用）。
        self.broker_panel.hide()

        self.status_bar_widget = LiveStrategyStatusBarWidget(self)
        layout.addWidget(self.status_bar_widget)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

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
        self.strategy_adapters = [self.ai_strategy_adapter, self.etf_strategy_adapter]
        self.portfolio_service = LiveStrategyPortfolioService(
            strategy_adapters=self.strategy_adapters,
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
        self.log_viewer = LiveLogViewerWidget(get_live_strategy_log_path(), self)

        self._tab_widgets = {
            self.TAB_AI: self.ai_panel,
            self.TAB_UNMANAGED: self.unmanaged_panel,
            self.TAB_ETF: self.etf_panel,
            self.TAB_ALERTS: self.alert_center_widget,
            self.TAB_TASKS: self.task_center_widget,
            self.TAB_EXCEPTIONS: self.exception_order_widget,
            self.TAB_PERFORMANCE: self.performance_widget,
            self.TAB_LOGS: self.log_viewer,
        }
        for key, title in [
            (self.TAB_AI, "AI策略"),
            (self.TAB_UNMANAGED, "未管理持仓"),
            (self.TAB_ETF, "ETF轮动"),
            (self.TAB_ALERTS, "告警中心"),
            (self.TAB_TASKS, "任务中心"),
            (self.TAB_EXCEPTIONS, "异常订单"),
            (self.TAB_PERFORMANCE, "收益中心"),
            (self.TAB_LOGS, "运行日志"),
        ]:
            self.tabs.addTab(self._tab_widgets[key], title)

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
            ai_task_action=lambda: self.ai_panel.scheduler.run_now("daily_ai_strategy_cycle"),
            unmanaged_scan_action=lambda: self.ai_panel.scheduler.run_now("daily_unmanaged_position_scan"),
            etf_scan_action=lambda: self.etf_panel.engine.run_signal_check(auto_execute=False),
            etf_execute_action=lambda: self.etf_panel.engine.run_signal_check(auto_execute=True),
            startup_message_provider=lambda: self.broker_panel.client_status_label.text()
            if hasattr(self.broker_panel, "client_status_label") else "",
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
        try:
            self.hub_state_service.refresh_state()
        except Exception:
            pass

        QTimer.singleShot(600, self._start_startup_orchestration)
        QTimer.singleShot(1200, self._refresh_center_public_views)

    def switch_to_tab(self, tab_name: str) -> None:
        normalized = str(tab_name or "").strip().lower()
        alias_map = {
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
        target_key = alias_map.get(normalized, self.TAB_AI)
        widget = self._tab_widgets.get(target_key)
        if widget is not None:
            self.tabs.setCurrentWidget(widget)

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
        initial_tab: str = LiveStrategyHubWidget.TAB_AI,
    ):
        super().__init__(parent)
        self.setWindowTitle("实盘策略中心")
        self.resize(1480, 900)
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

        show_alert_action = QAction("打开告警中心", self)
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
