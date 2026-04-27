from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.daily_update_policy import DailyUpdatePolicy, get_daily_update_policy
from common.data_portal import DataPortal, set_data_portal

def _write_daily_file(path: Path, dates: list[str]) -> None:
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": [1.0] * len(dates),
            "high": [1.1] * len(dates),
            "low": [0.9] * len(dates),
            "close": [1.0] * len(dates),
            "volume": [100] * len(dates),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        set_data_portal(DataPortal(default_data_dir=data_dir))
        policy = DailyUpdatePolicy()

        local_file = data_dir / "000001.parquet"
        _write_daily_file(local_file, ["2024-01-02", "2024-01-03"])

        window = policy.resolve_fetch_window(
            asset_type="stock",
            full_update=False,
            local_path=local_file,
            now=datetime(2024, 1, 4, 16, 0),
        )
        assert window.start_date == "20240103", window
        assert window.end_date == "20240104", window

        fresh, info = policy.check_daily_freshness(
            "000001",
            asset_type="stock",
            data_dir=data_dir,
            now=datetime(2024, 1, 3, 16, 0),
        )
        assert fresh, info

        stale = policy.find_stale_symbols(
            ["000001", "000002"],
            asset_type="stock",
            data_dir=data_dir,
            now=datetime(2024, 1, 4, 16, 0),
        )
        assert stale == ["000001", "000002"], stale

        result = policy.run_daily_history_precheck(
            lambda: (False, "测试失败"),
            action_hint="请检查数据源。",
        )
        assert not result.ok
        assert "miniQMT 历史K线数据源异常" in result.message

    set_data_portal(None)
    assert get_daily_update_policy().today_fetch_end(datetime(2024, 1, 5, 9, 0)) == "20240105"
    print("daily_update_policy_smoketest passed")


if __name__ == "__main__":
    main()
