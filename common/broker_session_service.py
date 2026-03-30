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

from common.credential_store import DEFAULT_SERVICE_NAME, delete_password, save_password
from common.io_utils import atomic_write_json
from common.qmt_client_service import QmtClientService

logger = logging.getLogger(__name__)


class _BrokerConnectWorker(QThread):
    """Background connector for miniQMT."""

    connected = pyqtSignal(object, object, str, str)  # xt_trader, acc, qmt_path, account
    failed = pyqtSignal(str)
    log_message = pyqtSignal(str)

    def __init__(self, config: dict[str, Any], parent=None):
        super().__init__(parent)
        self.config = dict(config or {})
        self.qmt_path = str(self.config.get("qmt_path", "") or "").strip()
        self.account = str(self.config.get("account", "") or "").strip()

    def _log(self, message: str):
        logger.info(message)
        self.log_message.emit(message)

    def run(self):
        try:
            client_service = QmtClientService(self.config)
            ready, ready_message = client_service.ensure_ready(status_callback=self._log)
            self._log(ready_message)
            if not ready:
                self.failed.emit(ready_message)
                return

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
    client_state_changed = pyqtSignal(dict)

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

    def _normalize_config(self, data: Optional[dict[str, Any]]) -> dict[str, Any]:
        source = dict(data or {})
        return {
            "qmt_path": str(source.get("qmt_path", "") or "").strip(),
            "account": str(source.get("account", "") or "").strip(),
            "qmt_exe_path": str(source.get("qmt_exe_path", "") or "").strip(),
            "login_username": str(source.get("login_username", "") or "").strip(),
            "login_password": str(source.get("login_password", "") or "").strip(),
            "credential_service": str(source.get("credential_service", "") or DEFAULT_SERVICE_NAME).strip() or DEFAULT_SERVICE_NAME,
            "password_stored": bool(source.get("password_stored", False)),
            "auto_launch": bool(source.get("auto_launch", True)),
            "auto_login": bool(source.get("auto_login", False)),
            "window_title_hint": str(source.get("window_title_hint", "") or "").strip(),
            "process_name": str(source.get("process_name", "") or "").strip(),
            "login_button_rel_x": float(source.get("login_button_rel_x", 0.38) or 0.38),
            "login_button_rel_y": float(source.get("login_button_rel_y", 0.855) or 0.855),
        }

    def load_config(self) -> dict:
        for path in [self._primary_config_path, *self._fallback_config_paths]:
            if path.exists():
                try:
                    data = json.loads(path.read_text("utf-8"))
                    return self._normalize_config(data)
                except Exception as exc:
                    logger.warning("读取券商配置失败 %s: %s", path, exc)
        return self._normalize_config({})

    def save_config(self, qmt_path: str | dict[str, Any], account: str = "", **extra: Any) -> None:
        if isinstance(qmt_path, dict):
            merged = dict(self._last_config)
            merged.update(qmt_path)
            data = self._normalize_config(merged)
        else:
            merged = dict(self._last_config)
            merged.update(extra)
            merged.update({
                "qmt_path": qmt_path.strip(),
                "account": account.strip(),
            })
            data = self._normalize_config(merged)

        username = data.get("login_username", "")
        service_name = data.get("credential_service", DEFAULT_SERVICE_NAME)
        raw_password = data.get("login_password", "")
        clear_password = bool(extra.get("clear_login_password", False)) if not isinstance(qmt_path, dict) else False

        if clear_password and username:
            delete_password(username, service_name=service_name)
            data["login_password"] = ""
            data["password_stored"] = False
        elif raw_password and username:
            stored = save_password(username, raw_password, service_name=service_name)
            data["password_stored"] = stored
            if stored:
                data["login_password"] = ""
        else:
            data["password_stored"] = bool(data.get("password_stored", False))

        atomic_write_json(self._primary_config_path, data)
        self._last_config = data
        self.config_changed.emit(dict(data))
        self.client_state_changed.emit(self.get_client_status())

    def get_config(self) -> dict:
        self.reload_config()
        return dict(self._last_config)

    def reload_config(self) -> dict:
        latest = self.load_config()
        if latest != self._last_config:
            self._last_config = latest
            self.config_changed.emit(dict(latest))
        return dict(self._last_config)

    def connect_async(self, qmt_path: str, account: str) -> bool:
        self.reload_config()
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
        connect_config = dict(self._last_config)
        connect_config.update({
            "qmt_path": qmt_path,
            "account": account,
        })
        self._connect_worker = _BrokerConnectWorker(connect_config, parent=self)
        self._connect_worker.connected.connect(self._on_connected)
        self._connect_worker.failed.connect(self._on_connect_failed)
        self._connect_worker.log_message.connect(self.log_message.emit)
        self._connect_worker.start()
        self.connection_changed.emit(False, "正在连接券商...")
        self.client_state_changed.emit(self.get_client_status())
        return True

    def _on_connected(self, xt_trader, acc, qmt_path: str, account: str):
        with self._lock:
            self._xt_trader = xt_trader
            self._acc = acc
            self._connected = True
            self._last_config.update({"qmt_path": qmt_path, "account": account})
        self.save_config(dict(self._last_config))
        self.connection_changed.emit(True, "券商连接成功")
        self.client_state_changed.emit(self.get_client_status())

    def _on_connect_failed(self, message: str):
        with self._lock:
            self._xt_trader = None
            self._acc = None
            self._connected = False
        self.connection_changed.emit(False, message)
        self.client_state_changed.emit(self.get_client_status())

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
        self.client_state_changed.emit(self.get_client_status())

    def get_client_status(self) -> dict:
        self.reload_config()
        status = QmtClientService(self._last_config).get_status().to_dict()
        if self.is_connected:
            status["message"] = "xtquant 已连接"
            status["ready"] = True
        return status

    def launch_client(self) -> tuple[bool, str, dict]:
        self.reload_config()
        client_service = QmtClientService(self._last_config)
        if bool(self._last_config.get("auto_login", False)):
            ok, message = client_service.launch_and_login(status_callback=self._emit_log)
        else:
            ok, message = client_service.launch(status_callback=self._emit_log)
        status = self.get_client_status()
        self.client_state_changed.emit(status)
        return ok, message, status

    def login_client(self) -> tuple[bool, str, dict]:
        self.reload_config()
        ok, message = QmtClientService(self._last_config).login(status_callback=self._emit_log)
        status = self.get_client_status()
        self.client_state_changed.emit(status)
        return ok, message, status

    def close_client(self) -> tuple[bool, str, dict]:
        self.reload_config()
        ok, message = QmtClientService(self._last_config).close(status_callback=self._emit_log)
        status = self.get_client_status()
        self.client_state_changed.emit(status)
        return ok, message, status

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
