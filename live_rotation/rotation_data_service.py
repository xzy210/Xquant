"""
ETF rotation data access service.

This thin adapter centralizes ETF rotation data reads, freshness checks, and
update primitives. Reads and freshness checks go through the unified DataPortal
MVP, while update primitives still reuse the existing ETF updater.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pandas as pd

from common.data_portal import DataPortal, get_data_portal

from .data_updater import (
    ETFDataUpdateThread,
    _default_data_dir,
    update_etf_pool,
)


class RotationDataService:
    """Central data access seam for ETF rotation live services."""

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        data_portal: Optional[DataPortal] = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else _default_data_dir()
        self.data_portal = data_portal or get_data_portal()

    def update_context(
        self,
        *,
        data_dir: Optional[Path] = None,
        data_portal: Optional[DataPortal] = None,
    ) -> None:
        """Refresh backing data dependencies."""
        if data_dir is not None:
            self.data_dir = Path(data_dir)
        if data_portal is not None:
            self.data_portal = data_portal

    def load_daily_bars(self, code: str) -> Optional[pd.DataFrame]:
        """Load normalized daily bars for one ETF code."""
        return self.data_portal.get_daily_bars(
            code,
            asset_type="etf",
            data_dir=self.data_dir,
            use_cache=False,
        )

    def latest_close(self, code: str) -> float:
        """Return the latest close price from local daily bars, or 0 when unavailable."""
        return self.data_portal.latest_close(
            code,
            asset_type="etf",
            data_dir=self.data_dir,
        )

    def is_code_fresh(self, code: str) -> Tuple[bool, str]:
        """Check whether one ETF code has data through the expected trading day."""
        status = self.data_portal.check_daily_freshness(
            code,
            asset_type="etf",
            data_dir=self.data_dir,
        )
        return status.is_fresh, status.latest_date

    def is_pool_fresh(self, codes: Iterable[str]) -> bool:
        """Check whether all codes in the ETF pool are fresh."""
        return self.data_portal.is_pool_fresh(
            codes,
            asset_type="etf",
            data_dir=self.data_dir,
        )

    def update_pool(self, codes: List[str]) -> Tuple[int, int, List[str]]:
        """Synchronously update the ETF pool daily bars."""
        return update_etf_pool(codes, self.data_dir)

    def create_update_thread(self, codes: List[str], *, parent=None) -> ETFDataUpdateThread:
        """Create the existing Qt update thread for asynchronous data updates."""
        return ETFDataUpdateThread(codes, self.data_dir, parent=parent)
