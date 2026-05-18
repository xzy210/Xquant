from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

SUPPORTED_FREQUENCIES = ("1d", "1m", "5m", "15m", "30m", "60m", "1h")
_FREQUENCY_LABELS = {
    "1d": "日线",
    "1m": "1分钟",
    "5m": "5分钟",
    "15m": "15分钟",
    "30m": "30分钟",
    "60m": "60分钟",
    "1h": "小时线",
}


def normalize_frequency(frequency: str) -> str:
    """Normalize UI/CLI aliases to the period used by local files and xtquant."""

    value = str(frequency or "1d").strip().lower()
    aliases = {
        "d": "1d",
        "day": "1d",
        "daily": "1d",
        "60": "60m",
        "h": "60m",
        "1h": "60m",
        "hour": "60m",
        "hourly": "60m",
    }
    normalized = aliases.get(value, value)
    if normalized not in {"1d", "1m", "5m", "15m", "30m", "60m"}:
        raise ValueError(f"不支持的时序策略周期: {frequency}")
    return normalized


def frequency_label(frequency: str) -> str:
    return _FREQUENCY_LABELS.get(str(frequency or ""), str(frequency or ""))


def load_timing_bars(
    data_dir: str | Path,
    symbol: str,
    *,
    frequency: str = "1d",
    start_date: str = "",
    end_date: str = "",
    auto_fetch: bool = True,
    log_callback: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Load bars for timing strategy and fetch from xtquant when local data is absent.

    Intraday timing data is cached under ``data/timing/{frequency}/{symbol}.parquet``
    to avoid mixing 1m/5m/15m/60m bars in the legacy ``data/minute`` cache.
    Every returned frame contains a ``date`` column so the same feature, labeling
    and backtest code can consume any frequency.
    """

    data_root = Path(data_dir)
    code = normalize_symbol(symbol)
    freq = normalize_frequency(frequency)
    start = _normalize_date_arg(start_date)
    end = _normalize_date_arg(end_date)

    local_frame = _standardize_bars(_load_local_bars(data_root, code, freq), freq)
    frame = _filter_dates(local_frame, start_date, end_date)
    missing_coverage = _coverage_missing(local_frame, start_date, end_date)
    if not frame.empty and not missing_coverage:
        return frame

    if not auto_fetch:
        raise FileNotFoundError(_missing_message(data_root, code, freq, start_date, end_date))

    if frame.empty:
        _log(log_callback, f"{code} {freq} 本地无可用数据，尝试通过 xtquant 拉取...")
    else:
        _log(log_callback, f"{code} {freq} 本地缓存未覆盖所选区间，尝试通过 xtquant 补齐...")
    _fetch_xtquant(data_root, code, freq, start, end)
    frame = _load_local_bars(data_root, code, freq)
    frame = _filter_dates(_standardize_bars(frame, freq), start_date, end_date)
    if frame.empty:
        raise FileNotFoundError(f"{code} {freq} 拉取后仍无有效数据，请检查 miniQMT/数据权限/日期区间")
    return frame


def normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    value = value.split(".", 1)[0] if "." in value else value
    return value.zfill(6) if value.isdigit() else value


def _load_local_bars(data_dir: Path, symbol: str, frequency: str) -> pd.DataFrame:
    if frequency == "1d":
        path = data_dir / f"{symbol}.parquet"
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    timing_path = _timing_cache_path(data_dir, symbol, frequency)
    if timing_path.exists():
        return pd.read_parquet(timing_path)

    # Backward-compatible read of the legacy minute cache. This is only used as
    # a fallback; new timing fetches write to data/timing/{frequency}.
    minute_dir = data_dir / "minute" / symbol
    if not minute_dir.exists():
        return pd.DataFrame()
    frames = []
    for path in sorted(minute_dir.glob("*.parquet")):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _standardize_bars(frame: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    data = frame.copy()
    if "date" not in data.columns and "time" in data.columns:
        data["date"] = data["time"]
    if "date" not in data.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    if "time" in data.columns:
        data["time"] = pd.to_datetime(data["time"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in data.columns]
    if missing:
        return pd.DataFrame(columns=required)
    data = data.dropna(subset=["date", "open", "high", "low", "close"])
    data = data.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
    data = _filter_expected_intraday_grid(data, frequency)
    data["frequency"] = frequency
    return data


def _filter_dates(frame: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if frame is None or frame.empty or "date" not in frame.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    data = frame.copy()
    if start_date:
        data = data[data["date"] >= pd.to_datetime(start_date)]
    if end_date:
        # End dates from the UI are day-level; include that whole trading day for intraday bars.
        end_ts = pd.to_datetime(end_date)
        if len(str(end_date).strip()) <= 10:
            end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        data = data[data["date"] <= end_ts]
    return data.reset_index(drop=True)


def _coverage_missing(frame: pd.DataFrame, start_date: str, end_date: str) -> bool:
    """Return True when local cache does not cover the requested range endpoints."""

    if frame is None or frame.empty or "date" not in frame.columns:
        return True
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
    if dates.empty:
        return True

    if start_date:
        start_ts = pd.to_datetime(start_date)
        if dates.min() > start_ts:
            return True
    if end_date:
        end_ts = pd.to_datetime(end_date)
        if len(str(end_date).strip()) <= 10:
            end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        # It is enough to have any bar on the requested end date. For intraday
        # data, the last bar is usually before 15:00 rather than at 23:59:59.
        if dates.max().date() < end_ts.date():
            return True
    return False


def _fetch_xtquant(data_dir: Path, symbol: str, frequency: str, start: str, end: str) -> None:
    try:
        from scripts.fetch_kline_xtquant import _get_kline_xtquant
    except Exception as exc:
        raise RuntimeError(f"无法导入 xtquant 数据拉取模块: {exc}") from exc

    data_dir.mkdir(parents=True, exist_ok=True)
    frame = _get_kline_xtquant(symbol, start, end, frequency)
    if frame is None or frame.empty:
        raise RuntimeError(f"xtquant 未返回 {symbol} {frequency} 数据")
    frame = _standardize_bars(frame, frequency)
    if frame.empty:
        raise RuntimeError(f"xtquant 返回的 {symbol} {frequency} 数据标准化后为空")
    existing = _standardize_bars(_load_exact_cache_bars(data_dir, symbol, frequency), frequency)
    if existing is not None and not existing.empty:
        frame = pd.concat([existing, frame], ignore_index=True)
        frame = frame.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    if frequency == "1d":
        path = data_dir / f"{symbol}.parquet"
    else:
        path = _timing_cache_path(data_dir, symbol, frequency)
        path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _timing_cache_path(data_dir: Path, symbol: str, frequency: str) -> Path:
    return data_dir / "timing" / frequency / f"{symbol}.parquet"


def _load_exact_cache_bars(data_dir: Path, symbol: str, frequency: str) -> pd.DataFrame:
    if frequency == "1d":
        path = data_dir / f"{symbol}.parquet"
    else:
        path = _timing_cache_path(data_dir, symbol, frequency)
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def _filter_expected_intraday_grid(frame: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frame is None or frame.empty or frequency == "1d":
        return frame
    try:
        minutes = int(str(frequency).replace("m", ""))
    except ValueError:
        return frame
    if minutes <= 1 or minutes >= 60:
        return frame
    # xtquant N-minute bars are aligned to natural minute boundaries, e.g.
    # 5m: 09:35/09:40, 15m: 09:45/10:00.  This drops accidental 1m rows from
    # legacy caches before labeling/training.
    aligned = frame["date"].dt.minute % minutes == 0
    session_start = (
        ((frame["date"].dt.hour == 9) & (frame["date"].dt.minute == 30))
        | ((frame["date"].dt.hour == 13) & (frame["date"].dt.minute == 0))
    )
    return frame[aligned & ~session_start].reset_index(drop=True)


def _normalize_date_arg(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return pd.Timestamp.today().strftime("%Y%m%d")
    return text.replace("-", "").replace("/", "")


def _missing_message(data_dir: Path, symbol: str, frequency: str, start_date: str, end_date: str) -> str:
    local_paths: Iterable[Path]
    if frequency == "1d":
        local_paths = [data_dir / f"{symbol}.parquet"]
    else:
        local_paths = [data_dir / "timing" / frequency / f"{symbol}.parquet", data_dir / "minute" / symbol]
    return (
        f"未找到 {symbol} {frequency} 在 {start_date or '-'}~{end_date or '-'} 的本地数据，"
        f"候选路径: {', '.join(str(path) for path in local_paths)}"
    )


def _log(callback: Callable[[str], None] | None, message: str) -> None:
    if callback:
        callback(message)
