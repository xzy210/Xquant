from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, Dict, List, Optional

import pandas as pd


@dataclass(frozen=True)
class BacktestEvent:
    """Event emitted by the unified backtest engine.

    The market-data fields keep the historical per-bar payload shape used by
    strategies, while the optional metadata fields make the same object useful
    for lifecycle, progress, and log events.
    """

    date: Any
    bars: Dict[str, Any]
    history: Dict[str, pd.DataFrame]
    prices: Dict[str, float]
    valid_symbols: list[str]
    daily_factors: Any = None
    primary_symbol: Optional[str] = None
    event_type: str = "bar"
    message: str = ""
    progress_current: Optional[int] = None
    progress_total: Optional[int] = None
    mode: str = ""
    run_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_strategy_payload(self, mode: str) -> dict:
        primary_symbol = self.primary_symbol if self.primary_symbol in self.valid_symbols else None
        primary_symbol = primary_symbol or (self.valid_symbols[0] if self.valid_symbols else None)
        return {
            "mode": mode,
            "date": self.date,
            "code": primary_symbol,
            "primary_symbol": primary_symbol,
            "bars": self.bars,
            "history": self.history,
            "history_slice": self.history.get(primary_symbol) if primary_symbol else None,
            "prices": self.prices,
            "valid_codes": self.valid_symbols,
            "valid_symbols": self.valid_symbols,
            "daily_factors": self.daily_factors,
        }


EventHandler = Callable[[BacktestEvent], None]


class EventBus:
    """Small synchronous pub/sub bus for backtest events."""

    def __init__(self) -> None:
        self._subscribers: Dict[Optional[str], List[EventHandler]] = {}
        self._lock = RLock()
        self.dispatch_errors: list[Exception] = []

    def subscribe(self, handler: EventHandler, event_type: Optional[str] = None) -> Callable[[], None]:
        """Subscribe to all events or one event type and return an unsubscribe function."""
        if not callable(handler):
            raise TypeError("event handler must be callable")
        key = str(event_type) if event_type is not None else None
        with self._lock:
            self._subscribers.setdefault(key, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._subscribers.get(key, [])
                if handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def publish(self, event: BacktestEvent) -> None:
        """Publish an event to exact-type subscribers and wildcard subscribers."""
        if not isinstance(event, BacktestEvent):
            raise TypeError("event must be a BacktestEvent")
        with self._lock:
            handlers = list(self._subscribers.get(None, []))
            handlers.extend(self._subscribers.get(str(event.event_type), []))
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                self.dispatch_errors.append(exc)


__all__ = ["BacktestEvent", "EventBus", "EventHandler"]
