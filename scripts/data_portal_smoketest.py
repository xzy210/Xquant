from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from common.data_portal import DataPortal
from live_rotation.rotation_data_service import RotationDataService


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        data_dir.mkdir(parents=True, exist_ok=True)

        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "open": [1.0, 1.1],
                "high": [1.2, 1.3],
                "low": [0.9, 1.0],
                "close": [1.1, 1.25],
                "volume": [1000, 1200],
            }
        )
        df.to_parquet(data_dir / "510880.parquet", index=False)

        portal = DataPortal(default_data_dir=data_dir)
        bars = portal.get_daily_bars(
            "510880.SH",
            asset_type="etf",
            data_dir=data_dir,
            use_cache=False,
        )
        assert bars is not None
        assert list(bars.columns) == ["date", "open", "high", "low", "close", "volume"]
        assert float(bars["close"].iloc[-1]) == 1.25

        result = portal.get_bars(
            ["510880.SH"],
            asset_type="etf",
            data_dir=data_dir,
            use_cache=False,
        )
        assert "510880" in result
        assert result["510880"].metadata.schema_version == "daily_bars.v1"
        assert result["510880"].metadata.latest_date == "2024-01-03"

        stale = portal.check_daily_freshness(
            "510880",
            asset_type="etf",
            data_dir=data_dir,
            now=datetime(2024, 1, 4, 16, 0),
        )
        assert not stale.is_fresh
        assert stale.latest_date == "2024-01-03"
        assert stale.expected_date == "2024-01-04"

        fresh = portal.check_daily_freshness(
            "510880",
            asset_type="etf",
            data_dir=data_dir,
            now=datetime(2024, 1, 3, 16, 0),
        )
        assert fresh.is_fresh

        rotation_data = RotationDataService(data_dir=data_dir, data_portal=portal)
        rotation_bars = rotation_data.load_daily_bars("510880")
        assert rotation_bars is not None
        assert float(rotation_data.latest_close("510880")) == 1.25
        is_fresh, latest_date = rotation_data.is_code_fresh("510880")
        assert isinstance(is_fresh, bool)
        assert latest_date == "2024-01-03"
        assert rotation_data.is_pool_fresh(["510880"]) == is_fresh

    print("data_portal_smoketest_ok")


if __name__ == "__main__":
    main()
