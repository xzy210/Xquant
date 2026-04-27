"""
ETF轮动实盘 - 数据更新器

轻量级ETF日线数据更新，仅更新 etf_pool 中的标的。
数据独立存放在 live_rotation/data/ 目录下，不依赖 trading_app。
"""
import sys
import logging
from pathlib import Path
from typing import List, Tuple, Optional

import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from common.daily_update_policy import get_daily_update_policy
from common.kline_update_engine import (
    run_xtquant_daily_history_precheck,
    update_rotation_etf_pool,
    update_rotation_single_etf,
)

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
    return run_xtquant_daily_history_precheck(
        action_hint="请先重启 miniQMT 后再更新/执行ETF轮动实盘。",
    )


def check_data_freshness(data_dir: Path, code: str) -> Tuple[bool, str]:
    """
    检查某只ETF的数据是否已包含最新交易日的K线。

    Returns:
        (is_fresh, last_date_str)
    """
    return get_daily_update_policy().check_daily_freshness(
        code,
        asset_type="etf",
        data_dir=data_dir,
        data_path=_parquet_path(data_dir, code),
    )


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
    return update_rotation_single_etf(code, data_dir, start=start, full=full)


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

    return update_rotation_etf_pool(
        codes,
        data_dir,
        full=full,
        progress_cb=progress_cb,
    )


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
