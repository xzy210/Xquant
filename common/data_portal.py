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

from .data_loader import (
    get_etf_cache,
    get_etf_list,
    get_stock_cache,
    get_stock_list,
    load_etf_categories,
    load_etf_data,
    load_etf_name_map,
    load_stock_data,
    load_stock_name_map,
)

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
    first_date: Optional[str] = None
    expected_date: Optional[str] = None
    is_fresh: Optional[bool] = None
    data_path: Optional[str] = None


@dataclass(frozen=True)
class FreshnessStatus:
    """Daily bars freshness status for one symbol."""

    symbol: str
    is_fresh: bool
    latest_date: str
    expected_date: str
    reason: str = ""


@dataclass(frozen=True)
class DailyDataStatus:
    """Unified local daily parquet metadata and freshness status."""

    symbol: str
    asset_type: str
    data_dir: str
    data_path: str
    exists: bool
    rows: int
    first_date: Optional[str]
    latest_date: Optional[str]
    expected_date: str
    is_fresh: bool
    reason: str
    schema_version: str = "daily_bars.v1"


@dataclass(frozen=True)
class AssetMetadata:
    """Unified symbol metadata for stocks, ETFs, and indices."""

    symbol: str
    asset_type: str
    name: str = ""
    market: str = ""
    category: str = ""
    first_date: Optional[str] = None
    latest_date: Optional[str] = None
    rows: int = 0
    data_path: Optional[str] = None
    is_fresh: Optional[bool] = None


