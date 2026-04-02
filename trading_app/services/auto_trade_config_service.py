from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "auto_trade_config.json"
_VALID_AUTO_MODES = {"off", "paper", "shadow", "live"}


@dataclass
class AutoTradeConfig:
    manual_orders_enabled: bool = True
    auto_trade_mode: str = "off"
    require_trading_time: bool = True
    duplicate_window_seconds: int = 30
    status_poll_seconds: float = 6.0
    status_poll_interval_seconds: float = 1.0
    max_new_positions_per_day: int = 2
    max_buy_orders_per_day: int = 2
    max_sell_orders_per_day: int = 6
    reserve_cash_pct: float = 0.20
    max_intraday_failures: int = 2
    max_daily_loss_pct: float = 0.02
    auto_reconcile_enabled: bool = True
    reconcile_time: str = "15:10"

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "AutoTradeConfig":
        source = dict(data or {})
        mode = str(source.get("auto_trade_mode", "off") or "off").strip().lower()
        if mode not in _VALID_AUTO_MODES:
            mode = "off"
        return cls(
            manual_orders_enabled=bool(source.get("manual_orders_enabled", True)),
            auto_trade_mode=mode,
            require_trading_time=bool(source.get("require_trading_time", True)),
            duplicate_window_seconds=max(int(source.get("duplicate_window_seconds", 30) or 30), 1),
            status_poll_seconds=max(float(source.get("status_poll_seconds", 6.0) or 6.0), 0.5),
            status_poll_interval_seconds=max(float(source.get("status_poll_interval_seconds", 1.0) or 1.0), 0.2),
            max_new_positions_per_day=max(int(source.get("max_new_positions_per_day", 2) or 2), 0),
            max_buy_orders_per_day=max(int(source.get("max_buy_orders_per_day", 2) or 2), 0),
            max_sell_orders_per_day=max(int(source.get("max_sell_orders_per_day", 6) or 6), 0),
            reserve_cash_pct=min(max(float(source.get("reserve_cash_pct", 0.20) or 0.20), 0.0), 0.95),
            max_intraday_failures=max(int(source.get("max_intraday_failures", 2) or 2), 1),
            max_daily_loss_pct=min(max(float(source.get("max_daily_loss_pct", 0.02) or 0.02), 0.0), 1.0),
            auto_reconcile_enabled=bool(source.get("auto_reconcile_enabled", True)),
            reconcile_time=str(source.get("reconcile_time", "15:10") or "15:10").strip(),
        )

    def to_dict(self) -> dict:
        return asdict(self)


class AutoTradeConfigService:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or _CONFIG_PATH
        self._config = self._load()

    def _load(self) -> AutoTradeConfig:
        try:
            if self.config_path.exists():
                return AutoTradeConfig.from_dict(json.loads(self.config_path.read_text("utf-8")))
        except Exception as exc:
            logger.warning("读取自动交易配置失败: %s", exc)
        return AutoTradeConfig()

    def get_config(self) -> AutoTradeConfig:
        self._config = self._load()
        return self._config

    def get_mode(self) -> str:
        return self.get_config().auto_trade_mode


_auto_trade_config_service: Optional[AutoTradeConfigService] = None


def get_auto_trade_config_service() -> AutoTradeConfigService:
    global _auto_trade_config_service
    if _auto_trade_config_service is None:
        _auto_trade_config_service = AutoTradeConfigService()
    return _auto_trade_config_service
