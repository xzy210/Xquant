"""End-of-day full K-line refresh service.

Synchronously refreshes all stock, ETF, and index K-line data using
xtquant/miniQMT with ``full_update=True`` so that forward-adjusted
(前复权) prices are recalculated from scratch.

Designed to be called from a background QThread (e.g. _EndOfDayWorker),
NOT from the main/GUI thread.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from trading_app.services.market_data_policy import latest_expected_trading_day

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_STOCKLIST_PATH = _PROJECT_ROOT / "stocklist" / "stocklist.csv"
_ETF_CONFIG_PATH = _PROJECT_ROOT / "trading_app" / "config" / "etf_list.json"
_ROTATION_DATA_DIR = _PROJECT_ROOT / "live_rotation" / "data"
_STOCK_FRESHNESS_PROBE_CODES = ("000001", "600000", "000333", "300750", "600519")

StatusCallback = Callable[[str], None]


def _noop_status(_msg: str) -> None:
    pass


def _latest_expected_trading_day():
    return latest_expected_trading_day()


def _check_daily_parquet_freshness(parquet_path: Path) -> Tuple[bool, str]:
    if not parquet_path.exists():
        return False, "文件不存在"
    try:
        df = pd.read_parquet(parquet_path, columns=["date"])
        if df.empty:
            return False, "空文件"
        last_date = pd.Timestamp(df["date"].max()).date()
        expected = _latest_expected_trading_day()
        return last_date >= expected, str(last_date)
    except Exception as exc:
        return False, f"读取失败: {exc}"


def _refresh_loaded_caches_after_full_refresh(data_dir: Path) -> Tuple[int, int]:
    """Reload already-loaded stock/ETF caches after full parquet overwrite."""
    stock_count = 0
    etf_count = 0
    try:
        from common.data_loader import get_etf_cache, get_etf_list, get_stock_cache, get_stock_list
    except Exception as exc:
        logger.warning("导入行情缓存管理器失败，跳过全量刷新后的缓存同步: %s", exc)
        return stock_count, etf_count

    try:
        stock_cache = get_stock_cache()
        if stock_cache.is_loaded():
            stock_codes = get_stock_list(str(data_dir))
            stock_count = stock_cache.reload_all(
                data_dir=str(data_dir),
                stock_codes=stock_codes,
                max_workers=8,
            )
    except Exception as exc:
        logger.warning("全量刷新后同步股票缓存失败: %s", exc)

    try:
        etf_cache = get_etf_cache()
        if etf_cache.is_loaded():
            etf_codes = get_etf_list(str(data_dir))
            etf_count = etf_cache.reload_all(
                data_dir=str(data_dir),
                etf_codes=etf_codes,
                max_workers=8,
            )
    except Exception as exc:
        logger.warning("全量刷新后同步ETF缓存失败: %s", exc)

    if stock_count or etf_count:
        logger.info("全量刷新后已同步内存缓存: 股票 %d 只, ETF %d 只", stock_count, etf_count)
    return stock_count, etf_count


class KlineFullRefreshService:
    """Coordinate a full (前复权) K-line refresh for stocks, ETFs and indices.

    All public methods are **blocking** – call them from a worker thread.
    """

    def __init__(
        self,
        *,
        data_dir: Path = _DATA_DIR,
        stocklist_path: Path = _STOCKLIST_PATH,
        etf_config_path: Path = _ETF_CONFIG_PATH,
        rotation_data_dir: Path = _ROTATION_DATA_DIR,
        rotation_etf_pool: Optional[List[str]] = None,
        max_workers: int = 4,
        start_date: str = "20190101",
    ) -> None:
        self.data_dir = data_dir
        self.stocklist_path = stocklist_path
        self.etf_config_path = etf_config_path
        self.rotation_data_dir = rotation_data_dir
        self.rotation_etf_pool = list(rotation_etf_pool or [])
        self.max_workers = max_workers
        self.start_date = start_date

    def run_full_refresh(
        self,
        status_cb: Optional[StatusCallback] = None,
    ) -> Tuple[bool, str]:
        """Run full-overwrite refresh for stocks + ETFs + indices.

        Returns ``(overall_success, summary_message)``.
        """
        cb = status_cb or _noop_status
        t0 = time.time()
        results: Dict[str, Tuple[bool, str]] = {}

        ok, msg = self._check_xtquant(cb)
        if not ok:
            return False, msg

        ok, msg = self._check_xtquant_daily_history(cb)
        if not ok:
            return False, msg

        results["stock"] = self._refresh_stocks(cb)
        results["etf"] = self._refresh_etfs(cb)
        results["index"] = self._refresh_indices(cb)
        results["rotation_etf"] = self._refresh_rotation_etfs(cb)

        elapsed = time.time() - t0
        failed = [k for k, (ok, _) in results.items() if not ok]
        parts = [f"{k}: {msg}" for k, (_, msg) in results.items()]
        summary = " | ".join(parts) + f" ({elapsed:.0f}s)"
        if failed:
            cb(f"⚠ 部分数据刷新失败: {', '.join(failed)}")
            return False, summary
        cache_stock_count, cache_etf_count = _refresh_loaded_caches_after_full_refresh(self.data_dir)
        if cache_stock_count or cache_etf_count:
            summary = f"{summary} | cache: 股票{cache_stock_count} ETF{cache_etf_count}"
        cb(f"✅ 全量K线刷新完成 ({elapsed:.0f}s)")
        return True, summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_xtquant(cb: StatusCallback) -> Tuple[bool, str]:
        try:
            from scripts import fetch_kline_xtquant
        except ImportError:
            return False, "fetch_kline_xtquant 导入失败"

        if not fetch_kline_xtquant.check_xtquant_available():
            return False, "xtquant 未安装"

        cb("检查 miniQMT 连接...")
        connected, msg = fetch_kline_xtquant.check_connection()
        if not connected:
            return False, f"miniQMT 连接失败: {msg}"
        cb("miniQMT 连接正常")
        return True, "ok"

    @staticmethod
    def _check_xtquant_daily_history(cb: StatusCallback) -> Tuple[bool, str]:
        cb("验证 miniQMT 历史K线是否更新到最新交易日...")
        try:
            from trading_app.services.data_freshness_service import test_xtquant_data_freshness
        except Exception as exc:
            return False, f"数据新鲜度检查服务导入失败: {exc}"

        ok, msg = test_xtquant_data_freshness(require_minute_freshness=False)
        if ok:
            cb(msg)
            return True, msg
        return False, (
            "miniQMT 历史K线数据源异常：连接可能正常，但无法拉取到最新交易日日线。"
            f"{msg}。请先重启 miniQMT 后再执行全量K线刷新。"
        )

    def _validate_stock_outputs(self, codes: List[str]) -> List[str]:
        code_set = {str(code).strip().upper().split(".", 1)[0] for code in codes}
        check_codes = [code for code in _STOCK_FRESHNESS_PROBE_CODES if code in code_set]
        stale_items: List[str] = []
        for code in check_codes:
            fresh, info = _check_daily_parquet_freshness(self.data_dir / f"{code}.parquet")
            if not fresh:
                stale_items.append(f"{code}: {info}")
        return stale_items

    def _validate_etf_outputs(self, codes: List[str]) -> List[str]:
        stale_items: List[str] = []
        for code in codes:
            fresh, info = _check_daily_parquet_freshness(self.data_dir / "etf" / f"{code}.parquet")
            if not fresh:
                stale_items.append(f"{code}: {info}")
        return stale_items

    def _validate_index_outputs(self, indices: List[dict]) -> List[str]:
        stale_items: List[str] = []
        for idx in indices:
            code = str(idx.get("code", "") or "")
            fresh, info = _check_daily_parquet_freshness(self.data_dir / "index" / f"{code}.parquet")
            if not fresh:
                stale_items.append(f"{code}: {info}")
        return stale_items

    def _validate_rotation_etf_outputs(self, codes: List[str]) -> List[str]:
        stale_items: List[str] = []
        for code in codes:
            fresh, info = _check_daily_parquet_freshness(self.rotation_data_dir / f"{code}.parquet")
            if not fresh:
                stale_items.append(f"{code}: {info}")
        return stale_items

    @staticmethod
    def _format_stale_items(label: str, stale_items: List[str], unit: str) -> str:
        preview = "；".join(stale_items[:8])
        suffix = f"；另有 {len(stale_items) - 8} {unit}" if len(stale_items) > 8 else ""
        return f"{label}更新后仍未达到最新交易日: {preview}{suffix}"

    def _refresh_stocks(self, cb: StatusCallback) -> Tuple[bool, str]:
        from scripts import fetch_kline_xtquant

        codes = self._load_stock_codes()
        if not codes:
            return True, "无股票需更新"

        total = len(codes)
        cb(f"🔄 全量刷新 {total} 只股票K线...")
        end_date = pd.Timestamp.now().strftime("%Y%m%d")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        success, fail = 0, 0

        for batch_start in range(0, total, 50):
            batch = codes[batch_start : batch_start + 50]
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {
                    pool.submit(
                        fetch_kline_xtquant.fetch_one_full,
                        code, self.start_date, end_date, self.data_dir, "1d",
                    ): code
                    for code in batch
                }
                for future in as_completed(futures):
                    code = futures[future]
                    try:
                        future.result()
                        success += 1
                    except Exception as exc:
                        fail += 1
                        logger.warning("股票 %s 全量拉取失败: %s", code, exc)
                    done = success + fail
                    if done % 20 == 0 or done == total:
                        cb(f"股票K线: {done}/{total}")

        msg = f"股票 {success}/{total} 成功"
        if fail:
            msg += f" ({fail} 失败)"
            return False, msg
        stale_items = self._validate_stock_outputs(codes)
        if stale_items:
            return False, self._format_stale_items("股票", stale_items, "只")
        return True, msg

    def _refresh_etfs(self, cb: StatusCallback) -> Tuple[bool, str]:
        from scripts import fetch_kline_xtquant

        codes = self._load_etf_codes()
        if not codes:
            return True, "无ETF需更新"

        total = len(codes)
        cb(f"🔄 全量刷新 {total} 只ETF K线...")
        end_date = pd.Timestamp.now().strftime("%Y%m%d")
        (self.data_dir / "etf").mkdir(parents=True, exist_ok=True)
        success, fail = 0, 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(
                    fetch_kline_xtquant.fetch_etf_one_full,
                    code, self.start_date, end_date, self.data_dir, "1d",
                ): code
                for code in codes
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    future.result()
                    success += 1
                except Exception as exc:
                    fail += 1
                    logger.warning("ETF %s 全量拉取失败: %s", code, exc)
                done = success + fail
                if done % 10 == 0 or done == total:
                    cb(f"ETF K线: {done}/{total}")

        msg = f"ETF {success}/{total} 成功"
        if fail:
            msg += f" ({fail} 失败)"
            return False, msg
        stale_items = self._validate_etf_outputs(codes)
        if stale_items:
            return False, self._format_stale_items("ETF", stale_items, "只")
        return True, msg

    def _refresh_indices(self, cb: StatusCallback) -> Tuple[bool, str]:
        from scripts import fetch_kline_xtquant

        indices = fetch_kline_xtquant.load_index_codes_from_config(None)
        if not indices:
            return True, "无指数需更新"

        total = len(indices)
        cb(f"🔄 全量刷新 {total} 个指数K线...")
        end_date = pd.Timestamp.now().strftime("%Y%m%d")
        (self.data_dir / "index").mkdir(parents=True, exist_ok=True)
        success, fail = 0, 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(
                    fetch_kline_xtquant.fetch_index_one_full,
                    idx["code"], "19900101", end_date,
                    self.data_dir, "1d", idx.get("exchange"),
                ): idx["code"]
                for idx in indices
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    future.result()
                    success += 1
                except Exception as exc:
                    fail += 1
                    logger.warning("指数 %s 全量拉取失败: %s", code, exc)

        msg = f"指数 {success}/{total} 成功"
        if fail:
            msg += f" ({fail} 失败)"
            cb(msg)
            return False, msg
        stale_items = self._validate_index_outputs(indices)
        if stale_items:
            msg = self._format_stale_items("指数", stale_items, "个")
            cb(msg)
            return False, msg
        cb(msg)
        return True, msg

    def _refresh_rotation_etfs(self, cb: StatusCallback) -> Tuple[bool, str]:
        codes = self.rotation_etf_pool
        if not codes:
            return True, "无轮动ETF需更新"

        cb(f"🔄 全量刷新 {len(codes)} 只轮动ETF K线...")
        try:
            from live_rotation.data_updater import update_etf_pool

            s, t, errs = update_etf_pool(
                codes,
                self.rotation_data_dir,
                full=True,
                progress_cb=lambda cur, tot, code, _msg: cb(f"轮动ETF: {cur}/{tot} ({code})"),
            )
            if errs:
                for e in errs:
                    logger.warning("轮动ETF更新错误: %s", e)
                return False, f"轮动ETF {s}/{t} 成功 ({len(errs)} 错误)"
            stale_items = self._validate_rotation_etf_outputs(codes)
            if stale_items:
                return False, self._format_stale_items("轮动ETF", stale_items, "只")
            return True, f"轮动ETF {s}/{t} 成功"
        except Exception as exc:
            logger.exception("轮动ETF全量刷新异常")
            return False, f"轮动ETF异常: {exc}"

    # ------------------------------------------------------------------
    # Code list loaders
    # ------------------------------------------------------------------

    def _load_stock_codes(self) -> List[str]:
        try:
            from scripts.fetch_kline_xtquant import load_codes_from_stocklist

            if self.stocklist_path.exists():
                return load_codes_from_stocklist(self.stocklist_path)
        except Exception as exc:
            logger.warning("加载 stocklist 失败: %s", exc)
        return []

    def _load_etf_codes(self) -> List[str]:
        """Load ETF codes from config or by scanning existing parquet files."""
        try:
            from scripts.fetch_kline_xtquant import load_etf_codes_from_config

            if self.etf_config_path.exists():
                codes = load_etf_codes_from_config(self.etf_config_path)
                if codes:
                    return codes
        except Exception:
            pass

        etf_dir = self.data_dir / "etf"
        if etf_dir.is_dir():
            return sorted(
                p.stem for p in etf_dir.glob("*.parquet") if p.stem.isdigit()
            )
        return []
