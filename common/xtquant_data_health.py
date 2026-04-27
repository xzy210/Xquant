"""Shared xtquant/miniQMT data-link health checks.

The functions in this module intentionally avoid Qt and trading_app service
objects so they can be reused by GUI update threads, AI freshness guards,
end-of-day refresh services, and live_rotation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from common.daily_update_policy import get_daily_update_policy
from common.market_data_policy import (
    REALTIME_MAX_AGE_SECONDS,
    extract_tick_datetime,
    is_etf_like_code,
    normalize_symbol_code,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DATA_DIR = _PROJECT_ROOT / "data"
_REALTIME_PROBE_CODES = ("159915", "510300", "000001")


@dataclass
class FreshnessCheckResult:
    name: str
    ok: bool
    message: str
    required: bool = True

    @property
    def status_label(self) -> str:
        if self.ok:
            return "正常"
        return "失败" if self.required else "告警"


@dataclass
class XtquantFreshnessReport:
    ok: bool
    checks: List[FreshnessCheckResult] = field(default_factory=list)
    connection_message: str = ""

    @property
    def has_warning(self) -> bool:
        return any((not check.ok) and (not check.required) for check in self.checks)

    @property
    def hard_failures(self) -> List[FreshnessCheckResult]:
        return [check for check in self.checks if (not check.ok) and check.required]

    @property
    def summary(self) -> str:
        headline = "数据链路正常"
        if self.hard_failures:
            headline = "数据链路异常"
        elif self.has_warning:
            headline = "数据链路可用（有告警）"
        parts = [headline]
        if self.connection_message and self.hard_failures:
            parts.append(f"连接: {self.connection_message}")
        for check in self.checks:
            parts.append(f"{check.name}{check.status_label}: {check.message}")
        return "；".join(parts)


def _latest_trading_day() -> date:
    return get_daily_update_policy().expected_trading_day()


def _is_intraday_check_window(now: Optional[datetime] = None) -> bool:
    return get_daily_update_policy().is_intraday_check_window(now)


def _intraday_expected_cutoff(now: Optional[datetime] = None) -> datetime:
    return get_daily_update_policy().intraday_expected_cutoff(now)


def _normalize_symbol_code(code: str) -> str:
    return normalize_symbol_code(code)


def _is_etf_like_code(code: str) -> bool:
    return is_etf_like_code(code)


def _extract_tick_datetime(tick: dict) -> Optional[datetime]:
    return extract_tick_datetime(tick)


def _to_probe_xt_code(code: str) -> str:
    code = _normalize_symbol_code(code).zfill(6)
    if code.startswith(("15", "16", "18")):
        return f"{code}.SZ"
    if code.startswith(("51", "52", "53", "55", "56", "58", "60", "68", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _probe_realtime_ticks(fetch_kline_xtquant) -> Tuple[Dict[str, dict], List[str]]:
    xt_codes = [_to_probe_xt_code(code) for code in _REALTIME_PROBE_CODES]
    reasons: List[str] = []
    try:
        tick_map = fetch_kline_xtquant._call_xtdata_locked(
            lambda: fetch_kline_xtquant.xtdata.get_full_tick(xt_codes),
            reconnect_on_failure=True,
        )
    except Exception as exc:
        return {}, [f"拉取实时行情失败: {exc}"]
    if not isinstance(tick_map, dict) or not tick_map:
        return {}, ["实时行情接口返回空数据"]
    valid_map: Dict[str, dict] = {}
    for xt_code in xt_codes:
        tick = tick_map.get(xt_code)
        if not isinstance(tick, dict):
            reasons.append(f"{xt_code} 未返回 tick")
            continue
        last_price = float(tick.get("lastPrice") or 0.0)
        if last_price <= 0:
            reasons.append(f"{xt_code} 最新价无效")
            continue
        valid_map[xt_code] = tick
    if not valid_map and not reasons:
        reasons.append("实时行情接口未返回有效最新价")
    return valid_map, reasons


def check_parquet_freshness(code: str, subdir: str = "", *, data_dir: Path = _DEFAULT_DATA_DIR) -> Tuple[bool, str]:
    """Check if a parquet file has data for the latest trading day."""
    normalized = _normalize_symbol_code(code)
    asset_type = "index" if subdir == "index" else "etf" if subdir == "etf" else "auto"
    data_path = data_dir / subdir / f"{normalized}.parquet" if subdir else None
    return get_daily_update_policy().check_daily_freshness(
        normalized,
        asset_type=asset_type,
        data_dir=data_dir / subdir if subdir else data_dir,
        data_path=data_path,
    )


def check_batch_freshness(codes: List[str], subdir: str = "", *, data_dir: Path = _DEFAULT_DATA_DIR) -> Dict[str, Tuple[bool, str]]:
    return {
        code: check_parquet_freshness(code, subdir, data_dir=data_dir)
        for code in codes
    }


def _test_xtquant_daily_freshness(fetch_kline_xtquant, *, data_dir: Path) -> FreshnessCheckResult:
    test_code = "000001"
    expected_date = _latest_trading_day()
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=10)).strftime("%Y%m%d")

    try:
        fetch_kline_xtquant.fetch_one(test_code, start_date, end_date, data_dir, "1d")
    except Exception as exc:
        return FreshnessCheckResult("日线完整性", False, f"拉取测试股票 {test_code} 日线失败: {exc}")

    import time
    time.sleep(0.3)
    fresh, info = check_parquet_freshness(test_code, data_dir=data_dir)
    if fresh:
        return FreshnessCheckResult("日线完整性", True, f"{test_code} 最新日期 {info}（预期 >={expected_date}）")
    return FreshnessCheckResult(
        "日线完整性",
        False,
        f"miniQMT 连接正常但无法拉取到最新日线数据：{test_code} 最新日期 {info}，预期 {expected_date}",
    )


def _test_xtquant_realtime_freshness(fetch_kline_xtquant) -> FreshnessCheckResult:
    now = datetime.now()
    tick_map, reasons = _probe_realtime_ticks(fetch_kline_xtquant)
    if not tick_map:
        return FreshnessCheckResult(
            "实时行情freshness",
            False,
            "；".join(reasons[:3]) if reasons else "实时行情接口未返回有效数据",
        )
    for xt_code, tick in tick_map.items():
        last_price = float(tick.get("lastPrice") or 0.0)
        volume = float(tick.get("volume") or 0.0)
        tick_dt = _extract_tick_datetime(tick)
        if tick_dt is not None:
            if tick_dt.date() != now.date():
                continue
            age_seconds = max((now - tick_dt).total_seconds(), 0.0)
            if _is_intraday_check_window(now) and age_seconds > REALTIME_MAX_AGE_SECONDS:
                continue
            return FreshnessCheckResult(
                "实时行情freshness",
                True,
                f"{xt_code} 最新价 {last_price:.3f}，时间 {tick_dt:%H:%M:%S}",
            )
        if last_price > 0 and volume > 0:
            return FreshnessCheckResult(
                "实时行情freshness",
                True,
                f"{xt_code} 最新价 {last_price:.3f}（未返回时间字段，按成交量判定）",
            )
    return FreshnessCheckResult(
        "实时行情freshness",
        False,
        "；".join(reasons[:3]) if reasons else "实时行情时间戳过旧或不属于今天",
    )


def _test_xtquant_order_book_freshness(fetch_kline_xtquant) -> FreshnessCheckResult:
    tick_map, reasons = _probe_realtime_ticks(fetch_kline_xtquant)
    if not tick_map:
        return FreshnessCheckResult(
            "盘口freshness",
            False,
            "；".join(reasons[:3]) if reasons else "未拿到可用 tick，无法校验盘口",
        )
    for xt_code, tick in tick_map.items():
        bid_prices = [float(v or 0.0) for v in (tick.get("bidPrice") or [])]
        ask_prices = [float(v or 0.0) for v in (tick.get("askPrice") or [])]
        bid_ok = any(price > 0 for price in bid_prices)
        ask_ok = any(price > 0 for price in ask_prices)
        if bid_ok and ask_ok:
            return FreshnessCheckResult("盘口freshness", True, f"{xt_code} 买卖盘可用")
        if bid_ok or ask_ok:
            return FreshnessCheckResult("盘口freshness", True, f"{xt_code} 盘口单边可用，可能处于涨跌停或竞价阶段")
    return FreshnessCheckResult(
        "盘口freshness",
        False,
        "；".join(reasons[:3]) if reasons else "未返回有效买卖盘口",
    )


def _test_xtquant_minute_freshness(fetch_kline_xtquant, *, required: bool) -> FreshnessCheckResult:
    today_str = date.today().strftime("%Y%m%d")
    now = datetime.now()
    expected_cutoff = _intraday_expected_cutoff(now)
    reasons: List[str] = []
    for test_code in _REALTIME_PROBE_CODES:
        xt_code = _to_probe_xt_code(test_code)
        try:
            df = fetch_kline_xtquant.get_minute_data(test_code, today_str, "1m")
        except Exception as exc:
            reasons.append(f"{xt_code} 拉取分时失败: {exc}")
            continue
        if df is None or df.empty or "time" not in df.columns:
            reasons.append(f"{xt_code} 未获取到当日分时数据")
            continue
        latest_dt = pd.Timestamp(df["time"].max()).to_pydatetime()
        if latest_dt.date() != now.date():
            reasons.append(f"{xt_code} 分时最新时间 {latest_dt:%Y-%m-%d %H:%M} 不属于今天")
            continue
        if latest_dt < expected_cutoff:
            reasons.append(f"{xt_code} 分时最新时间 {latest_dt:%H:%M} 低于预期阈值 {expected_cutoff:%H:%M}")
            continue
        return FreshnessCheckResult(
            "分钟线freshness",
            True,
            f"{xt_code} 最新时间 {latest_dt:%Y-%m-%d %H:%M}",
            required=required,
        )
    return FreshnessCheckResult(
        "分钟线freshness",
        False,
        "；".join(reasons[:3]) if reasons else "分钟线接口未返回有效当日数据",
        required=required,
    )


def evaluate_xtquant_data_freshness(
    *,
    require_minute_freshness: bool = False,
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> XtquantFreshnessReport:
    import sys

    project_root = _PROJECT_ROOT
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from scripts import fetch_kline_xtquant
    except ImportError:
        return XtquantFreshnessReport(
            False,
            [FreshnessCheckResult("数据链路", False, "fetch_kline_xtquant 模块导入失败")],
        )

    if not fetch_kline_xtquant.check_xtquant_available():
        return XtquantFreshnessReport(
            False,
            [FreshnessCheckResult("数据链路", False, "xtquant 未安装")],
        )

    conn_msg = ""
    try:
        _connected, conn_msg = fetch_kline_xtquant.check_connection()
    except Exception as exc:
        conn_msg = f"连接预检异常: {exc}"

    checks = [_test_xtquant_daily_freshness(fetch_kline_xtquant, data_dir=data_dir)]
    if checks[-1].ok and _is_intraday_check_window():
        checks.append(_test_xtquant_realtime_freshness(fetch_kline_xtquant))
        checks.append(_test_xtquant_order_book_freshness(fetch_kline_xtquant))
        checks.append(
            _test_xtquant_minute_freshness(
                fetch_kline_xtquant,
                required=require_minute_freshness,
            )
        )

    ok = all(check.ok or (not check.required) for check in checks)
    return XtquantFreshnessReport(ok, checks, connection_message=conn_msg)


def test_xtquant_data_freshness(*, require_minute_freshness: bool = False, data_dir: Path = _DEFAULT_DATA_DIR) -> Tuple[bool, str]:
    """Test that xtquant can actually pull today's data through the same daily pipeline."""
    report = evaluate_xtquant_data_freshness(
        require_minute_freshness=require_minute_freshness,
        data_dir=data_dir,
    )
    return report.ok, report.summary


__all__ = [
    "FreshnessCheckResult",
    "XtquantFreshnessReport",
    "check_batch_freshness",
    "check_parquet_freshness",
    "evaluate_xtquant_data_freshness",
    "test_xtquant_data_freshness",
]
