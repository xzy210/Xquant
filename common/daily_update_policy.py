from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from common.data_portal import DailyDataStatus, DataPortal, get_data_portal


@dataclass(frozen=True)
class DailyUpdateWindow:
    """Unified daily-bar fetch window."""

    start_date: str
    end_date: str
    expected_date: str
    full_update: bool = False
    latest_local_date: Optional[str] = None


@dataclass(frozen=True)
class DailyHistoryPrecheckResult:
    """Result of a daily history source precheck."""

    ok: bool
    message: str


class DailyUpdatePolicy:
    """Shared skeleton for local daily-bar update and freshness decisions."""

    DEFAULT_START_DATES = {
        "stock": "20190101",
        "etf": "20190101",
        "index": "19900101",
        "auto": "20190101",
    }

    def __init__(self, data_portal: Optional[DataPortal] = None) -> None:
        self.data_portal = data_portal or get_data_portal()

    def expected_trading_day(self, now: Optional[datetime] = None) -> date:
        """Return the latest daily K-line date expected to be available."""
        return self.data_portal.latest_expected_trading_day(now)

    @staticmethod
    def is_intraday_check_window(now: Optional[datetime] = None) -> bool:
        """Return whether realtime/intraday freshness checks should run."""
        from trading_app.services.market_data_policy import is_intraday_check_window

        return is_intraday_check_window(now)

    @staticmethod
    def intraday_expected_cutoff(now: Optional[datetime] = None) -> datetime:
        """Return the expected latest minute-bar cutoff for the current session."""
        from trading_app.services.market_data_policy import intraday_expected_cutoff

        return intraday_expected_cutoff(now)

    def today_fetch_end(self, now: Optional[datetime] = None) -> str:
        """Return the inclusive fetch end date used by xtquant/tushare helpers."""
        current = now or datetime.now()
        return current.strftime("%Y%m%d")

    def resolve_fetch_window(
        self,
        *,
        asset_type: str = "auto",
        explicit_start: Optional[str] = None,
        default_start: Optional[str] = None,
        full_update: bool = False,
        local_path: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> DailyUpdateWindow:
        """Build a consistent daily fetch window for full or incremental updates."""
        resolved_type = (asset_type or "auto").strip().lower()
        start = (explicit_start or "").strip() or default_start or self.DEFAULT_START_DATES.get(resolved_type, "20190101")
        latest_local_date = None
        if not full_update and local_path is not None and not explicit_start:
            latest_local_date = self.latest_local_fetch_date(local_path)
            if latest_local_date:
                start = latest_local_date
        expected = self.expected_trading_day(now).strftime("%Y-%m-%d")
        return DailyUpdateWindow(
            start_date=start,
            end_date=self.today_fetch_end(now),
            expected_date=expected,
            full_update=full_update,
            latest_local_date=latest_local_date,
        )

    @staticmethod
    def latest_local_fetch_date(parquet_path: Path) -> Optional[str]:
        """Return local parquet max date as YYYYMMDD for incremental fetches."""
        path = Path(parquet_path)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path, columns=["date"])
        except Exception:
            return None
        if df.empty or "date" not in df.columns:
            return None
        latest = pd.to_datetime(df["date"], errors="coerce").dropna().max()
        if pd.isna(latest):
            return None
        return pd.Timestamp(latest).strftime("%Y%m%d")

    def daily_status(
        self,
        symbol: str,
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        data_path: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> DailyDataStatus:
        """Return normalized daily metadata for a symbol or explicit parquet file."""
        normalized = self.data_portal.normalize_symbol(symbol)
        if data_path is not None:
            return self.data_portal.get_daily_file_metadata(
                Path(data_path),
                symbol=normalized,
                asset_type=asset_type,
                data_dir=data_dir,
                now=now,
            )
        return self.data_portal.get_daily_metadata(
            normalized,
            asset_type=asset_type,
            data_dir=data_dir,
            now=now,
        )

    def check_daily_freshness(
        self,
        symbol: str,
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        data_path: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """Return legacy freshness tuple backed by DataPortal metadata."""
        status = self.daily_status(
            symbol,
            asset_type=asset_type,
            data_dir=data_dir,
            data_path=data_path,
            now=now,
        )
        return status.is_fresh, self.data_portal.format_daily_status_message(status)

    def check_daily_freshness_map(
        self,
        symbols: Iterable[str],
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Tuple[bool, str]]:
        """Return legacy freshness tuples for multiple symbols."""
        return {
            self.data_portal.normalize_symbol(symbol): self.check_daily_freshness(
                symbol,
                asset_type=asset_type,
                data_dir=data_dir,
                now=now,
            )
            for symbol in symbols
        }

    def find_stale_symbols(
        self,
        symbols: Iterable[str],
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> List[str]:
        """Return symbols whose local daily bars are not fresh."""
        stale: List[str] = []
        for symbol, (fresh, _message) in self.check_daily_freshness_map(
            symbols,
            asset_type=asset_type,
            data_dir=data_dir,
            now=now,
        ).items():
            if not fresh:
                stale.append(symbol)
        return stale

    def validate_daily_outputs(
        self,
        symbols: Iterable[str],
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> List[str]:
        """Return formatted stale output items after an update run."""
        stale_items: List[str] = []
        for symbol in symbols:
            normalized = self.data_portal.normalize_symbol(symbol)
            fresh, info = self.check_daily_freshness(
                normalized,
                asset_type=asset_type,
                data_dir=data_dir,
                now=now,
            )
            if not fresh:
                stale_items.append(f"{normalized}: {info}")
        return stale_items

    @staticmethod
    def format_stale_items(label: str, stale_items: List[str], unit: str) -> str:
        """Format a compact stale-output diagnostic message."""
        preview = "；".join(stale_items[:8])
        suffix = f"；另有 {len(stale_items) - 8} {unit}" if len(stale_items) > 8 else ""
        return f"{label}更新后仍未达到最新交易日: {preview}{suffix}"

    @staticmethod
    def run_daily_history_precheck(
        checker: Callable[[], Tuple[bool, str]],
        *,
        action_hint: str,
    ) -> DailyHistoryPrecheckResult:
        """Run the shared miniQMT daily-history precheck wrapper."""
        ok, msg = checker()
        if ok:
            return DailyHistoryPrecheckResult(True, msg)
        return DailyHistoryPrecheckResult(
            False,
            "miniQMT 历史K线数据源异常：连接可能正常，但无法拉取到最新交易日日线。"
            f"{msg}。{action_hint}",
        )


_daily_update_policy: Optional[DailyUpdatePolicy] = None


def get_daily_update_policy() -> DailyUpdatePolicy:
    """Return the process-wide daily update policy."""
    global _daily_update_policy
    if _daily_update_policy is None:
        _daily_update_policy = DailyUpdatePolicy()
    return _daily_update_policy


def set_daily_update_policy(policy: Optional[DailyUpdatePolicy]) -> None:
    """Replace the process-wide daily update policy, mainly for tests."""
    global _daily_update_policy
    _daily_update_policy = policy


__all__ = [
    "DailyHistoryPrecheckResult",
    "DailyUpdatePolicy",
    "DailyUpdateWindow",
    "get_daily_update_policy",
    "set_daily_update_policy",
]
