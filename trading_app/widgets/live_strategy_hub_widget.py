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
from trading_app.services.live_strategy_end_of_day_service import LiveStrategyEndOfDayService
from trading_app.services.live_strategy_logging import get_live_strategy_log_path
from trading_app.services.qmt_startup_orchestrator import QmtStartupOrchestrator
from widgets.live_log_viewer_widget import LiveLogViewerWidget
from widgets.ai_trade_decision_widget import AITradeDecisionPanel
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

    TAB_AI = "ai"
    TAB_ETF = "etf"
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
        layout.addWidget(self.broker_panel)

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

        self.tabs.addTab(self.ai_panel, "AI策略")
        self.tabs.addTab(self.etf_panel, "ETF轮动")
        self.log_viewer = LiveLogViewerWidget(get_live_strategy_log_path(), self)
        self.tabs.addTab(self.log_viewer, "运行日志")

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
        QTimer.singleShot(600, self._start_startup_orchestration)

    def switch_to_tab(self, tab_name: str) -> None:
        normalized = str(tab_name or "").strip().lower()
        if normalized == self.TAB_ETF:
            self.tabs.setCurrentWidget(self.etf_panel)
            return
        self.tabs.setCurrentWidget(self.ai_panel)

    def set_symbol(self, code: str, name: str = "") -> None:
        self.switch_to_tab(self.TAB_AI)
        self.ai_panel.set_symbol(code, name)

    def _start_startup_orchestration(self) -> None:
        if self.startup_orchestrator.is_running:
            return
        started = self.startup_orchestrator.start()
        if started:
            self.broker_panel.show_client_workflow_status("启动自检中...", success=None)

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
        if self.startup_orchestrator.is_running:
            self.broker_panel.show_client_workflow_status("09:35 盘中数据检查跳过：当前自检进行中", success=None)
            return
        started = self.startup_orchestrator.start()
        if started:
            self.broker_panel.show_client_workflow_status("09:35 盘中数据检查中...", success=None)

    def _on_startup_status(self, message: str) -> None:
        self.broker_panel.show_client_workflow_status(message, success=None)

    def _on_startup_finished(self, success: bool, message: str) -> None:
        self.broker_panel.show_client_workflow_status(message, success=success)
        self.broker_panel.refresh_client_status()
        if success:
            QTimer.singleShot(800, self._try_end_of_day_catchup)

    def _run_end_of_day_cycle(self) -> None:
        self._sync_rotation_pool()
        self._start_end_of_day_worker("manual")

    def _try_end_of_day_catchup(self) -> None:
        self._sync_rotation_pool()
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
        color = "#4caf50" if success else "#d9534f"
        self.eod_status_label.setStyleSheet(f"color:{color};font-size:12px;")
        self._set_eod_text(f"日终: {message}")
        self.status_changed.emit(message)

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
        self._show_from_tray()
        self.workspace.tabs.setCurrentWidget(self.workspace.log_viewer)

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
