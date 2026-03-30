"""决策止损/目标价实时监控

对已执行的决策持续监控，当价格触及止损价或目标价时发出信号和通知。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger(__name__)


class DecisionAlertMonitor(QObject):
    """Monitors executed decisions for stop-loss / target-price triggers."""

    alert_triggered = pyqtSignal(str, str, str)  # record_id, alert_type, message

    POLL_INTERVAL_MS = 30_000  # 30 seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watched: Dict[str, Dict[str, Any]] = {}
        self._broker = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_alerts)
        self._triggered_set: set = set()

    def start(self):
        if not self._timer.isActive():
            self._timer.start(self.POLL_INTERVAL_MS)
            logger.info("DecisionAlertMonitor started (interval=%ds)", self.POLL_INTERVAL_MS // 1000)

    def stop(self):
        self._timer.stop()

    def is_running(self) -> bool:
        return self._timer.isActive()

    def watch_decision(
        self,
        record_id: str,
        symbol_code: str,
        symbol_name: str,
        stop_loss_price: float,
        target_price: float,
        invalidation: str = "",
    ):
        self._watched[record_id] = {
            "symbol_code": symbol_code,
            "symbol_name": symbol_name,
            "stop_loss_price": stop_loss_price,
            "target_price": target_price,
            "invalidation": invalidation,
            "added_at": datetime.now().isoformat(),
        }
        if not self._timer.isActive():
            self.start()

    def unwatch(self, record_id: str):
        self._watched.pop(record_id, None)
        if not self._watched:
            self.stop()

    def clear(self):
        self._watched.clear()
        self._triggered_set.clear()
        self.stop()

    def watched_count(self) -> int:
        return len(self._watched)

    def _get_broker(self):
        if self._broker is None:
            try:
                from common.broker_session_service import get_broker_session_service
            except ImportError:
                from trading_app.common.broker_session_service import get_broker_session_service
            self._broker = get_broker_session_service()
        return self._broker

    def _check_alerts(self):
        broker = self._get_broker()
        if not broker.is_connected:
            return

        try:
            positions = broker.query_stock_positions() or []
        except Exception:
            return

        price_map: Dict[str, float] = {}
        for pos in positions:
            code = getattr(pos, "stock_code", "") or ""
            price = float(getattr(pos, "market_price", 0) or 0)
            if code and price > 0:
                price_map[code] = price

        for record_id, info in list(self._watched.items()):
            code = info["symbol_code"]
            name = info["symbol_name"]
            current_price = price_map.get(code, 0)
            if current_price <= 0:
                continue

            stop_loss = info["stop_loss_price"]
            target = info["target_price"]

            trigger_key_sl = f"{record_id}:stop_loss"
            trigger_key_tg = f"{record_id}:target_hit"

            if stop_loss > 0 and current_price <= stop_loss and trigger_key_sl not in self._triggered_set:
                self._triggered_set.add(trigger_key_sl)
                msg = (
                    f"{name}({code}) 当前价 {current_price:.2f} 已触及止损价 {stop_loss:.2f}，"
                    f"建议立即关注并考虑执行止损。"
                )
                self.alert_triggered.emit(record_id, "stop_loss", msg)
                self._try_notify(code, name, "stop_loss", msg)

            if target > 0 and current_price >= target and trigger_key_tg not in self._triggered_set:
                self._triggered_set.add(trigger_key_tg)
                msg = (
                    f"{name}({code}) 当前价 {current_price:.2f} 已到达目标价 {target:.2f}，"
                    f"建议考虑止盈或调整策略。"
                )
                self.alert_triggered.emit(record_id, "target_hit", msg)
                self._try_notify(code, name, "target_hit", msg)

    @staticmethod
    def _try_notify(code: str, name: str, alert_type: str, message: str):
        try:
            from services.ai_decision_notifier import notify_alert
        except ImportError:
            try:
                from trading_app.services.ai_decision_notifier import notify_alert
            except ImportError:
                return
        try:
            notify_alert(code, name, alert_type, message)
        except Exception as exc:
            logger.debug("Alert notification failed: %s", exc)
