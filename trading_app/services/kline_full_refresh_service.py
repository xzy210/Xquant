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

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_STOCKLIST_PATH = _PROJECT_ROOT / "stocklist" / "stocklist.csv"
_ETF_CONFIG_PATH = _PROJECT_ROOT / "trading_app" / "config" / "etf_list.json"
_ROTATION_DATA_DIR = _PROJECT_ROOT / "live_rotation" / "data"

StatusCallback = Callable[[str], None]


def _noop_status(_msg: str) -> None:
    pass


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
        return fail == 0, msg

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
        return fail == 0, msg

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
        return fail == 0, msg

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
