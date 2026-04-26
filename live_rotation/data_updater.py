"""
ETF轮动实盘 - 数据更新器

轻量级ETF日线数据更新，仅更新 etf_pool 中的标的。
数据独立存放在 live_rotation/data/ 目录下，不依赖 trading_app。
"""
import sys
import logging
import datetime as dt
from pathlib import Path
from typing import List, Tuple, Optional

import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from common.data_portal import get_data_portal

logger = logging.getLogger(__name__)

# xtquant 延迟导入
_xtquant_checked = False
_has_xtquant = False


def _ensure_xtquant() -> bool:
    global _xtquant_checked, _has_xtquant
    if not _xtquant_checked:
        try:
            from xtquant import xtdata  # noqa: F401
            _has_xtquant = True
        except ImportError:
            _has_xtquant = False
        _xtquant_checked = True
    return _has_xtquant


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


def _parquet_path(data_dir: Path, code: str) -> Path:
    return data_dir / f"{code}.parquet"


def _run_xtquant_daily_history_precheck() -> Tuple[bool, str]:
    try:
        from trading_app.services.data_freshness_service import test_xtquant_data_freshness
    except Exception as exc:
        return False, f"无法导入数据新鲜度检查服务: {exc}"

    ok, msg = test_xtquant_data_freshness(require_minute_freshness=False)
    if ok:
        return True, msg
    return False, (
        "miniQMT 历史K线数据源异常：连接可能正常，但无法拉取到最新交易日日线。"
            f"{msg}。请先重启 miniQMT 后再更新/执行ETF轮动实盘。"
    )


def check_data_freshness(data_dir: Path, code: str) -> Tuple[bool, str]:
    """
    检查某只ETF的数据是否已包含最新交易日的K线。

    Returns:
        (is_fresh, last_date_str)
    """
    status = get_data_portal().get_daily_metadata(
        code,
        asset_type="etf",
        data_dir=data_dir,
    )
    return status.is_fresh, status.latest_date or ""


def update_single_etf(
    code: str,
    data_dir: Path,
    start: str = "20190101",
    full: bool = False,
) -> Tuple[bool, str]:
    """
    增量更新单只ETF日线数据到独立目录。

    Returns:
        (success, message)
    """
    if not _ensure_xtquant():
        return False, "xtquant 未安装"

    try:
        from scripts.fetch_kline_xtquant import fetch_etf_kline, validate
    except ImportError:
        return False, "无法导入 fetch_kline_xtquant"

    data_dir.mkdir(parents=True, exist_ok=True)
    pq = _parquet_path(data_dir, code)

    end = dt.date.today().strftime("%Y%m%d")
    incremental_start = start
    existing_df = None

    if not full and pq.exists():
        try:
            existing_df = pd.read_parquet(pq)
            if not existing_df.empty and "date" in existing_df.columns:
                incremental_start = existing_df["date"].max().strftime("%Y%m%d")
        except Exception:
            existing_df = None

    for attempt in range(1, 4):
        try:
            new_df = fetch_etf_kline(code, incremental_start, end, "1d")

            if new_df.empty:
                if existing_df is not None and not existing_df.empty:
                    fresh, last_str = check_data_freshness(data_dir, code)
                    if fresh:
                        return True, "无新数据"
                    return False, f"无新数据且本地仍未达到最新交易日，最新 {last_str or '未知'}"
                return False, "无法获取数据"

            if existing_df is not None and not existing_df.empty:
                merged = pd.concat([existing_df, new_df], ignore_index=True)
                new_df = merged.drop_duplicates(subset="date", keep="last")

            new_df = validate(new_df, "1d")
            new_df = new_df.sort_values("date").reset_index(drop=True)
            new_df.to_parquet(pq, index=False)
            fresh, last_str = check_data_freshness(data_dir, code)
            if not fresh:
                return False, f"更新后仍未达到最新交易日，最新 {last_str or '未知'}"
            return True, f"{len(new_df)} 条"

        except Exception as e:
            if attempt < 3:
                import time
                time.sleep(1)
            else:
                return False, f"3次重试失败: {e}"

    return False, "未知错误"


def update_etf_pool(
    codes: List[str],
    data_dir: Optional[Path] = None,
    full: bool = False,
    progress_cb=None,
) -> Tuple[int, int, List[str]]:
    """
    批量更新ETF池数据。

    Args:
        codes: ETF代码列表
        data_dir: 数据目录（默认 live_rotation/data/）
        full: 是否全量更新
        progress_cb: 进度回调 (current, total, code, message)

    Returns:
        (success_count, total, error_messages)
    """
    if data_dir is None:
        data_dir = _default_data_dir()

    if not _ensure_xtquant():
        return 0, len(codes), ["xtquant 未安装"]

    try:
        from scripts.fetch_kline_xtquant import check_connection
        connected, msg = check_connection()
        if not connected:
            return 0, len(codes), [f"miniQMT 连接失败: {msg}"]
    except ImportError:
        return 0, len(codes), ["无法导入 fetch_kline_xtquant"]

    history_ok, history_msg = _run_xtquant_daily_history_precheck()
    if not history_ok:
        return 0, len(codes), [history_msg]

    total = len(codes)
    success = 0
    errors = []

    for i, code in enumerate(codes):
        ok, msg = update_single_etf(code, data_dir, full=full)
        if ok:
            success += 1
        else:
            errors.append(f"{code}: {msg}")

        if progress_cb:
            progress_cb(i + 1, total, code, msg)

    return success, total, errors


def load_etf_parquet(code: str, data_dir: Optional[Path] = None) -> Optional[pd.DataFrame]:
    """
    从独立数据目录加载ETF日线数据。

    Returns:
        DataFrame (date/open/high/low/close/volume) 或 None
    """
    if data_dir is None:
        data_dir = _default_data_dir()

    pq = _parquet_path(data_dir, code)
    if not pq.exists():
        return None

    try:
        df = pd.read_parquet(pq)
    except Exception:
        return None

    if df.empty:
        return None

    df = df.sort_values("date").reset_index(drop=True)

    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"])
    return df if not df.empty else None


class ETFDataUpdateThread(QThread):
    """
    后台线程：更新 ETF 池数据。
    完成后发射 finished_signal(success_count, total, errors)。
    """
    progress = pyqtSignal(int, int, str, str)   # current, total, code, message
    finished_signal = pyqtSignal(int, int, list)  # success, total, errors

    def __init__(self, codes: List[str], data_dir: Path,
                 full: bool = False, parent=None):
        super().__init__(parent)
        self.codes = codes
        self.data_dir = data_dir
        self.full = full

    def run(self):
        s, t, errs = update_etf_pool(
            self.codes, self.data_dir, self.full,
            progress_cb=lambda *a: self.progress.emit(*a),
        )
        self.finished_signal.emit(s, t, errs)
