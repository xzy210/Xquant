"""
ETF rotation data access service.

This thin adapter centralizes ETF rotation data reads, freshness checks, and
update primitives so the engine and services do not depend on parquet helpers
directly. It is intentionally small and can later be backed by a unified
DataPortal without changing strategy/runtime callers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pandas as pd

from .data_updater import (
    ETFDataUpdateThread,
    _default_data_dir,
    check_data_freshness,
    load_etf_parquet,
    update_etf_pool,
)


class RotationDataService:
    """Central data access seam for ETF rotation live services."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else _default_data_dir()

    def update_context(self, *, data_dir: Optional[Path] = None) -> None:
        """Refresh the backing data directory."""
        if data_dir is not None:
            self.data_dir = Path(data_dir)

    def load_daily_bars(self, code: str) -> Optional[pd.DataFrame]:
        """Load normalized daily bars for one ETF code."""
        return load_etf_parquet(code, self.data_dir)

    def latest_close(self, code: str) -> float:
        """Return the latest close price from local daily bars, or 0 when unavailable."""
        df = self.load_daily_bars(code)
        if df is None or df.empty or "close" not in df.columns:
            return 0.0
        try:
            return float(df["close"].iloc[-1] or 0.0)
        except Exception:
            return 0.0

    def is_code_fresh(self, code: str) -> Tuple[bool, str]:
        """Check whether one ETF code has data through the expected trading day."""
        return check_data_freshness(self.data_dir, code)

    def is_pool_fresh(self, codes: Iterable[str]) -> bool:
        """Check whether all codes in the ETF pool are fresh."""
        for code in codes:
            fresh, _ = self.is_code_fresh(str(code))
            if not fresh:
                return False
        return True

    def update_pool(self, codes: List[str]) -> Tuple[int, int, List[str]]:
        """Synchronously update the ETF pool daily bars."""
        return update_etf_pool(codes, self.data_dir)

    def create_update_thread(self, codes: List[str], *, parent=None) -> ETFDataUpdateThread:
        """Create the existing Qt update thread for asynchronous data updates."""
        return ETFDataUpdateThread(codes, self.data_dir, parent=parent)
