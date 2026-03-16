from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from data_loader import get_etf_cache, get_etf_list, get_stock_cache, get_stock_list, load_stock_data
from scheduler import FullDataSyncWorker

logger = logging.getLogger(__name__)


class SyncController(QObject):
    """Coordinates full-sync lifecycle and runtime pause/resume."""

    def __init__(self, main_window, realtime_controller=None, trading_bridge=None, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.realtime_controller = realtime_controller
        self.trading_bridge = trading_bridge
        self.full_sync_worker = None
        self._full_sync_runtime_state = None
        self._market_close_reminder_shown_today = False

    def start_full_data_sync(self):
        if self.full_sync_worker and self.full_sync_worker.isRunning():
            QMessageBox.warning(self.main_window, "提示", "正在同步中，请等待完成...")
            return

        reply = QMessageBox.question(
            self.main_window,
            "全量数据同步",
            "即将开始全量同步数据，包括：\n\n"
            "1. 股票日线数据（全量前复权）\n"
            "2. ETF日线数据（全量前复权）\n"
            "3. 指数日线数据\n\n"
            "此操作可能需要较长时间，确认继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.prepare_runtime_for_full_sync()
        self.main_window.sync_btn.setEnabled(False)
        self.main_window.sync_btn.setText("⏳ 同步中...")
        self.main_window.statusBar().showMessage("🔄 开始全量数据同步，已暂停实时行情...")

        start_date = self.main_window.scheduler_manager.config.get("maint_start_date", "20080101")
        self.full_sync_worker = FullDataSyncWorker(
            self.main_window.data_dir,
            self.main_window.stocklist_path,
            start_date=start_date,
        )
        self.full_sync_worker.progress_signal.connect(self.on_sync_progress)
        self.full_sync_worker.log_signal.connect(self.on_sync_log)
        self.full_sync_worker.finished_signal.connect(self.on_sync_finished)
        self.full_sync_worker.start()

    def prepare_runtime_for_full_sync(self):
        broker_widget = self.trading_bridge.broker_widget if self.trading_bridge else None
        self._full_sync_runtime_state = {
            "kline_realtime_enabled": bool(
                hasattr(self.main_window, 'kline_widget') and self.main_window.kline_widget.is_realtime_enabled
            ),
            "timeshare_refresh_active": bool(
                hasattr(self.main_window, 'timeshare_widget')
                and hasattr(self.main_window.timeshare_widget, '_refresh_timer')
                and self.main_window.timeshare_widget._refresh_timer.isActive()
            ),
            "watchlist_panel_realtime_enabled": bool(
                hasattr(self.main_window, 'watchlist_panel') and getattr(self.main_window.watchlist_panel, '_realtime_enabled', False)
            ),
            "watchlist_panel_auto_refresh_active": bool(
                hasattr(self.main_window, 'watchlist_panel') and getattr(self.main_window.watchlist_panel, '_auto_refresh_active', False)
            ),
            "quote_service_running": bool(
                self.realtime_controller and self.realtime_controller.quote_service.is_running
            ),
            "sector_window_running": bool(
                self.main_window.sector_window and self.main_window.sector_window.isVisible()
                and getattr(self.main_window.sector_window, '_is_running', False)
            ),
            "broker_order_book_active": bool(
                broker_widget and hasattr(broker_widget, 'order_book_timer') and broker_widget.order_book_timer.isActive()
            ),
        }

        try:
            self.main_window.timeshare_widget.stop_auto_refresh()
        except Exception:
            pass
        try:
            if self.main_window.kline_widget.is_realtime_enabled:
                self.main_window.kline_widget.stop_realtime()
        except Exception:
            pass
        try:
            self.main_window.watchlist_panel.set_auto_refresh_active(False)
        except Exception:
            pass
        try:
            if self.main_window.sector_window and self.main_window.sector_window.isVisible():
                self.main_window.sector_window.stop_service()
        except Exception:
            pass
        try:
            if broker_widget and hasattr(broker_widget, 'order_book_timer'):
                broker_widget.order_book_timer.stop()
        except Exception:
            pass
        if self.realtime_controller:
            self.realtime_controller.pause_for_sync()

    def restore_runtime_after_full_sync(self):
        state = self._full_sync_runtime_state or {}
        self._full_sync_runtime_state = None
        if not state:
            return

        if self.realtime_controller:
            self.realtime_controller.resume_after_sync(bool(state.get("quote_service_running")))

        try:
            if state.get("watchlist_panel_realtime_enabled"):
                self.main_window.watchlist_panel._start_realtime()
            else:
                self.main_window.watchlist_panel.set_auto_refresh_active(
                    bool(state.get("watchlist_panel_auto_refresh_active"))
                )
        except Exception:
            pass

        try:
            if state.get("timeshare_refresh_active") and self.main_window.right_tabs.currentIndex() == 1:
                if self.main_window.current_view == "etf":
                    self.main_window.load_etf_timeshare_data()
                else:
                    self.main_window.load_timeshare_data()
        except Exception:
            pass

        try:
            if state.get("sector_window_running") and self.main_window.sector_window and self.main_window.sector_window.isVisible():
                self.main_window.sector_window.start_service()
        except Exception:
            pass

        try:
            broker_widget = self.trading_bridge.broker_widget if self.trading_bridge else None
            if state.get("broker_order_book_active") and broker_widget and hasattr(broker_widget, 'order_book_timer'):
                broker_widget.order_book_timer.start()
        except Exception:
            pass

        try:
            if state.get("kline_realtime_enabled"):
                self.main_window.kline_widget.start_realtime()
        except Exception:
            pass

    def on_sync_progress(self, phase_name: str, current: int, total: int):
        if total > 0:
            percent = int(current * 100 / total)
            self.main_window.statusBar().showMessage(f"🔄 {phase_name}: {percent}% ({current}/{total})")

    def on_sync_log(self, message: str):
        logger.info("Full sync: %s", message)

    def on_sync_finished(self, success: bool, message: str):
        self.main_window.sync_btn.setEnabled(True)
        self.main_window.sync_btn.setText("🔄 同步数据")

        if success:
            self.main_window.statusBar().showMessage(f"✅ {message}")
            self.refresh_all_caches()
            self.main_window.load_stock_list()
            self.main_window.refresh_chart()
            QTimer.singleShot(200, self.restore_runtime_after_full_sync)
            QMessageBox.information(self.main_window, "同步完成", f"✅ {message}\n\n数据已刷新。")
        else:
            self.main_window.statusBar().showMessage(f"❌ {message}")
            self.restore_runtime_after_full_sync()
            QMessageBox.warning(self.main_window, "同步失败", f"❌ {message}")

    def refresh_all_caches(self):
        self.main_window.statusBar().showMessage("🔄 正在刷新缓存...")
        QApplication.processEvents()

        stock_cache = get_stock_cache()
        if stock_cache.is_loaded():
            stock_codes = get_stock_list(self.main_window.data_dir)
            count = stock_cache.reload_all(
                data_dir=self.main_window.data_dir,
                stock_codes=stock_codes,
                max_workers=8,
            )
            self.main_window.statusBar().showMessage(f"✅ 股票缓存已刷新 ({count}只)")

        etf_cache = get_etf_cache()
        if etf_cache.is_loaded():
            etf_codes = get_etf_list(self.main_window.data_dir)
            count = etf_cache.reload_all(
                data_dir=self.main_window.data_dir,
                etf_codes=etf_codes,
                max_workers=8,
            )
            self.main_window.statusBar().showMessage(f"✅ ETF缓存已刷新 ({count}只)")

    def check_market_close_reminder(self):
        from datetime import datetime, time

        if self._market_close_reminder_shown_today:
            return
        now = datetime.now()
        if now.weekday() >= 5:
            return
        if not (time(15, 5) <= now.time() <= time(15, 30)):
            return
        if self.is_data_up_to_date():
            return
        last_date = self.get_last_data_date()
        self.show_market_close_reminder(last_date)

    def is_data_up_to_date(self) -> bool:
        from datetime import date

        today = date.today()
        last_date = self.get_last_data_date()
        if last_date:
            return last_date == today.strftime("%Y-%m-%d")
        return False

    def get_last_data_date(self) -> str:
        if self.main_window.kline_widget.data is not None and not self.main_window.kline_widget.data.empty:
            last_date = self.main_window.kline_widget.data.iloc[-1]['date']
            if hasattr(last_date, 'strftime'):
                return last_date.strftime("%Y-%m-%d")
            return str(last_date)[:10]

        if self.main_window.stock_list:
            df = load_stock_data(self.main_window.stock_list[0], self.main_window.data_dir)
            if df is not None and not df.empty:
                last_date = df.iloc[-1]['date']
                if hasattr(last_date, 'strftime'):
                    return last_date.strftime("%Y-%m-%d")
                return str(last_date)[:10]
        return ""

    def show_market_close_reminder(self, last_data_date: str):
        dialog = self.main_window._build_market_close_reminder_dialog(last_data_date)
        dialog.syncNow.connect(self.on_reminder_sync_now)
        dialog.remindLater.connect(self.on_reminder_later)
        dialog.ignoreToday.connect(self.on_reminder_ignore)
        dialog.show()

    def on_reminder_sync_now(self):
        self.start_full_data_sync()
        self._market_close_reminder_shown_today = True

    def on_reminder_later(self):
        QTimer.singleShot(5 * 60 * 1000, self.check_market_close_reminder)

    def on_reminder_ignore(self):
        self._market_close_reminder_shown_today = True

    def shutdown(self):
        if self.full_sync_worker and self.full_sync_worker.isRunning():
            self.full_sync_worker.stop()
            self.full_sync_worker.wait(2000)