@dataclass(frozen=True)
class CacheRefreshResult:
    """Result of refreshing already-loaded local data caches."""

    stock_count: int = 0
    etf_count: int = 0
    stock_cache_loaded: bool = False
    etf_cache_loaded: bool = False

    @property
    def refreshed(self) -> bool:
        return bool(self.stock_count or self.etf_count)


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
                daily_status = self.get_daily_metadata(
                    symbol,
                    asset_type=resolved_type,
                    data_dir=effective_dir,
                    now=None,
                )
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
                        first_date=self._first_date_str(bars),
                        expected_date=daily_status.expected_date,
                        is_fresh=daily_status.is_fresh,
                        data_path=daily_status.data_path,
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
        elif resolved_type == "index":
            df = self._load_index_daily_bars_from_local_dir(
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
        status = self.get_daily_metadata(
            symbol,
            asset_type=asset_type,
            data_dir=data_dir,
            now=now,
        )
        return FreshnessStatus(
            status.symbol,
            status.is_fresh,
            status.latest_date or "",
            status.expected_date,
            status.reason,
        )

    def get_daily_metadata(
        self,
        symbol: str,
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> DailyDataStatus:
        """Return unified metadata and freshness status for one local daily parquet file."""
        normalized = self.normalize_symbol(symbol)
        resolved_type = self.resolve_asset_type(normalized, asset_type)
        effective_dir = self._effective_data_dir(resolved_type, data_dir)
        parquet_path = self._resolve_daily_parquet_path(normalized, resolved_type, effective_dir)
        return self.get_daily_file_metadata(
            parquet_path,
            symbol=normalized,
            asset_type=resolved_type,
            data_dir=effective_dir,
            now=now,
        )

    def get_daily_metadata_map(
        self,
        symbols: Iterable[str],
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, DailyDataStatus]:
        """Return daily parquet metadata for multiple symbols."""
        result: Dict[str, DailyDataStatus] = {}
        for symbol in self._normalize_symbols(symbols):
            result[symbol] = self.get_daily_metadata(
                symbol,
                asset_type=asset_type,
                data_dir=data_dir,
                now=now,
            )
        return result

    def check_daily_freshness_map(
        self,
        symbols: Iterable[str],
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, FreshnessStatus]:
        """Return daily freshness statuses for multiple symbols."""
        return {
            symbol: FreshnessStatus(
                status.symbol,
                status.is_fresh,
                status.latest_date or "",
                status.expected_date,
                status.reason,
            )
            for symbol, status in self.get_daily_metadata_map(
                symbols,
                asset_type=asset_type,
                data_dir=data_dir,
                now=now,
            ).items()
        }

    def get_daily_file_metadata(
        self,
        parquet_path: Path,
        *,
        symbol: str = "",
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
        now: Optional[datetime] = None,
    ) -> DailyDataStatus:
        """Return metadata and freshness status for an explicit daily parquet path."""
        path = Path(parquet_path)
        normalized = self.normalize_symbol(symbol or path.stem)
        resolved_type = self._infer_asset_type_from_path(path, normalized, asset_type)
        effective_dir = Path(data_dir) if data_dir is not None else path.parent
        expected = self.latest_expected_trading_day(now)
        expected_str = expected.strftime("%Y-%m-%d")

        if not path.exists():
            return DailyDataStatus(
                normalized,
                resolved_type,
                str(effective_dir),
                str(path),
                False,
                0,
                None,
                None,
                expected_str,
                False,
                "missing_file",
            )
        try:
            date_df = pd.read_parquet(path, columns=["date"])
        except Exception as exc:
            return DailyDataStatus(
                normalized,
                resolved_type,
                str(effective_dir),
                str(path),
                True,
                0,
                None,
                None,
                expected_str,
                False,
                f"read_error: {exc}",
            )
        if date_df.empty:
            return DailyDataStatus(
                normalized,
                resolved_type,
                str(effective_dir),
                str(path),
                True,
                0,
                None,
                None,
                expected_str,
                False,
                "empty_file",
            )
        dates = pd.to_datetime(date_df["date"], errors="coerce").dropna()
        if dates.empty:
            return DailyDataStatus(
                normalized,
                resolved_type,
                str(effective_dir),
                str(path),
                True,
                len(date_df),
                None,
                None,
                expected_str,
                False,
                "missing_date",
            )
        first_str = pd.Timestamp(dates.min()).strftime("%Y-%m-%d")
        latest_str = pd.Timestamp(dates.max()).strftime("%Y-%m-%d")
        is_fresh = latest_str >= expected_str
        return DailyDataStatus(
            normalized,
            resolved_type,
            str(effective_dir),
            str(path),
            True,
            len(date_df),
            first_str,
            latest_str,
            expected_str,
            is_fresh,
            "fresh" if is_fresh else "stale",
        )

    @staticmethod
    def format_daily_status_message(status: DailyDataStatus) -> str:
        """Format a daily metadata status as a legacy freshness message."""
        if status.reason == "missing_file":
            return "文件不存在"
        if status.reason == "empty_file":
            return "空文件"
        if status.reason == "missing_date":
            return "缺少date列"
        if status.reason.startswith("read_error: "):
            return f"读取失败: {status.reason.split(': ', 1)[1]}"
        return status.latest_date or status.reason

    def is_pool_fresh(
        self,
        symbols: Iterable[str],
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
    ) -> bool:
        """Return True when every symbol has fresh daily bars."""
        return all(
            status.is_fresh
            for status in self.get_daily_metadata_map(
                symbols,
                asset_type=asset_type,
                data_dir=data_dir,
            ).values()
        )

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
        if resolved_type == "index":
            index_dir = effective_dir / "index"
            if index_dir.exists():
                return sorted({path.stem for path in index_dir.glob("*.parquet")})
            return []
        return sorted({path.stem for path in effective_dir.glob("*.parquet")})

    def get_name_map(
        self,
        *,
        asset_type: str = "stock",
        stocklist_path: Optional[Path] = None,
        etf_config_path: Optional[Path] = None,
    ) -> Dict[str, str]:
        """Return a unified code-to-name map for one asset type."""
        resolved_type = self.resolve_asset_type("", asset_type)
        if resolved_type == "stock":
            return load_stock_name_map(str(stocklist_path)) if stocklist_path else load_stock_name_map()
        if resolved_type == "etf":
            return load_etf_name_map(str(etf_config_path)) if etf_config_path else load_etf_name_map()
        if resolved_type == "index":
            return {item["symbol"]: item["name"] for item in self.list_assets(asset_type="index", include_status=False)}
        return {}

    def get_categories(
        self,
        *,
        asset_type: str = "etf",
        config_path: Optional[Path] = None,
    ) -> list[Dict]:
        """Return classification metadata for asset types that have categories."""
        resolved_type = self.resolve_asset_type("", asset_type)
        if resolved_type == "etf":
            return load_etf_categories(str(config_path)) if config_path else load_etf_categories()
        return []

    def get_date_range(
        self,
        symbol: str,
        *,
        asset_type: str = "auto",
        data_dir: Optional[Path] = None,
    ) -> Optional[tuple[str, str]]:
        """Return the first/latest local daily-bar date for one symbol."""
        status = self.get_daily_metadata(symbol, asset_type=asset_type, data_dir=data_dir)
        if not status.first_date or not status.latest_date:
            return None
        return status.first_date, status.latest_date

    def list_assets(
        self,
        *,
        asset_type: str = "stock",
        data_dir: Optional[Path] = None,
        stocklist_path: Optional[Path] = None,
        etf_config_path: Optional[Path] = None,
        include_status: bool = True,
    ) -> list[dict]:
        """Return unified asset metadata records for stocks, ETFs, or indices."""
        resolved_type = self.resolve_asset_type("", asset_type)
        effective_dir = self._effective_data_dir(resolved_type, data_dir)
        if resolved_type == "index":
            assets = self._list_index_assets()
            if include_status:
                assets = [self._attach_asset_status(asset, effective_dir) for asset in assets]
            return [self._asset_to_dict(asset) for asset in assets]

        symbols = self.list_symbols(asset_type=resolved_type, data_dir=effective_dir)
        name_map = self.get_name_map(
            asset_type=resolved_type,
            stocklist_path=stocklist_path,
            etf_config_path=etf_config_path,
        )
        category_map = self._load_etf_category_map(etf_config_path) if resolved_type == "etf" else {}
        assets: list[AssetMetadata] = []
        for symbol in symbols:
            asset = AssetMetadata(
                symbol=symbol,
                asset_type=resolved_type,
                name=name_map.get(symbol, ""),
                category=category_map.get(symbol, ""),
            )
            assets.append(self._attach_asset_status(asset, effective_dir) if include_status else asset)
        return [self._asset_to_dict(asset) for asset in assets]

    def refresh_loaded_caches(
        self,
        *,
        data_dir: Optional[Path] = None,
        stock_codes: Optional[list[str]] = None,
        etf_codes: Optional[list[str]] = None,
        max_workers: int = 8,
    ) -> CacheRefreshResult:
        """Reload already-loaded stock/ETF memory caches after parquet updates."""
        effective_dir = self._effective_data_dir("stock", data_dir)
        stock_count = 0
        etf_count = 0
        stock_loaded = False
        etf_loaded = False

        stock_cache = get_stock_cache()
        stock_loaded = stock_cache.is_loaded()
        if stock_loaded:
            codes = stock_codes if stock_codes is not None else get_stock_list(str(effective_dir))
            stock_count = stock_cache.reload_all(
                data_dir=str(effective_dir),
                stock_codes=codes,
                max_workers=max_workers,
            )

        etf_cache = get_etf_cache()
        etf_loaded = etf_cache.is_loaded()
        if etf_loaded:
            codes = etf_codes if etf_codes is not None else get_etf_list(str(effective_dir))
            etf_count = etf_cache.reload_all(
                data_dir=str(effective_dir),
                etf_codes=codes,
                max_workers=max_workers,
            )

        return CacheRefreshResult(
            stock_count=stock_count,
            etf_count=etf_count,
            stock_cache_loaded=stock_loaded,
            etf_cache_loaded=etf_loaded,
        )

    def resolve_asset_type(self, symbol: str, asset_type: str = "auto") -> str:
        """Resolve asset type for MVP loaders."""
        value = (asset_type or "auto").strip().lower()
        if value in ("stock", "equity"):
            return "stock"
        if value in ("etf", "fund"):
            return "etf"
        if value in ("index", "idx"):
            return "index"
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

    def _resolve_daily_parquet_path(self, symbol: str, asset_type: str, data_dir: Path) -> Path:
        normalized = self.normalize_symbol(symbol)
        if asset_type == "index":
            return Path(data_dir) / "index" / f"{normalized}.parquet"
        if asset_type == "etf":
            etf_path = Path(data_dir) / "etf" / f"{normalized}.parquet"
            if etf_path.exists():
                return etf_path
            return Path(data_dir) / f"{normalized}.parquet"
        return Path(data_dir) / f"{normalized}.parquet"

    def _infer_asset_type_from_path(self, path: Path, symbol: str, asset_type: str = "auto") -> str:
        value = (asset_type or "auto").strip().lower()
        if value != "auto":
            return self.resolve_asset_type(symbol, value)
        parent_name = path.parent.name.lower()
        if parent_name == "index":
            return "index"
        if parent_name == "etf":
            return "etf"
        return self.resolve_asset_type(symbol, "auto")

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
    def _load_index_daily_bars_from_local_dir(
        symbol: str,
        data_dir: Path,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """Load index bars from data/index/{symbol}.parquet."""
        parquet_path = Path(data_dir) / "index" / f"{symbol}.parquet"
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

    def _list_index_assets(self) -> list[AssetMetadata]:
        from trading_app.services.index_service import get_index_list

        return [
            AssetMetadata(
                symbol=item.get("code", ""),
                asset_type="index",
                name=item.get("name", ""),
                market=item.get("market", item.get("exchange", "")),
            )
            for item in get_index_list()
            if item.get("code")
        ]

    def _attach_asset_status(self, asset: AssetMetadata, data_dir: Path) -> AssetMetadata:
        status = self.get_daily_metadata(asset.symbol, asset_type=asset.asset_type, data_dir=data_dir)
        return AssetMetadata(
            symbol=asset.symbol,
            asset_type=asset.asset_type,
            name=asset.name,
            market=asset.market,
            category=asset.category,
            first_date=status.first_date,
            latest_date=status.latest_date,
            rows=status.rows,
            data_path=status.data_path,
            is_fresh=status.is_fresh,
        )

    @staticmethod
    def _load_etf_category_map(config_path: Optional[Path] = None) -> Dict[str, str]:
        categories = load_etf_categories(str(config_path)) if config_path else load_etf_categories()
        result: Dict[str, str] = {}
        for category in categories:
            category_name = category.get("name", "")
            for etf in category.get("etfs", []):
                code = etf.get("code", "")
                if code:
                    result[code] = category_name
        return result

    @staticmethod
    def _asset_to_dict(asset: AssetMetadata) -> dict:
        result = asset.__dict__.copy()
        result["code"] = asset.symbol
        return result

    @staticmethod
    def _latest_date_str(df: pd.DataFrame) -> Optional[str]:
        if df is None or df.empty or "date" not in df.columns:
            return None
        latest = pd.to_datetime(df["date"], errors="coerce").max()
        if pd.isna(latest):
            return None
        return pd.Timestamp(latest).strftime("%Y-%m-%d")

    @staticmethod
    def _first_date_str(df: pd.DataFrame) -> Optional[str]:
        if df is None or df.empty or "date" not in df.columns:
            return None
        first = pd.to_datetime(df["date"], errors="coerce").min()
        if pd.isna(first):
            return None
        return pd.Timestamp(first).strftime("%Y-%m-%d")

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
