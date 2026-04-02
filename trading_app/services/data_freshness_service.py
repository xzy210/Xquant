"""Data freshness check & auto-update before AI tasks.

Checks whether local parquet files contain today's (or latest trading day's) data.
If stale, tests xtquant connectivity via reconnect, then triggers DataUpdateThread
for the required stock codes + indices.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import threading

import pandas as pd
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_INDEX_DIR = _DATA_DIR / "index"
_STOCKLIST_PATH = _PROJECT_ROOT / "stocklist" / "stocklist.csv"


def _latest_trading_day() -> date:
    """Return the latest expected trading day (skip weekends)."""
    today = date.today()
    now = datetime.now()
    if today.weekday() == 5:
        return today - timedelta(days=1)
    if today.weekday() == 6:
        return today - timedelta(days=2)
    if now.hour < 15:
        yesterday = today - timedelta(days=1)
        if yesterday.weekday() == 6:
            return yesterday - timedelta(days=2)
        if yesterday.weekday() == 5:
            return yesterday - timedelta(days=1)
        return yesterday
    return today


def check_parquet_freshness(code: str, subdir: str = "") -> Tuple[bool, str]:
    """Check if a parquet file has data for the latest trading day.

    Returns (is_fresh, last_date_str).
    """
    if subdir:
        pq_path = _DATA_DIR / subdir / f"{code}.parquet"
    else:
        pq_path = _DATA_DIR / f"{code}.parquet"

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
    import sys, time
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

    # Step 2: use the exact same fetch_one that the main window uses
    test_code = "000001"
    expected_date = _latest_trading_day()
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=10)).strftime("%Y%m%d")

    try:
        fetch_kline_xtquant.fetch_one(
            test_code, start_date, end_date, _DATA_DIR, "1d"
        )
    except Exception as exc:
        return False, f"拉取测试股票 {test_code} 失败: {exc}"

    # Step 3: check the resulting parquet file
    time.sleep(0.3)
    fresh, info = check_parquet_freshness(test_code)
    if fresh:
        return True, f"xtquant 数据正常，{test_code} 最新日期 {info}（预期 ≥{expected_date}）"

    return (
        False,
        f"miniQMT 连接正常但无法拉取到最新数据！\n"
        f"测试股票 {test_code} 拉取后最新日期: {info}，预期: {expected_date}\n\n"
        "这通常是因为 miniQMT 客户端长时间未重启导致数据缓存过期。\n"
        "请完全关闭并重新启动 miniQMT 客户端，然后重试。",
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
        self._pending_callback: Optional[Callable] = None
        self._stale_codes: List[str] = []
        self.freshness_test_done.connect(self._on_freshness_test_done)

    def ensure_fresh_then_run(
        self,
        codes: List[str],
        callback: Callable,
        *,
        include_indices: bool = True,
    ):
        """Check freshness for `codes`; if stale, update then call `callback`.

        If data is already fresh, `callback` is called immediately.
        """
        logger.info("开始校验任务数据新鲜度: 股票 %d 只, 包含指数=%s", len(codes), include_indices)
        self.check_started.emit()
        self._pending_callback = callback

        stale_stock_codes = []
        for code in codes:
            fresh, info = check_parquet_freshness(code)
            if not fresh:
                stale_stock_codes.append(code)

        stale_index_codes = []
        if include_indices:
            for idx_code in ["000001", "399001", "399006", "000300", "000905"]:
                fresh, _ = check_parquet_freshness(idx_code, subdir="index")
                if not fresh:
                    stale_index_codes.append(idx_code)

        total_stale = len(stale_stock_codes) + len(stale_index_codes)
        if total_stale == 0:
            logger.info("All data is fresh, proceeding directly")
            self.update_finished.emit(True, "数据已是最新")
            QTimer.singleShot(0, callback)
            return

        self._stale_codes = stale_stock_codes
        logger.info(
            "发现数据待更新: 股票 %d 只, 指数 %d 个",
            len(stale_stock_codes),
            len(stale_index_codes),
        )
        self.update_needed.emit(
            total_stale,
            f"发现 {len(stale_stock_codes)} 只股票 + {len(stale_index_codes)} 个指数数据需更新"
        )

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

        total = len(stale_stock_codes) + len(stale_index_codes)
        logger.info("xtquant OK, starting data update for %d items", total)
        self._start_update(stale_stock_codes, stale_index_codes, callback)

    def _start_update(
        self,
        stock_codes: List[str],
        index_codes: List[str],
        callback: Callable,
    ):
        from trading_app.data_updater import DataUpdateThread, IndexUpdateThread

        self._pending_callback = callback
        self._stock_update_done = not bool(stock_codes)
        self._index_update_done = not bool(index_codes)
        self._stock_update_success = not bool(stock_codes)
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

        if self._stock_update_done and self._index_update_done:
            self._all_done()

    def _on_stock_update_done(self, success: bool, msg: str):
        logger.info("Stock update finished: success=%s, %s", success, msg)
        self._stock_update_done = True
        self._stock_update_success = bool(success)
        if not success and msg:
            self._update_errors.append(f"股票更新失败: {msg}")
        if self._index_update_done:
            self._all_done()

    def _on_index_update_done(self, success: bool, msg: str):
        logger.info("Index update finished: success=%s, %s", success, msg)
        self._index_update_done = True
        self._index_update_success = bool(success)
        if not success and msg:
            self._update_errors.append(f"指数更新失败: {msg}")
        if self._stock_update_done:
            self._all_done()

    def _all_done(self):
        if not self._stock_update_success or not self._index_update_success:
            message = "；".join(self._update_errors) if self._update_errors else "数据更新失败"
            self.update_finished.emit(False, message)
            self._pending_callback = None
            return

        self.update_finished.emit(True, "数据更新完成，开始 AI 分析")
        cb = self._pending_callback
        self._pending_callback = None
        if cb:
            QTimer.singleShot(500, cb)
