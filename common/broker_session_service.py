"""
Shared broker session service for miniQMT / xtquant.
"""
from __future__ import annotations

import json
import logging
import random
import threading
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from common.io_utils import atomic_write_json

logger = logging.getLogger(__name__)


class _BrokerConnectWorker(QThread):
    """Background connector for miniQMT."""

    connected = pyqtSignal(object, object, str, str)  # xt_trader, acc, qmt_path, account
    failed = pyqtSignal(str)
    log_message = pyqtSignal(str)

    def __init__(self, qmt_path: str, account: str, parent=None):
        super().__init__(parent)
        self.qmt_path = qmt_path
        self.account = account

    def _log(self, message: str):
        logger.info(message)
        self.log_message.emit(message)

    def run(self):
        try:
            from xtquant import xttrader
            from xtquant.xttype import StockAccount

            session_id = int(random.randint(100000, 999999))
            self._log(f"正在连接 miniQMT: {self.qmt_path} / {self.account}")

            xt_trader = xttrader.XtQuantTrader(self.qmt_path, session_id)
            xt_trader.start()

            result = xt_trader.connect()
            if result != 0:
                self.failed.emit("连接 QMT 交易端失败，请确认 miniQMT 已启动并登录")
                return

            acc = StockAccount(self.account)
            res = xt_trader.subscribe(acc)
            if res != 0:
                try:
                    xt_trader.stop()
                except Exception:
                    pass
                self.failed.emit(f"订阅账户失败（返回码 {res}），请检查资金账号")
                return

            self._log("券商连接成功")
            self.connected.emit(xt_trader, acc, self.qmt_path, self.account)
        except ImportError:
            self.failed.emit("未找到 xtquant 库，请确认已安装 miniQMT 并激活对应 Python 环境")
        except Exception as exc:
            self.failed.emit(f"连接异常: {exc}")


class BrokerSessionService(QObject):
    """
    Shared broker session singleton.

    It owns the xtquant trader/account lifecycle and exposes thin query/order APIs.
    """

    connection_changed = pyqtSignal(bool, str)
    log_message = pyqtSignal(str)
    config_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._xt_trader = None
        self._acc = None
        self._connected = False
        self._connect_worker: Optional[_BrokerConnectWorker] = None
        self._lock = threading.RLock()

        project_root = Path(__file__).resolve().parent.parent
        self._primary_config_path = project_root / "trading_app" / "config" / "broker_config.json"
        self._fallback_config_paths = [
            project_root / "live_rotation" / "config" / "broker_settings.json",
            project_root / "live_rotation" / "config" / "broker_config.json",
        ]
        self._last_config = self.load_config()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._xt_trader is not None and self._acc is not None

    @property
    def xt_trader(self):
        return self._xt_trader

    @property
    def account_obj(self):
        return self._acc

    def _emit_log(self, message: str):
        logger.info(message)
        self.log_message.emit(message)

    def load_config(self) -> dict:
        for path in [self._primary_config_path, *self._fallback_config_paths]:
            if path.exists():
                try:
                    data = json.loads(path.read_text("utf-8"))
                    return {
                        "qmt_path": data.get("qmt_path", ""),
                        "account": data.get("account", ""),
                    }
                except Exception as exc:
                    logger.warning("读取券商配置失败 %s: %s", path, exc)
        return {"qmt_path": "", "account": ""}

    def save_config(self, qmt_path: str, account: str) -> None:
        data = {
            "qmt_path": qmt_path.strip(),
            "account": account.strip(),
        }
        atomic_write_json(self._primary_config_path, data)
        self._last_config = data
        self.config_changed.emit(dict(data))

    def get_config(self) -> dict:
        return dict(self._last_config)

    def connect_async(self, qmt_path: str, account: str) -> bool:
        qmt_path = qmt_path.strip()
        account = account.strip()
        if not qmt_path or not account:
            self.connection_changed.emit(False, "请先填写 miniQMT 路径和资金账号")
            return False

        if self.is_connected:
            current = self._last_config
            if current.get("qmt_path") == qmt_path and current.get("account") == account:
                self.connection_changed.emit(True, "券商会话已连接")
                return True
            self.disconnect()

        if self._connect_worker and self._connect_worker.isRunning():
            self.connection_changed.emit(False, "券商连接正在进行中")
            return False

        self._emit_log("开始建立共享券商会话...")
        self._connect_worker = _BrokerConnectWorker(qmt_path, account, parent=self)
        self._connect_worker.connected.connect(self._on_connected)
        self._connect_worker.failed.connect(self._on_connect_failed)
        self._connect_worker.log_message.connect(self.log_message.emit)
        self._connect_worker.start()
        self.connection_changed.emit(False, "正在连接券商...")
        return True

    def _on_connected(self, xt_trader, acc, qmt_path: str, account: str):
        with self._lock:
            self._xt_trader = xt_trader
            self._acc = acc
            self._connected = True
            self._last_config = {"qmt_path": qmt_path, "account": account}
        self.save_config(qmt_path, account)
        self.connection_changed.emit(True, "券商连接成功")

    def _on_connect_failed(self, message: str):
        with self._lock:
            self._xt_trader = None
            self._acc = None
            self._connected = False
        self.connection_changed.emit(False, message)

    def disconnect(self):
        with self._lock:
            trader = self._xt_trader
            self._xt_trader = None
            self._acc = None
            self._connected = False
        if trader is not None:
            try:
                trader.stop()
            except Exception as exc:
                logger.warning("停止券商连接失败: %s", exc)
        self.connection_changed.emit(False, "券商已断开")

    def _require_connected(self):
        if not self.is_connected:
            raise RuntimeError("券商未连接")
        return self._xt_trader, self._acc

    def query_stock_asset(self):
        trader, acc = self._require_connected()
        return trader.query_stock_asset(acc)

    def query_stock_positions(self):
        trader, acc = self._require_connected()
        return trader.query_stock_positions(acc)

    def query_stock_orders(self):
        trader, acc = self._require_connected()
        return trader.query_stock_orders(acc)

    def query_stock_trades(self):
        trader, acc = self._require_connected()
        return trader.query_stock_trades(acc)

    def query_stock_order(self, order_id: int):
        trader, acc = self._require_connected()
        if hasattr(trader, "query_stock_order"):
            return trader.query_stock_order(acc, order_id)
        orders = trader.query_stock_orders(acc) or []
        for order in orders:
            if getattr(order, "order_id", None) == order_id:
                return order
        return None

    def query_stock_deals(self):
        trader, acc = self._require_connected()
        for method_name in ("query_stock_deal", "query_stock_deals"):
            if hasattr(trader, method_name):
                return getattr(trader, method_name)(acc)
        return []

    def order_stock(
        self,
        stock_code: str,
        order_type: int,
        order_volume: int,
        price_type: int,
        price: float,
        strategy_name: str = "",
        remark: str = "",
    ):
        trader, acc = self._require_connected()
        return trader.order_stock(
            acc,
            stock_code,
            order_type,
            order_volume,
            price_type,
            price,
            strategy_name,
            remark,
        )

    def cancel_order_stock(self, order_id: int):
        trader, acc = self._require_connected()
        return trader.cancel_order_stock(acc, order_id)


_broker_session_service: Optional[BrokerSessionService] = None


def get_broker_session_service() -> BrokerSessionService:
    global _broker_session_service
    if _broker_session_service is None:
        _broker_session_service = BrokerSessionService()
    return _broker_session_service
