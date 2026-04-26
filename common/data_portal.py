"""
Unified data portal MVP.

The MVP intentionally focuses on local daily bars and freshness metadata. It
provides a stable access seam for live strategies and future backtest code while
reusing existing loaders and update logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Union

import pandas as pd

from .data_loader import load_etf_data, load_stock_data

SymbolInput = Union[str, Iterable[str]]

_DAILY_FREQUENCIES = {"1d", "d", "day", "daily"}
_STANDARD_BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class BarsMetadata:
    """Metadata attached to a local bars response."""

    symbol: str
    asset_type: str
    frequency: str
    adjust: str
    data_dir: str
    start: Optional[str]
    end: Optional[str]
    rows: int
    latest_date: Optional[str]
    schema_version: str = "daily_bars.v1"


@dataclass(frozen=True)
class FreshnessStatus:
    """Daily bars freshness status for one symbol."""

    symbol: str
    is_fresh: bool
    latest_date: str
    expected_date: str
    reason: str = ""


@dataclass(frozen=True)
class BarsResult:
    """Bars dataframe plus MVP metadata."""

    data: pd.DataFrame
    metadata: BarsMetadata


class DataPortal:
    """Unified local data access seam for research, backtest, and live services."""

    def __init__(self, *, default_data_dir: Optional[Path] = None) -> None:
        self.default_data_dir = Path(default_data_dir) if default_data_dir is not None else self._project_root() / "data"

    def get_bars(
        self,
        symbols: SymbolInput,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        frequency: str = "1d",
        adjust: str = "qfq",
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        use_cache: bool = True,
    ) -> Dict[str, BarsResult]:
        """Return normalized local daily bars for one or more symbols."""
        self._ensure_daily_frequency(frequency)
        result: Dict[str, BarsResult] = {}
        for symbol in self._normalize_symbols(symbols):
            bars = self.get_daily_bars(
                symbol,
                start=start,
                end=end,
                adjust=adjust,
                asset_type=asset_type,
                data_dir=data_dir,
                use_cache=use_cache,
            )
            if bars is not None:
                resolved_type = self.resolve_asset_type(symbol, asset_type)
                effective_dir = self._effective_data_dir(resolved_type, data_dir)
                result[symbol] = BarsResult(
                    data=bars,
                    metadata=BarsMetadata(
                        symbol=symbol,
                        asset_type=resolved_type,
                        frequency="1d",
                        adjust=adjust,
                        data_dir=str(effective_dir),
                        start=start,
                        end=end,
                        rows=len(bars),
                        latest_date=self._latest_date_str(bars),
                    ),
                )
        return result

    def get_daily_bars(
        self,
        symbol: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        adjust: str = "qfq",
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        use_cache: bool = True,
    ) -> Optional[pd.DataFrame]:
        """Return one symbol's normalized daily bars, or None when unavailable."""
        normalized = self.normalize_symbol(symbol)
        resolved_type = self.resolve_asset_type(normalized, asset_type)
        effective_dir = self._effective_data_dir(resolved_type, data_dir)

        if resolved_type == "etf":
            df = load_etf_data(
                normalized,
                str(effective_dir),
                start_date=start,
                end_date=end,
                use_cache=use_cache,
            )
            if df is None:
                df = self._load_etf_daily_bars_from_local_dir(
                    normalized,
                    effective_dir,
                    start=start,
                    end=end,
                )
        else:
            df = load_stock_data(
                normalized,
                str(effective_dir),
                adj=adjust,
                start_date=start,
                end_date=end,
                use_cache=use_cache,
            )
        return self._normalize_daily_bars(df)

    def latest_close(
        self,
        symbol: str,
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
    ) -> float:
        """Return the latest close from local daily bars, or 0 when unavailable."""
        df = self.get_daily_bars(
            symbol,
            asset_type=asset_type,
            data_dir=data_dir,
            use_cache=False,
        )
        if df is None or df.empty or "close" not in df.columns:
            return 0.0
        try:
            return float(df["close"].iloc[-1] or 0.0)
        except Exception:
            return 0.0

    def check_daily_freshness(
        self,
        symbol: str,
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> FreshnessStatus:
        """Check whether local daily bars include the expected latest trading day."""
        normalized = self.normalize_symbol(symbol)
        expected = self.latest_expected_trading_day(now)
        expected_str = expected.strftime("%Y-%m-%d")
        df = self.get_daily_bars(
            normalized,
            asset_type=asset_type,
            data_dir=data_dir,
            use_cache=False,
        )
        if df is None or df.empty or "date" not in df.columns:
            return FreshnessStatus(normalized, False, "", expected_str, "missing_data")
        latest_str = self._latest_date_str(df) or ""
        is_fresh = latest_str >= expected_str
        reason = "fresh" if is_fresh else "stale"
        return FreshnessStatus(normalized, is_fresh, latest_str, expected_str, reason)

    def is_pool_fresh(
        self,
        symbols: Iterable[str],
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
    ) -> bool:
        """Return True when every symbol has fresh daily bars."""
        for symbol in symbols:
            if not self.check_daily_freshness(symbol, asset_type=asset_type, data_dir=data_dir).is_fresh:
                return False
        return True

    def list_symbols(
        self,
        *,
        asset_type: str = "stock",
        data_dir: Optional[Path] = None,
    ) -> list[str]:
        """List symbols with local daily-bar parquet files."""
        resolved_type = self.resolve_asset_type("", asset_type)
        effective_dir = self._effective_data_dir(resolved_type, data_dir)
        if not effective_dir.exists():
            return []
        if resolved_type == "etf":
            etf_dir = effective_dir / "etf"
            if etf_dir.exists():
                return sorted({path.stem for path in etf_dir.glob("*.parquet")})
        return sorted({path.stem for path in effective_dir.glob("*.parquet")})

    def resolve_asset_type(self, symbol: str, asset_type: str = "auto") -> str:
        """Resolve asset type for MVP loaders."""
        value = (asset_type or "auto").strip().lower()
        if value in ("stock", "equity"):
            return "stock"
        if value in ("etf", "fund"):
            return "etf"
        if value != "auto":
            raise ValueError(f"Unsupported asset_type: {asset_type}")
        return "etf" if self._is_etf_like_code(symbol) else "stock"

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        value = str(symbol or "").strip().upper()
        return value.split(".", 1)[0] if "." in value else value

    @staticmethod
    def latest_expected_trading_day(now: Optional[datetime] = None) -> date:
        from trading_app.services.market_data_policy import latest_expected_trading_day

        return latest_expected_trading_day(now)

    @classmethod
    def _project_root(cls) -> Path:
        return Path(__file__).resolve().parent.parent

    def _effective_data_dir(self, asset_type: str, data_dir: Optional[Path]) -> Path:
        if data_dir is not None:
            return Path(data_dir)
        return self.default_data_dir

    @staticmethod
    def _normalize_daily_bars(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None
        bars = df.copy()
        if "date" not in bars.columns:
            return None
        bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume"]:
            if column in bars.columns:
                bars[column] = pd.to_numeric(bars[column], errors="coerce")
        required = ["date", "open", "high", "low", "close"]
        if not all(column in bars.columns for column in required):
            return None
        bars = bars.dropna(subset=required)
        if bars.empty:
            return None
        existing_columns = [column for column in _STANDARD_BAR_COLUMNS if column in bars.columns]
        extra_columns = [column for column in bars.columns if column not in existing_columns]
        bars = bars[existing_columns + extra_columns]
        bars = bars.sort_values("date").reset_index(drop=True)
        return bars

    @staticmethod
    def _load_etf_daily_bars_from_local_dir(
        symbol: str,
        data_dir: Path,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """Load ETF bars from a direct-code parquet directory such as live_rotation/data."""
        parquet_path = Path(data_dir) / f"{symbol}.parquet"
        if not parquet_path.exists():
            return None
        try:
            df = pd.read_parquet(parquet_path)
        except Exception:
            return None
        if start and "date" in df.columns:
            df = df[df["date"] >= pd.to_datetime(start)]
        if end and "date" in df.columns:
            df = df[df["date"] <= pd.to_datetime(end)]
        return df.reset_index(drop=True) if not df.empty else None

    @staticmethod
    def _latest_date_str(df: pd.DataFrame) -> Optional[str]:
        if df is None or df.empty or "date" not in df.columns:
            return None
        latest = pd.to_datetime(df["date"], errors="coerce").max()
        if pd.isna(latest):
            return None
        return pd.Timestamp(latest).strftime("%Y-%m-%d")

    @staticmethod
    def _normalize_symbols(symbols: SymbolInput) -> Iterable[str]:
        if isinstance(symbols, str):
            return [DataPortal.normalize_symbol(symbols)]
        return [DataPortal.normalize_symbol(symbol) for symbol in symbols]

    @staticmethod
    def _ensure_daily_frequency(frequency: str) -> None:
        if str(frequency or "").strip().lower() not in _DAILY_FREQUENCIES:
            raise ValueError("DataPortal MVP only supports daily bars")

    @staticmethod
    def _is_etf_like_code(symbol: str) -> bool:
        from trading_app.services.market_data_policy import is_etf_like_code

        return is_etf_like_code(symbol)


_data_portal: Optional[DataPortal] = None


def get_data_portal() -> DataPortal:
    """Return the process-wide DataPortal instance."""
    global _data_portal
    if _data_portal is None:
        _data_portal = DataPortal()
    return _data_portal


def set_data_portal(portal: Optional[DataPortal]) -> None:
    """Replace the process-wide DataPortal instance, mainly for tests."""
    global _data_portal
    _data_portal = portal
