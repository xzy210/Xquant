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

        rotation_pool = list(getattr(self.etf_panel, "engine", None) and self.etf_panel.engine.config.etf_pool or [])
        self.end_of_day_service = LiveStrategyEndOfDayService(parent=self, rotation_etf_pool=rotation_pool)
        self.end_of_day_service.register_strategy("ai_trade_decision_center", "AI交易中心", self.ai_panel.run_end_of_day_tasks)
        strategy_id, strategy_name, _virtual_account_id = self.etf_panel._etf_strategy_identity()
        self.end_of_day_service.register_strategy(strategy_id, strategy_name, self.etf_panel.run_end_of_day_tasks)
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
        self.performance_widget = LiveStrategyPerformanceWidget(self.ai_panel, self.etf_panel, self)
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
            (self.TAB_ALERTS, "事件中心"),
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
        self.status_bar_widget.emergency_pause_requested.connect(self._pause_center_automation)
        self.status_bar_widget.account_settings_requested.connect(self._open_account_settings_dialog)

        self._register_center_tasks()
        self.hub_state_service.bind(
            broker_service=self.broker_panel.broker,
            startup_orchestrator=self.startup_orchestrator,
            eod_service=self.end_of_day_service,
            ai_panel=self.ai_panel,
            etf_panel=self.etf_panel,
            alert_service=self.alert_event_service,
            task_service=self.task_orchestrator_service,
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

    def _register_center_tasks(self) -> None:
        self.task_orchestrator_service.register_task(
            task_key="startup_check",
            task_type="system",
            title="启动自检",
            provider=self._task_provider_startup,
            actions={"立即执行": self._trigger_startup_check},
        )
        self.task_orchestrator_service.register_task(
            task_key="morning_freshness",
            task_type="system",
            title="数据新鲜度检查",
            provider=self._task_provider_morning_freshness,
            actions={"立即执行": self._trigger_morning_freshness},
        )
        self.task_orchestrator_service.register_task(
            task_key="end_of_day_cycle",
            task_type="eod",
            title="统一日终流程",
            provider=self._task_provider_end_of_day,
            actions={"立即执行": self._trigger_end_of_day},
        )
        self.task_orchestrator_service.register_task(
            task_key="daily_ai_strategy_cycle",
            task_type="ai",
            title="每日 AI 策略总任务",
            provider=self._task_provider_ai_scheduler,
            actions={
                "立即执行": self._trigger_ai_task_now,
                "暂停调度": self.ai_panel.pause_center_automation,
                "恢复调度": self.ai_panel.resume_center_automation,
            },
        )
        self.task_orchestrator_service.register_task(
            task_key="daily_unmanaged_position_scan",
            task_type="ai",
            title="未管理持仓 AI 巡检",
            provider=self._task_provider_unmanaged_ai_scheduler,
            actions={"立即执行": self._trigger_unmanaged_scan_now},
        )
        self.task_orchestrator_service.register_task(
            task_key="etf_rotation_auto_check",
            task_type="etf",
            title="ETF 自动轮动检查",
            provider=self._task_provider_etf_rotation,
            actions={
                "仅检查信号": self._trigger_etf_scan_now,
                "检查并执行": self._trigger_etf_execute_now,
                "暂停调度": self.etf_panel.pause_center_automation,
                "恢复调度": self.etf_panel.resume_center_automation,
            },
        )

    def _refresh_center_public_views(self) -> str:
        try:
            self.hub_state_service.refresh_state()
        except Exception:
            pass
        try:
            self.alert_center_widget.refresh_events()
        except Exception:
            pass
        try:
            self.exception_order_widget.refresh_orders()
        except Exception:
            pass
        try:
            self.performance_widget.refresh_view()
        except Exception:
            pass
        return "中心公共视图已刷新"

    def _set_auto_trade_mode(self, mode: str) -> str:
        cfg = self._auto_trade_config_service.update_config(auto_trade_mode=mode)
        self.hub_state_service.refresh_state()
        return f"统一执行模式已切换为 {cfg.auto_trade_mode}"

    def _open_account_settings_dialog(self) -> None:
        """Show the shared account-level gateway settings dialog.

        打开后修改的是 ``AutoTradeConfig``，对所有策略（AI + ETF + 其他条件单）
        生效；关闭后立刻刷新 hub 状态，避免状态栏与新配置不一致。
        """
        dialog = LiveStrategyAccountSettingsDialog(self, service=self._auto_trade_config_service)
        if dialog.exec():
            try:
                self.hub_state_service.refresh_state()
            except Exception:
                pass

    def _pause_center_automation(self) -> str:
        messages = [
            self.ai_panel.pause_center_automation(),
            self.etf_panel.pause_center_automation(),
        ]
        return "；".join([item for item in messages if item])

    def _resume_center_automation(self) -> str:
        messages = [
            self.ai_panel.resume_center_automation(),
            self.etf_panel.resume_center_automation(),
        ]
        return "；".join([item for item in messages if item])

    def _trigger_startup_check(self) -> str:
        self._start_startup_orchestration()
        return "已触发启动自检"

    def _trigger_morning_freshness(self) -> str:
        self._run_morning_freshness_check()
        return "已触发盘中新鲜度检查"

    def _trigger_end_of_day(self) -> str:
        self._run_end_of_day_cycle()
        return "已触发统一日终流程"

    def _trigger_ai_task_now(self) -> str:
        self.ai_panel.scheduler.run_now("daily_ai_strategy_cycle")
        return "已触发 AI 定时任务"

    def _trigger_unmanaged_scan_now(self) -> str:
        self.ai_panel.scheduler.run_now("daily_unmanaged_position_scan")
        return "已触发未管理持仓 AI 巡检"

    def _trigger_etf_scan_now(self) -> str:
        self.etf_panel.engine.run_signal_check(auto_execute=False)
        return "已触发 ETF 信号检查"

    def _trigger_etf_execute_now(self) -> str:
        self.etf_panel.engine.run_signal_check(auto_execute=True)
        return "已触发 ETF 信号检查并执行"

    def _task_provider_startup(self) -> dict:
        return {
            "status": "running" if self.startup_orchestrator.is_running else "idle",
            "message": self.broker_panel.client_status_label.text() if hasattr(self.broker_panel, "client_status_label") else "",
            "last_run": "",
            "schedule_time": "启动后自动 / 手动触发",
        }

    def _task_provider_morning_freshness(self) -> dict:
        return {
            "status": "scheduled",
            "message": "交易日 09:35 自动检查",
            "schedule_time": "09:35",
        }

    def _task_provider_end_of_day(self) -> dict:
        cycle_state = self.end_of_day_service._get_cycle_state()  # noqa: SLF001
        return {
            "status": str(cycle_state.get("status", "") or "idle"),
            "message": str(cycle_state.get("last_error", "") or cycle_state.get("updated_at", "") or ""),
            "last_run": str(cycle_state.get("completed_at", "") or cycle_state.get("updated_at", "") or ""),
            "schedule_time": "收盘后 / 手动触发",
        }

    def _task_provider_ai_scheduler(self) -> dict:
        rows = self.ai_panel.get_center_task_summaries()
        return dict(rows[0] if rows else {})

    def _task_provider_unmanaged_ai_scheduler(self) -> dict:
        return dict(self.ai_panel.get_center_task_summary("daily_unmanaged_position_scan") or {})

    def _task_provider_etf_rotation(self) -> dict:
        rows = self.etf_panel.get_center_task_summaries()
        return dict(rows[0] if rows else {})

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
        pool = list(getattr(self.etf_panel, "engine", None) and self.etf_panel.engine.config.etf_pool or [])
        self.end_of_day_service.set_rotation_etf_pool(pool)

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
        self.ai_panel.refresh_end_of_day_ui()
        self.etf_panel.refresh_end_of_day_ui()
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
            strategy_budget = self.ai_panel.account_panel.strategy_budget
        except Exception:
            return

        providers: dict[str, dict] = {}

        try:
            live = self.ai_panel.account_panel.get_live_positions() or []
            providers[AI_STOCK_STRATEGY_ID] = {
                "live_positions": [
                    {
                        "stock_code": item.get("code", "") or "",
                        "market_value": float(item.get("market_value", 0.0) or 0.0),
                        "volume": int(item.get("volume", 0) or 0),
                        "name": item.get("name", "") or "",
                    }
                    for item in live
                ],
                "remark": "日终统一快照",
            }
        except Exception:
            pass

        try:
            etf_strategy_id, _name, _vaid = self.etf_panel._etf_strategy_identity()  # noqa: SLF001
            summary = dict(self.etf_panel.engine.get_status_summary() or {})
            holding = str(summary.get("holding", "") or "")
            spot_prices: dict[str, float] = {}
            if holding:
                current_price = float(summary.get("current_price", 0.0) or 0.0)
                if current_price <= 0:
                    current_price = float(summary.get("buy_price", 0.0) or 0.0)
                if current_price > 0:
                    spot_prices[holding] = current_price
            etf_provider: dict[str, object] = {"remark": "日终统一快照"}
            if spot_prices:
                etf_provider["spot_prices"] = spot_prices
            # 主账本为唯一真源：不再传 capital_limit_override / cash_override
            providers[etf_strategy_id] = etf_provider
        except Exception:
            pass

        try:
            strategy_budget.finalize_day(providers=providers, remark="日终统一快照")
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
