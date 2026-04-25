"""Data freshness check & auto-update before AI tasks.

Checks whether local parquet files contain today's (or latest trading day's) data.
If stale, tests xtquant connectivity via reconnect, then triggers DataUpdateThread
for the required stock codes + indices.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import threading

import pandas as pd
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from live_rotation.holiday_calendar import is_trading_day

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_INDEX_DIR = _DATA_DIR / "index"
_STOCKLIST_PATH = _PROJECT_ROOT / "stocklist" / "stocklist.csv"
_REALTIME_PROBE_CODES = ("159915", "510300", "000001")
_REALTIME_MAX_AGE_SECONDS = 90


def _normalize_symbol_code(code: str) -> str:
    value = str(code or "").strip().upper()
    return value.split(".", 1)[0] if "." in value else value


def _is_etf_like_code(code: str) -> bool:
    code = _normalize_symbol_code(code).zfill(6)
    return code.startswith(("15", "16", "18", "51", "52", "56", "58"))


def _resolve_parquet_path(code: str, subdir: str = "") -> Path:
    code = _normalize_symbol_code(code)
    if subdir:
        return _DATA_DIR / subdir / f"{code}.parquet"
    if _is_etf_like_code(code):
        etf_path = _DATA_DIR / "etf" / f"{code}.parquet"
        if etf_path.exists():
            return etf_path
    return _DATA_DIR / f"{code}.parquet"


def _dedupe_codes(codes: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for code in codes:
        normalized = _normalize_symbol_code(code)
        if not normalized:
            continue
        normalized = normalized.zfill(6)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _refresh_loaded_market_data_caches(stock_codes: List[str], etf_codes: List[str]) -> Tuple[int, int]:
    """Reload in-memory caches after parquet freshness is confirmed."""
    refreshed_stocks = 0
    refreshed_etfs = 0
    try:
        from common.data_loader import get_etf_cache, get_stock_cache
    except Exception as exc:
        logger.warning("导入行情缓存管理器失败，跳过缓存刷新: %s", exc)
        return refreshed_stocks, refreshed_etfs

    try:
        stock_cache = get_stock_cache()
        if stock_cache.is_loaded():
            for code in _dedupe_codes(stock_codes):
                if stock_cache.reload_stock(code, str(_DATA_DIR)):
                    refreshed_stocks += 1
    except Exception as exc:
        logger.warning("刷新股票行情缓存失败: %s", exc)

    try:
        etf_cache = get_etf_cache()
        if etf_cache.is_loaded():
            for code in _dedupe_codes(etf_codes):
                if etf_cache.reload_etf(code, str(_DATA_DIR)):
                    refreshed_etfs += 1
    except Exception as exc:
        logger.warning("刷新ETF行情缓存失败: %s", exc)

    if refreshed_stocks or refreshed_etfs:
        logger.info("已刷新内存行情缓存: 股票 %d 只, ETF %d 只", refreshed_stocks, refreshed_etfs)
    return refreshed_stocks, refreshed_etfs


def _latest_trading_day() -> date:
    """Return the latest expected trading day (skip weekends and holidays)."""
    today = date.today()
    now = datetime.now()
    if not is_trading_day(today):
        d = today - timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
        return d
    if now.hour < 15:
        d = today - timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
        return d
    return today


def _is_intraday_check_window(now: Optional[datetime] = None) -> bool:
    """Return True only during active trading sessions for minute freshness checks."""
    now = now or datetime.now()
    if not is_trading_day(now.date()):
        return False
    current = now.time()
    in_morning = dt_time(9, 30) <= current <= dt_time(11, 30)
    in_afternoon = dt_time(13, 0) <= current <= dt_time(15, 0)
    return in_morning or in_afternoon


def _intraday_expected_cutoff(now: Optional[datetime] = None) -> datetime:
    """Expected lower bound for the latest minute bar timestamp."""
    now = now or datetime.now()
    current = now.time()
    if current <= dt_time(11, 30):
        session_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    else:
        session_start = now.replace(hour=13, minute=0, second=0, microsecond=0)
    return max(session_start, now - timedelta(minutes=15))


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


def _coerce_tick_datetime(value) -> Optional[datetime]:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        ivalue = int(value)
        if ivalue <= 0:
            return None
        if ivalue >= 10**12:
            return datetime.fromtimestamp(ivalue / 1000)
        return datetime.fromtimestamp(ivalue)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if value.isdigit():
            return _coerce_tick_datetime(int(value))
        for fmt in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def _extract_tick_datetime(tick: dict) -> Optional[datetime]:
    if not isinstance(tick, dict):
        return None
    for key in ("timetag", "time"):
        tick_dt = _coerce_tick_datetime(tick.get(key))
        if tick_dt is not None:
            return tick_dt
    return None


def _to_probe_xt_code(code: str) -> str:
    code = _normalize_symbol_code(code).zfill(6)
    if code.startswith(("15", "16", "18")):
        return f"{code}.SZ"
    if code.startswith(("51", "52", "56", "58", "60", "68", "9")):
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


def _test_xtquant_daily_freshness(fetch_kline_xtquant) -> FreshnessCheckResult:
    test_code = "000001"
    expected_date = _latest_trading_day()
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=10)).strftime("%Y%m%d")

    try:
        fetch_kline_xtquant.fetch_one(test_code, start_date, end_date, _DATA_DIR, "1d")
    except Exception as exc:
        return FreshnessCheckResult("日线完整性", False, f"拉取测试股票 {test_code} 日线失败: {exc}")

    import time
    time.sleep(0.3)
    fresh, info = check_parquet_freshness(test_code)
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
            if _is_intraday_check_window(now) and age_seconds > _REALTIME_MAX_AGE_SECONDS:
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


def check_parquet_freshness(code: str, subdir: str = "") -> Tuple[bool, str]:
    """Check if a parquet file has data for the latest trading day.

    Returns (is_fresh, last_date_str).
    """
    code = _normalize_symbol_code(code)
    pq_path = _resolve_parquet_path(code, subdir=subdir)

    if not pq_path.exists():
        return False, "文件不存在"

    try:
        df = pd.read_parquet(pq_path, columns=["date"])
        if df.empty:
            return False, "空文件"
        last_date = pd.Timestamp(df["date"].max()).date()
        expected = _latest_trading_day()
        return last_date >= expected, str(last_date)
    except Exception as exc:
        return False, f"读取失败: {exc}"


def check_batch_freshness(codes: List[str], subdir: str = "") -> Dict[str, Tuple[bool, str]]:
    return {code: check_parquet_freshness(code, subdir) for code in codes}


def evaluate_xtquant_data_freshness(*, require_minute_freshness: bool = False) -> XtquantFreshnessReport:
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

    checks = [_test_xtquant_daily_freshness(fetch_kline_xtquant)]
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


def test_xtquant_data_freshness(*, require_minute_freshness: bool = False) -> Tuple[bool, str]:
    """Test that xtquant can actually pull today's data by running the exact
    same fetch pipeline as the main window's "同步数据" button.

    This catches the common case where miniQMT has been open for many days
    and silently returns stale cached data despite the connection appearing
    healthy.

    Returns (success, message).
    """
    report = evaluate_xtquant_data_freshness(require_minute_freshness=require_minute_freshness)
    return report.ok, report.summary


class DataFreshnessGuard(QObject):
    """Pre-flight check before AI scheduled tasks.

    Signals:
        check_started: emitted when freshness check begins
        update_needed: (stale_count, message) emitted when data needs updating
        update_progress: (current, total, msg) forwarded from DataUpdateThread
        update_finished: (success, message) when everything is ready
        xtquant_failed: (message) when xtquant reconnect test fails
    """

    check_started = pyqtSignal()
    update_needed = pyqtSignal(int, str)
    update_progress = pyqtSignal(int, int, str)
    update_finished = pyqtSignal(bool, str)
    status_notice = pyqtSignal(str, str)
    xtquant_failed = pyqtSignal(str)
    freshness_test_done = pyqtSignal(object, list, list, list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._update_thread = None
        self._etf_thread = None
        self._pending_callback: Optional[Callable] = None
        self._stale_codes: List[str] = []
        self._stale_etf_codes: List[str] = []
        self._checked_stock_codes: List[str] = []
        self._checked_etf_codes: List[str] = []
        self._pending_notice_message: str = ""
        self.freshness_test_done.connect(self._on_freshness_test_done)

    def ensure_fresh_then_run(
        self,
        codes: List[str],
        callback: Callable,
        *,
        include_indices: bool = True,
        prefer_realtime: bool = False,
        require_minute_freshness: bool = False,
    ):
        """Check freshness for `codes`; if stale, update then call `callback`.

        If data is already fresh, `callback` is called immediately.
        """
        stock_like_codes = [code for code in codes if not _is_etf_like_code(code)]
        etf_like_codes = [code for code in codes if _is_etf_like_code(code)]
        self._checked_stock_codes = _dedupe_codes(stock_like_codes)
        self._checked_etf_codes = _dedupe_codes(etf_like_codes)
        logger.info(
            "开始校验任务数据新鲜度: 股票 %d 只, ETF %d 只, 包含指数=%s",
            len(stock_like_codes),
            len(etf_like_codes),
            include_indices,
        )
        self.check_started.emit()
        self.status_notice.emit("info", "开始执行数据新鲜度检查")
        self._pending_callback = callback

        stale_stock_codes = []
        stale_etf_codes = []
        for code in codes:
            fresh, info = check_parquet_freshness(code)
            if not fresh:
                if _is_etf_like_code(code):
                    stale_etf_codes.append(code)
                else:
                    stale_stock_codes.append(code)

        stale_index_codes = []
        if include_indices:
            for idx_code in ["000001", "399001", "399006", "000300", "000905"]:
                fresh, _ = check_parquet_freshness(idx_code, subdir="index")
                if not fresh:
                    stale_index_codes.append(idx_code)

        total_stale = len(stale_stock_codes) + len(stale_etf_codes) + len(stale_index_codes)
        need_intraday_test = bool(prefer_realtime and _is_intraday_check_window())
        if total_stale == 0 and not need_intraday_test:
            logger.info("All data is fresh, proceeding directly")
            _refresh_loaded_market_data_caches(self._checked_stock_codes, self._checked_etf_codes)
            self.status_notice.emit("success", "日线完整性已通过，本地数据已是最新")
            self.update_finished.emit(True, "数据已是最新")
            self._pending_notice_message = ""
            QTimer.singleShot(0, callback)
            return

        self._stale_codes = stale_stock_codes
        self._stale_etf_codes = stale_etf_codes
        if total_stale > 0:
            logger.info(
                "发现数据待更新: 股票 %d 只, ETF %d 只, 指数 %d 个",
                len(stale_stock_codes),
                len(stale_etf_codes),
                len(stale_index_codes),
            )
            self.update_needed.emit(
                total_stale,
                f"发现 {len(stale_stock_codes)} 只股票 + {len(stale_etf_codes)} 只ETF + {len(stale_index_codes)} 个指数数据需更新"
            )
            self.status_notice.emit("info", "检测到本地日线未更新，准备执行增量更新")
        elif need_intraday_test:
            logger.info("日线已最新，继续校验盘中实时行情链路")

        # Run the (potentially slow) xtquant freshness test in a background
        # thread so the UI remains responsive.
        def _bg_test():
            logger.info("开始执行 xtquant 新鲜度拉取测试")
            try:
                report = evaluate_xtquant_data_freshness(
                    require_minute_freshness=require_minute_freshness
                )
            except Exception as exc:
                logger.exception("xtquant 新鲜度拉取测试异常")
                report = XtquantFreshnessReport(
                    False,
                    [FreshnessCheckResult("数据链路", False, f"xtquant 新鲜度测试异常: {exc}")],
                )
            logger.info("xtquant 新鲜度拉取测试结束: success=%s, %s", report.ok, report.summary)
            self.freshness_test_done.emit(
                report,
                stale_stock_codes,
                stale_etf_codes,
                stale_index_codes,
                callback,
            )

        threading.Thread(target=_bg_test, daemon=True).start()

    def _on_freshness_test_done(
        self,
        report: object,
        stale_stock_codes: List[str],
        stale_etf_codes: List[str],
        stale_index_codes: List[str],
        callback: Callable,
    ):
        """Called on main thread after background freshness test finishes."""
        if not isinstance(report, XtquantFreshnessReport):
            report = XtquantFreshnessReport(
                False,
                [FreshnessCheckResult("数据链路", False, f"未知的新鲜度检查结果: {report}")],
            )
        self._pending_notice_message = report.summary
        if not report.ok:
            logger.warning("xtquant freshness test failed: %s", report.summary)
            self.status_notice.emit("error", report.summary)
            self.xtquant_failed.emit(report.summary)
            self.update_finished.emit(False, report.summary)
            self._pending_notice_message = ""
            return
        if report.has_warning:
            self.status_notice.emit("warning", report.summary)
        else:
            self.status_notice.emit("success", report.summary)

        if not stale_stock_codes and not stale_etf_codes and not stale_index_codes:
            logger.info("xtquant OK, intraday realtime path is ready")
            _refresh_loaded_market_data_caches(self._checked_stock_codes, self._checked_etf_codes)
            self.update_finished.emit(True, report.summary)
            self._pending_callback = None
            self._pending_notice_message = ""
            QTimer.singleShot(0, callback)
            return

        total = len(stale_stock_codes) + len(stale_etf_codes) + len(stale_index_codes)
        logger.info("xtquant OK, starting data update for %d items", total)
        self._start_update(stale_stock_codes, stale_etf_codes, stale_index_codes, callback)

    def _start_update(
        self,
        stock_codes: List[str],
        etf_codes: List[str],
        index_codes: List[str],
        callback: Callable,
    ):
        from trading_app.data_updater import DataUpdateThread, ETFUpdateThread, IndexUpdateThread

        self._pending_callback = callback
        self._stale_codes = list(stock_codes)
        self._stale_etf_codes = list(etf_codes)
        self._stale_index_codes = list(index_codes)
        self._stock_update_done = not bool(stock_codes)
        self._etf_update_done = not bool(etf_codes)
        self._index_update_done = not bool(index_codes)
        self._stock_update_success = not bool(stock_codes)
        self._etf_update_success = not bool(etf_codes)
        self._index_update_success = not bool(index_codes)
        self._update_errors: List[str] = []

        if stock_codes:
            self._stock_thread = DataUpdateThread(
                data_dir=str(_DATA_DIR),
                stocklist_path=str(_STOCKLIST_PATH),
                codes=stock_codes,
                data_source="xtquant",
                period="1d",
                max_workers=4,
            )
            self._stock_thread.progress_updated.connect(
                lambda c, t, m: self.update_progress.emit(c, t, f"[股票] {m}")
            )
            self._stock_thread.finished_signal.connect(self._on_stock_update_done)
            self._stock_thread.start()

        if etf_codes:
            logger.info("启动ETF更新线程: %d 只ETF", len(etf_codes))
            self._etf_thread = ETFUpdateThread(
                data_dir=str(_DATA_DIR),
                codes=etf_codes,
                full_update=False,
            )
            self._etf_thread.progress_updated.connect(
                lambda c, t, m: self.update_progress.emit(c, t, f"[ETF] {m}")
            )
            self._etf_thread.finished_signal.connect(self._on_etf_update_done)
            self._etf_thread.start()

        if index_codes:
            logger.info("启动指数更新线程: %d 个指数", len(index_codes))
            self._index_thread = IndexUpdateThread(
                data_dir=str(_DATA_DIR),
                index_codes=[
                    {
                        "code": code,
                        "exchange": "SZ" if str(code).startswith("399") else "SH",
                    }
                    for code in index_codes
                ],
                full_update=False,
            )
            self._index_thread.progress_updated.connect(
                lambda c, t, m: self.update_progress.emit(c, t, f"[指数] {m}")
            )
            self._index_thread.finished_signal.connect(self._on_index_update_done)
            self._index_thread.start()

        if self._stock_update_done and self._etf_update_done and self._index_update_done:
            self._all_done()

    def _on_stock_update_done(self, success: bool, msg: str):
        logger.info("Stock update finished: success=%s, %s", success, msg)
        self._stock_update_done = True
        self._stock_update_success = bool(success)
        if not success and msg:
            self._update_errors.append(f"股票更新失败: {msg}")
        if self._etf_update_done and self._index_update_done:
            self._all_done()

    def _on_etf_update_done(self, success: bool, msg: str):
        logger.info("ETF update finished: success=%s, %s", success, msg)
        self._etf_update_done = True
        self._etf_update_success = bool(success)
        if not success and msg:
            self._update_errors.append(f"ETF更新失败: {msg}")
        if self._stock_update_done and self._index_update_done:
            self._all_done()

    def _on_index_update_done(self, success: bool, msg: str):
        logger.info("Index update finished: success=%s, %s", success, msg)
        self._index_update_done = True
        self._index_update_success = bool(success)
        if not success and msg:
            self._update_errors.append(f"指数更新失败: {msg}")
        if self._stock_update_done and self._etf_update_done:
            self._all_done()

    def _all_done(self):
        if not self._stock_update_success or not self._etf_update_success or not self._index_update_success:
            message = "；".join(self._update_errors) if self._update_errors else "数据更新失败"
            self.status_notice.emit("error", message)
            self.update_finished.emit(False, message)
            self._pending_callback = None
            self._pending_notice_message = ""
            return

        remaining_stock_codes = [
            code for code in getattr(self, "_stale_codes", [])
            if not check_parquet_freshness(code)[0]
        ]
        remaining_index_codes = [
            code for code in getattr(self, "_stale_index_codes", [])
            if not check_parquet_freshness(code, subdir="index")[0]
        ]
        remaining_etf_codes = [
            code for code in getattr(self, "_stale_etf_codes", [])
            if not check_parquet_freshness(code)[0]
        ]
        if remaining_stock_codes or remaining_etf_codes or remaining_index_codes:
            parts = []
            if remaining_stock_codes:
                preview = ", ".join(remaining_stock_codes[:5])
                if len(remaining_stock_codes) > 5:
                    preview += f" 等{len(remaining_stock_codes)}只股票"
                parts.append(f"股票数据仍未就绪: {preview}")
            if remaining_etf_codes:
                preview = ", ".join(remaining_etf_codes[:5])
                if len(remaining_etf_codes) > 5:
                    preview += f" 等{len(remaining_etf_codes)}只ETF"
                parts.append(f"ETF数据仍未就绪: {preview}")
            if remaining_index_codes:
                preview = ", ".join(remaining_index_codes[:5])
                if len(remaining_index_codes) > 5:
                    preview += f" 等{len(remaining_index_codes)}个指数"
                parts.append(f"指数数据仍未就绪: {preview}")
            message = "；".join(parts)
            self.status_notice.emit("error", message)
            self.update_finished.emit(False, message)
            self._pending_callback = None
            self._pending_notice_message = ""
            return

        final_message = "数据更新完成，开始 AI 分析"
        refreshed_stocks, refreshed_etfs = _refresh_loaded_market_data_caches(
            self._checked_stock_codes,
            self._checked_etf_codes,
        )
        if refreshed_stocks or refreshed_etfs:
            final_message = f"{final_message}；已刷新内存缓存 股票{refreshed_stocks}只 ETF{refreshed_etfs}只"
        if self._pending_notice_message:
            final_message = f"{final_message}；{self._pending_notice_message}"
        self.status_notice.emit("success", final_message)
        self.update_finished.emit(True, final_message)
        cb = self._pending_callback
        self._pending_callback = None
        self._pending_notice_message = ""
        if cb:
            QTimer.singleShot(500, cb)
