from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

try:
    from services.auto_trade_config_service import get_auto_trade_config_service
except ImportError:
    from trading_app.services.auto_trade_config_service import get_auto_trade_config_service

try:
    from services.trade_record_service import get_trade_record_service
except ImportError:
    from trading_app.services.trade_record_service import get_trade_record_service

try:
    from services.strategy_budget_service import get_strategy_budget_service
except ImportError:
    from trading_app.services.strategy_budget_service import get_strategy_budget_service

logger = logging.getLogger(__name__)

_EXCEPTION_ORDER_STATUSES = {"blocked", "failed", "cancelled", "rejected"}
_UNMANAGED_RECONCILE_MIN_INTERVAL_SEC = 30.0


class HubStateService(QObject):
    state_changed = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.broker_service = None
        self.startup_orchestrator = None
        self.eod_service = None
        self.ai_panel = None
        self.etf_panel = None
        self.alert_service = None
        self.task_service = None
        self._state: dict = {}
        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self.refresh_state)
        self._auto_trade_config = get_auto_trade_config_service()
        self._trade_service = get_trade_record_service()
        self._budget_service = get_strategy_budget_service()
        self._last_unmanaged_reconcile_ts: float = 0.0
        self._last_broker_connected: bool = False
        try:
            self._budget_service.ensure_unmanaged_strategy()
        except Exception as exc:
            logger.warning("初始化 unmanaged 策略失败: %s", exc)

    def bind(
        self,
        *,
        broker_service,
        startup_orchestrator,
        eod_service,
        ai_panel,
        etf_panel,
        alert_service,
        task_service,
    ) -> None:
        self.broker_service = broker_service
        self.startup_orchestrator = startup_orchestrator
        self.eod_service = eod_service
        self.ai_panel = ai_panel
        self.etf_panel = etf_panel
        self.alert_service = alert_service
        self.task_service = task_service
        broker_service.connection_changed.connect(self._on_broker_connection_changed)
        broker_service.client_state_changed.connect(lambda *_: self.refresh_state())
        startup_orchestrator.finished.connect(lambda *_: self.refresh_state())
        eod_service.cycle_finished.connect(self._on_eod_cycle_finished)
        alert_service.events_changed.connect(self.refresh_state)
        task_service.tasks_changed.connect(lambda *_: self.refresh_state())
        try:
            self._trade_service.order_record_added.connect(lambda *_: self.refresh_state())
            self._trade_service.order_record_updated.connect(lambda *_: self.refresh_state())
        except Exception:
            pass
        self._timer.start()
        self.refresh_state()

    def refresh_state(self) -> None:
        if self.broker_service is None:
            return
        broker_status = dict(self.broker_service.get_client_status() or {})
        ai_status = dict(self.ai_panel.get_center_status_summary() or {}) if self.ai_panel is not None else {}
        etf_status = dict(self.etf_panel.get_center_status_summary() or {}) if self.etf_panel is not None else {}
        eod_state = {}
        if self.eod_service is not None:
            eod_state = self.eod_service._get_cycle_state()  # noqa: SLF001
        alert_counts = self.alert_service.get_counts() if self.alert_service is not None else {}
        tasks = self.task_service.list_tasks() if self.task_service is not None else []
        cfg = self._auto_trade_config.get_config()
        exception_order_count = self._count_today_exception_orders()
        self._state = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "broker_status": broker_status,
            "qmt_running": bool(broker_status.get("running", False)),
            "broker_connected": bool(getattr(self.broker_service, "is_connected", False)),
            "startup_running": bool(getattr(self.startup_orchestrator, "is_running", False)),
            "eod_state": eod_state,
            "ai_status": ai_status,
            "etf_status": etf_status,
            "alert_counts": alert_counts,
            "exception_order_count": exception_order_count,
            "tasks": tasks,
            "auto_trade_mode": cfg.auto_trade_mode,
            "manual_orders_enabled": bool(cfg.manual_orders_enabled),
        }
        self.state_changed.emit(dict(self._state))

    def _count_today_exception_orders(self) -> int:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            records = self._trade_service.get_order_records(
                start_time=f"{today} 00:00:00",
                end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                include_archived=False,
                limit=500,
            )
        except Exception:
            return 0
        return sum(
            1 for item in records
            if str(getattr(item, "status", "") or "").strip().lower() in _EXCEPTION_ORDER_STATUSES
        )

    def get_state(self) -> dict:
        return dict(self._state)

    # ------------------------------------------------------------------
    #  未管理账户对账：broker 连接成功/EOD 时把券商里"无主"现金和持仓
    #  同步进 unmanaged 虚拟账户，保证主账本 ≡ 券商实况
    # ------------------------------------------------------------------

    def _on_broker_connection_changed(self, *args) -> None:
        self.refresh_state()
        connected = bool(getattr(self.broker_service, "is_connected", False))
        if connected and not self._last_broker_connected:
            QTimer.singleShot(3000, lambda: self._reconcile_unmanaged_account(source="broker_connected"))
        self._last_broker_connected = connected

    def _on_eod_cycle_finished(self, *args) -> None:
        self.refresh_state()
        self._reconcile_unmanaged_account(source="eod", force=True)

    def _reconcile_unmanaged_account(self, *, source: str = "", force: bool = False) -> None:
        if self.broker_service is None:
            return
        if not getattr(self.broker_service, "is_connected", False):
            return
        now = time.monotonic()
        if not force and (now - self._last_unmanaged_reconcile_ts) < _UNMANAGED_RECONCILE_MIN_INTERVAL_SEC:
            return
        self._last_unmanaged_reconcile_ts = now
        try:
            asset = self.broker_service.query_stock_asset()
            broker_cash = float(getattr(asset, "cash", 0.0) or 0.0)
        except Exception as exc:
            logger.warning("[%s] 查询券商资产失败，unmanaged 对账跳过: %s", source or "reconcile", exc)
            return
        try:
            raw_positions = self.broker_service.query_stock_positions() or []
        except Exception as exc:
            logger.warning("[%s] 查询券商持仓失败，unmanaged 对账跳过: %s", source or "reconcile", exc)
            return
        broker_positions = []
        for pos in raw_positions:
            try:
                volume = int(getattr(pos, "volume", 0) or 0)
                if volume <= 0:
                    continue
                broker_positions.append({
                    "stock_code": str(getattr(pos, "stock_code", "") or ""),
                    "volume": volume,
                    "open_price": float(getattr(pos, "open_price", 0.0) or 0.0),
                })
            except Exception:
                continue
        try:
            summary = self._budget_service.reconcile_unmanaged_with_broker(
                broker_cash=broker_cash,
                broker_positions=broker_positions,
            )
            logger.info(
                "[%s] unmanaged 账户对账: broker_cash=%.2f claimed=%.2f unmanaged_cash=%.2f positions=%d",
                source or "reconcile",
                summary.get("broker_cash", 0.0),
                summary.get("claimed_cash", 0.0),
                summary.get("unmanaged_cash", 0.0),
                summary.get("unmanaged_position_count", 0),
            )
        except Exception as exc:
            logger.warning("[%s] unmanaged 账户对账执行失败: %s", source or "reconcile", exc)
