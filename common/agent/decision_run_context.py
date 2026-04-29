from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any, Callable, Dict, Optional

TradingDayResolver = Callable[[date], bool]
_trading_day_resolver: TradingDayResolver | None = None


def set_trading_day_resolver(resolver: TradingDayResolver | None) -> None:
    global _trading_day_resolver
    _trading_day_resolver = resolver


def _is_trading_day(day: date) -> bool:
    if _trading_day_resolver is not None:
        return bool(_trading_day_resolver(day))
    return day.weekday() < 5


def _previous_trading_day(base_day: date) -> date:
    current = base_day - timedelta(days=1)
    while not _is_trading_day(current):
        current -= timedelta(days=1)
    return current


def latest_completed_trading_day(now: Optional[datetime] = None) -> date:
    now = now or datetime.now()
    today = now.date()
    if not _is_trading_day(today):
        return _previous_trading_day(today)
    if now.time() < dt_time(15, 0):
        return _previous_trading_day(today)
    return today


def resolve_session_phase(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    if not _is_trading_day(now.date()):
        return "offday"
    current = now.time()
    if current < dt_time(9, 25):
        return "premarket"
    if dt_time(9, 25) <= current <= dt_time(11, 30):
        return "intraday"
    if dt_time(11, 30) < current < dt_time(13, 0):
        return "midday_break"
    if dt_time(13, 0) <= current <= dt_time(15, 0):
        return "intraday"
    return "postmarket"


@dataclass
class DecisionRunContext:
    run_at: str = ""
    trading_day: str = ""
    is_trading_day: bool = False
    session_phase: str = ""
    prefer_realtime: bool = False
    daily_bar_as_of: str = ""
    realtime_as_of: str = ""

    @property
    def is_intraday(self) -> bool:
        return self.session_phase == "intraday"

    @property
    def should_try_realtime(self) -> bool:
        return bool(self.prefer_realtime and self.session_phase in {"intraday", "postmarket"})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "DecisionRunContext":
        payload = dict(payload or {})
        known = {key: payload.get(key) for key in cls.__dataclass_fields__.keys()}
        return cls(**known)


def build_decision_run_context(
    *,
    now: Optional[datetime] = None,
    prefer_realtime: bool = False,
    daily_bar_as_of: str = "",
    realtime_as_of: str = "",
) -> DecisionRunContext:
    now = now or datetime.now()
    completed_day = latest_completed_trading_day(now)
    phase = resolve_session_phase(now)
    realtime_value = realtime_as_of
    if not realtime_value and prefer_realtime and phase in {"intraday", "postmarket"}:
        realtime_value = now.strftime("%Y-%m-%d %H:%M")
    return DecisionRunContext(
        run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        trading_day=now.strftime("%Y-%m-%d"),
        is_trading_day=_is_trading_day(now.date()),
        session_phase=phase,
        prefer_realtime=bool(prefer_realtime),
        daily_bar_as_of=daily_bar_as_of or completed_day.strftime("%Y-%m-%d"),
        realtime_as_of=realtime_value,
    )


__all__ = [
    "DecisionRunContext",
    "build_decision_run_context",
    "latest_completed_trading_day",
    "resolve_session_phase",
    "set_trading_day_resolver",
]
