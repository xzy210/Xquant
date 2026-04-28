from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.daily_update_policy import DailyUpdatePolicy, get_daily_update_policy, set_daily_update_policy
from common.data_portal import DataPortal, set_data_portal
from common.xtquant_data_health import _recent_daily_history_window, _test_xtquant_daily_freshness


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


def _make_daily_frame(dates: list) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": [1.0] * len(dates),
            "high": [1.1] * len(dates),
            "low": [0.9] * len(dates),
            "close": [1.0] * len(dates),
            "volume": [100] * len(dates),
        }
    )


def _assert_recent_daily_history_precheck() -> None:
    _window_start, _window_end, expected_dates = _recent_daily_history_window(10)
    assert expected_dates, "recent daily-history window should include trading days"

    complete_fetcher = SimpleNamespace(
        _get_kline_xtquant=lambda code, start, end, period: _make_daily_frame(expected_dates)
    )
    complete = _test_xtquant_daily_freshness(
        complete_fetcher,
        data_dir=Path("."),
        require_recent_daily_history=True,
        recent_calendar_days=10,
    )
    assert complete.ok, complete.message

    incomplete_dates = expected_dates[:-1]
    incomplete_fetcher = SimpleNamespace(
        _get_kline_xtquant=lambda code, start, end, period: _make_daily_frame(incomplete_dates)
    )
    incomplete = _test_xtquant_daily_freshness(
        incomplete_fetcher,
        data_dir=Path("."),
        require_recent_daily_history=True,
        recent_calendar_days=10,
    )
    assert not incomplete.ok, incomplete.message
    assert "无法完整获取" in incomplete.message or "未返回" in incomplete.message


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        set_data_portal(DataPortal(default_data_dir=data_dir))
        set_daily_update_policy(None)
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
    set_daily_update_policy(None)
    assert get_daily_update_policy().today_fetch_end(datetime(2024, 1, 5, 9, 0)) == "20240105"
    _assert_recent_daily_history_precheck()
    print("daily_update_policy_smoketest passed")


if __name__ == "__main__":
    main()
