"""Data freshness check & auto-update before AI tasks.

Checks whether local parquet files contain today's (or latest trading day's) data.
If stale, tests xtquant connectivity via reconnect, then triggers DataUpdateThread
for the required stock codes + indices.
"""
from __future__ import annotations

import logging
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


def _test_xtquant_daily_freshness(fetch_kline_xtquant) -> Tuple[bool, str]:
    test_code = "000001"
    expected_date = _latest_trading_day()
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=10)).strftime("%Y%m%d")

    try:
        fetch_kline_xtquant.fetch_one(test_code, start_date, end_date, _DATA_DIR, "1d")
    except Exception as exc:
        return False, f"拉取测试股票 {test_code} 日线失败: {exc}"

    import time
    time.sleep(0.3)
    fresh, info = check_parquet_freshness(test_code)
    if fresh:
        return True, f"日线正常，{test_code} 最新日期 {info}（预期 >={expected_date}）"
    return (
        False,
        f"miniQMT 连接正常但无法拉取到最新日线数据：{test_code} 最新日期 {info}，预期 {expected_date}",
    )


def _test_xtquant_intraday_freshness(fetch_kline_xtquant) -> Tuple[bool, str]:
    test_code = "000001"
    today_str = date.today().strftime("%Y%m%d")
    now = datetime.now()
    expected_cutoff = _intraday_expected_cutoff(now)

    try:
        df = fetch_kline_xtquant.get_minute_data(test_code, today_str, "1m")
    except Exception as exc:
        return False, f"拉取测试股票 {test_code} 分时失败: {exc}"

    if df is None or df.empty or "time" not in df.columns:
        return False, f"未获取到 {test_code} 当日分时数据"

    latest_dt = pd.Timestamp(df["time"].max()).to_pydatetime()
    if latest_dt.date() != now.date():
        return False, f"{test_code} 分时最新时间 {latest_dt:%Y-%m-%d %H:%M}，不属于今天"
    if latest_dt < expected_cutoff:
        return (
            False,
            f"{test_code} 分时最新时间 {latest_dt:%H:%M}，低于预期阈值 {expected_cutoff:%H:%M}",
        )
    return True, f"分时正常，{test_code} 最新时间 {latest_dt:%Y-%m-%d %H:%M}"


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


def test_xtquant_data_freshness() -> Tuple[bool, str]:
    """Test that xtquant can actually pull today's data by running the exact
    same fetch pipeline as the main window's "同步数据" button.

    This catches the common case where miniQMT has been open for many days
    and silently returns stale cached data despite the connection appearing
    healthy.

    Returns (success, message).
    """
    import sys
    project_root = _PROJECT_ROOT
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from scripts import fetch_kline_xtquant
    except ImportError:
        return False, "fetch_kline_xtquant 模块导入失败"

    if not fetch_kline_xtquant.check_xtquant_available():
        return False, "xtquant 未安装"

    # Step 1: check connection (includes reconnect internally)
    connected, conn_msg = fetch_kline_xtquant.check_connection()
    if not connected:
        return False, f"miniQMT 连接失败: {conn_msg}"

    daily_ok, daily_msg = _test_xtquant_daily_freshness(fetch_kline_xtquant)
    if not daily_ok:
        return (
            False,
            daily_msg + "\n\n这通常是因为 miniQMT 客户端长时间未重启导致数据缓存过期。\n请完全关闭并重新启动 miniQMT 客户端，然后重试。",
        )

    if not _is_intraday_check_window():
        return True, f"xtquant 数据正常，{daily_msg}"

    intraday_ok, intraday_msg = _test_xtquant_intraday_freshness(fetch_kline_xtquant)
    if intraday_ok:
        return True, f"xtquant 数据正常，{daily_msg}；{intraday_msg}"

    return (
        False,
        intraday_msg + "\n\n日线检测已通过，但盘中分时数据未达到新鲜度要求。建议重启 miniQMT 后重试。",
    )


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
    xtquant_failed = pyqtSignal(str)
    freshness_test_done = pyqtSignal(bool, str, list, list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._update_thread = None
        self._etf_thread = None
        self._pending_callback: Optional[Callable] = None
        self._stale_codes: List[str] = []
        self._stale_etf_codes: List[str] = []
        self.freshness_test_done.connect(self._on_freshness_test_done)

    def ensure_fresh_then_run(
        self,
        codes: List[str],
        callback: Callable,
        *,
        include_indices: bool = True,
        prefer_realtime: bool = False,
    ):
        """Check freshness for `codes`; if stale, update then call `callback`.

        If data is already fresh, `callback` is called immediately.
        """
        stock_like_codes = [code for code in codes if not _is_etf_like_code(code)]
        etf_like_codes = [code for code in codes if _is_etf_like_code(code)]
        logger.info(
            "开始校验任务数据新鲜度: 股票 %d 只, ETF %d 只, 包含指数=%s",
            len(stock_like_codes),
            len(etf_like_codes),
            include_indices,
        )
        self.check_started.emit()
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
            self.update_finished.emit(True, "数据已是最新")
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
        elif need_intraday_test:
            logger.info("日线已最新，继续校验盘中实时行情链路")

        # Run the (potentially slow) xtquant freshness test in a background
        # thread so the UI remains responsive.
        def _bg_test():
            logger.info("开始执行 xtquant 新鲜度拉取测试")
            try:
                ok, msg = test_xtquant_data_freshness()
            except Exception as exc:
                logger.exception("xtquant 新鲜度拉取测试异常")
                ok, msg = False, f"xtquant 新鲜度测试异常: {exc}"
            logger.info("xtquant 新鲜度拉取测试结束: success=%s, %s", ok, msg)
            self.freshness_test_done.emit(
                ok,
                msg,
                stale_stock_codes,
                stale_index_codes,
                callback,
            )

        threading.Thread(target=_bg_test, daemon=True).start()

    def _on_freshness_test_done(
        self,
        ok: bool,
        msg: str,
        stale_stock_codes: List[str],
        stale_index_codes: List[str],
        callback: Callable,
    ):
        """Called on main thread after background freshness test finishes."""
        if not ok:
            logger.warning("xtquant freshness test failed: %s", msg)
            self.xtquant_failed.emit(msg)
            self.update_finished.emit(False, msg)
            return

        stale_etf_codes = [code for code in stale_stock_codes if _is_etf_like_code(code)]
        stale_stock_codes = [code for code in stale_stock_codes if not _is_etf_like_code(code)]

        if not stale_stock_codes and not stale_etf_codes and not stale_index_codes:
            logger.info("xtquant OK, intraday realtime path is ready")
            self.update_finished.emit(True, msg)
            self._pending_callback = None
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
            self.update_finished.emit(False, message)
            self._pending_callback = None
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
            self.update_finished.emit(False, "；".join(parts))
            self._pending_callback = None
            return

        self.update_finished.emit(True, "数据更新完成，开始 AI 分析")
        cb = self._pending_callback
        self._pending_callback = None
        if cb:
            QTimer.singleShot(500, cb)
