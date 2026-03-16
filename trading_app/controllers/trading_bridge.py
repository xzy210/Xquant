from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QMainWindow

from common.broker_session_service import get_broker_session_service
from services.auto_stop_loss_service import get_auto_stop_loss_service
from services.conditional_order_service import get_conditional_order_service
from services.trade_record_service import set_auto_stop_loss_service_getter
from widgets.broker_account_widget import BrokerAccountWidget

logger = logging.getLogger(__name__)


class TradingBridge(QObject):
    """Bridges trading UI, broker session, and trading-related services."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.broker_session_service = get_broker_session_service()
        self.broker_window = None
        self._broker_widget = None
        self._conditional_log_connected = False
        self._auto_stop_log_connected = False

    @property
    def broker_widget(self):
        return self._broker_widget

    def initialize(self):
        conditional_service = get_conditional_order_service()
        conditional_service.start_monitoring()
        if not self._conditional_log_connected:
            conditional_service.log_message.connect(
                lambda msg: self.main_window.statusBar().showMessage(msg, 5000)
            )
            self._conditional_log_connected = True

        auto_stop_loss_service = get_auto_stop_loss_service()
        auto_stop_loss_service.set_conditional_order_service(conditional_service)
        if not self._auto_stop_log_connected:
            auto_stop_loss_service.log_message.connect(
                lambda msg: self.main_window.statusBar().showMessage(msg, 5000)
            )
            self._auto_stop_log_connected = True
        set_auto_stop_loss_service_getter(get_auto_stop_loss_service)

        pending_count = conditional_service.pending_count
        if pending_count > 0:
            self.main_window.statusBar().showMessage(
                f"✓ 条件单监控已启动，{pending_count}个待触发",
                5000,
            )
        else:
            self.main_window.statusBar().showMessage("✓ 条件单监控已启动", 3000)

    def shutdown(self):
        if self.broker_window and self.broker_window.isVisible():
            try:
                self.broker_window.close()
            except Exception:
                pass
        try:
            self.broker_session_service.disconnect()
        except Exception:
            pass

    def on_quote_updated(self, quote_data):
        if quote_data.last_price <= 0:
            return
        conditional_service = get_conditional_order_service()
        if conditional_service.is_monitoring:
            conditional_service.check_single_quote(
                quote_data.simple_code,
                quote_data.last_price,
            )

    def open_broker_account(self, stock_code: str = None):
        if self.broker_window and self.broker_window.isVisible():
            self.broker_window.activateWindow()
            self.broker_window.raise_()
            if stock_code and self._broker_widget:
                self._broker_widget.set_stock_code(stock_code)
            return

        self.broker_window = QMainWindow(self.main_window)
        self.broker_window.setWindowTitle("交易")
        self.broker_window.resize(1200, 800)
        self.broker_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        combined_name_map = {**self.main_window.name_map, **self.main_window.etf_name_map}
        self._broker_widget = BrokerAccountWidget(
            name_map=combined_name_map,
            broker_session_service=self.broker_session_service,
        )
        if stock_code:
            self._broker_widget.set_stock_code(stock_code)

        self._broker_widget.positionsUpdated.connect(self.sync_broker_positions)
        self.broker_window.setCentralWidget(self._broker_widget)
        self.broker_window.destroyed.connect(self._on_broker_window_destroyed)
        self.broker_window.show()

    def _on_broker_window_destroyed(self):
        self.broker_window = None
        self._broker_widget = None

    def sync_broker_positions(self, position_codes: list):
        success, msg = self.main_window.watchlist_manager.update_broker_positions(position_codes)
        if success:
            self.main_window.stock_list_widget.update_group_combo()
            self.main_window.etf_list_widget.update_group_combo()
            if self.main_window.stock_list_widget.get_current_group() == "中金持仓":
                self.main_window.stock_list_widget.on_group_combo_changed(
                    self.main_window.stock_list_widget.group_combo.currentIndex()
                )
            self.main_window.statusBar().showMessage(msg)
