from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from typing import Optional

from live_rotation.holiday_calendar import is_trading_day

REALTIME_MAX_AGE_SECONDS = 90

MORNING_SESSION_START = dt_time(9, 30)
MORNING_SESSION_END = dt_time(11, 30)
AFTERNOON_SESSION_START = dt_time(13, 0)
AFTERNOON_SESSION_END = dt_time(15, 0)


@dataclass(frozen=True)
class TickFreshness:
    source_time: Optional[datetime]
    age_seconds: Optional[float]
    is_fresh: bool
    reason: str = ""


def normalize_symbol_code(code: str) -> str:
    value = str(code or "").strip().upper()
    return value.split(".", 1)[0] if "." in value else value


def is_etf_like_code(code: str) -> bool:
    normalized = normalize_symbol_code(code).zfill(6)
    return normalized.startswith(("15", "16", "18", "51", "52", "53", "55", "56", "58"))


def previous_trading_day(d: Optional[date] = None) -> date:
    current = (d or date.today()) - timedelta(days=1)
    while not is_trading_day(current):
        current -= timedelta(days=1)
    return current


def latest_expected_trading_day(now: Optional[datetime] = None) -> date:
    """Return the latest daily K-line date that should be available locally."""
    current_time = now or datetime.now()
    current_date = current_time.date()
    if not is_trading_day(current_date):
        return previous_trading_day(current_date)
    if current_time.time() < AFTERNOON_SESSION_END:
        return previous_trading_day(current_date)
    return current_date


def is_trading_session(now: Optional[datetime] = None) -> bool:
    current_time = now or datetime.now()
    if not is_trading_day(current_time.date()):
        return False
    current = current_time.time()
    return (
        MORNING_SESSION_START <= current <= MORNING_SESSION_END
        or AFTERNOON_SESSION_START <= current <= AFTERNOON_SESSION_END
    )


def is_intraday_check_window(now: Optional[datetime] = None) -> bool:
    return is_trading_session(now)


def intraday_expected_cutoff(now: Optional[datetime] = None) -> datetime:
    current_time = now or datetime.now()
    current = current_time.time()
    if current <= MORNING_SESSION_END:
        session_start = current_time.replace(hour=9, minute=30, second=0, microsecond=0)
    else:
        session_start = current_time.replace(hour=13, minute=0, second=0, microsecond=0)
    return max(session_start, current_time - timedelta(minutes=15))


def can_use_daily_fallback(now: Optional[datetime] = None) -> bool:
    return not is_trading_session(now)


def parse_tick_datetime(value) -> Optional[datetime]:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        ivalue = int(value)
        if ivalue <= 0:
            return None
        if ivalue >= 10**12:
            return datetime.fromtimestamp(ivalue / 1000)
        return datetime.fromtimestamp(ivalue)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if value.isdigit():
            return parse_tick_datetime(int(value))
        for fmt in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def extract_tick_datetime(tick: dict) -> Optional[datetime]:
    if not isinstance(tick, dict):
        return None
    for key in ("timetag", "time"):
        tick_time = parse_tick_datetime(tick.get(key))
        if tick_time is not None:
            return tick_time
    return None


def evaluate_tick_freshness(
    tick: dict,
    received_time: Optional[datetime] = None,
    *,
    max_age_seconds: int = REALTIME_MAX_AGE_SECONDS,
    allow_missing_time_outside_session_with_volume: bool = False,
) -> TickFreshness:
    now = received_time or datetime.now()
    source_time = extract_tick_datetime(tick)
    if source_time is None:
        if allow_missing_time_outside_session_with_volume and not is_trading_session(now):
            last_price = float((tick or {}).get("lastPrice") or 0.0) if isinstance(tick, dict) else 0.0
            volume = float((tick or {}).get("volume") or 0.0) if isinstance(tick, dict) else 0.0
            if last_price > 0 and volume > 0:
                return TickFreshness(None, None, True, "非交易时段无时间戳tick按成交量兜底")
        return TickFreshness(None, None, False, "tick缺少时间戳")

    age_seconds = max((now - source_time).total_seconds(), 0.0)
    if source_time.date() != now.date():
        return TickFreshness(source_time, age_seconds, False, "tick不属于当前日期")
    if is_trading_session(now) and age_seconds > max_age_seconds:
        return TickFreshness(source_time, age_seconds, False, "tick超过实时freshness阈值")
    return TickFreshness(source_time, age_seconds, True, "tick新鲜")


def is_tick_fresh(tick: dict, now: Optional[datetime] = None, *, max_age_seconds: int = REALTIME_MAX_AGE_SECONDS) -> bool:
    return evaluate_tick_freshness(tick, now, max_age_seconds=max_age_seconds).is_fresh
