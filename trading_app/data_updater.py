import sys
import os
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal
from common.data_portal import get_data_portal
from trading_app.services.data_update_result import DataUpdateResult
from trading_app.services.market_data_policy import is_etf_like_code
# Import from scripts directory
import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from scripts import fetch_kline
    from scripts import fetch_kline_xtquant
except ImportError:
    # Direct import if scripts is in path
    sys.path.insert(0, str(project_root / "scripts"))
    import fetch_kline
    import fetch_kline_xtquant

# xtquant 延迟导入
try:
    from xtquant import xtdata
    HAS_XTQUANT = True
except ImportError:
    HAS_XTQUANT = False
    xtdata = None

# Tushare 延迟导入（可能未安装）
try:
    import tushare as ts
    HAS_TUSHARE = True
except ImportError:
    HAS_TUSHARE = False
    ts = None


_STOCK_FRESHNESS_PROBE_CODES = ("000001", "600000", "000333", "300750", "600519")


def _check_daily_parquet_freshness(parquet_path: Path) -> tuple[bool, str]:
    portal = get_data_portal()
    status = portal.get_daily_file_metadata(parquet_path)
    return status.is_fresh, portal.format_daily_status_message(status)


def _resolve_daily_parquet_path(data_dir: Path, code: str, subdir: str = "") -> Path:
    normalized = str(code or "").strip().upper().split(".", 1)[0]
    if subdir:
        return data_dir / subdir / f"{normalized}.parquet"
    if is_etf_like_code(normalized):
        etf_path = data_dir / "etf" / f"{normalized}.parquet"
        if etf_path.exists():
            return etf_path
    return data_dir / f"{normalized}.parquet"


def _check_update_output_freshness(data_dir: Path, code: str, subdir: str = "") -> tuple[bool, str]:
    portal = get_data_portal()
    normalized = str(code or "").strip().upper().split(".", 1)[0]
    if subdir:
        asset_type = "index" if subdir == "index" else "etf" if subdir == "etf" else "auto"
        status = portal.get_daily_file_metadata(
            data_dir / subdir / f"{normalized}.parquet",
            symbol=normalized,
            asset_type=asset_type,
            data_dir=data_dir / subdir,
        )
    else:
        status = portal.get_daily_metadata(
            normalized,
            asset_type="auto",
            data_dir=data_dir,
        )
    return status.is_fresh, portal.format_daily_status_message(status)


def _run_xtquant_daily_history_precheck() -> tuple[bool, str]:
    """Verify miniQMT can really fetch the latest daily K-line, not only connect."""
    try:
        from trading_app.services.data_freshness_service import test_xtquant_data_freshness
    except Exception as exc:
        return False, f"无法导入数据新鲜度检查服务: {exc}"

    ok, msg = test_xtquant_data_freshness(require_minute_freshness=False)
    if ok:
        return True, msg
    return False, (
        "miniQMT 历史K线数据源异常：连接可能正常，但无法拉取到最新交易日日线。"
        f"{msg}。请先重启 miniQMT 后再更新/执行策略。"
    )


def _check_project_parquet_freshness(code: str, subdir: str = "") -> tuple[bool, str]:
    from trading_app.services.data_freshness_service import check_parquet_freshness

    return check_parquet_freshness(code, subdir=subdir)


class DataUpdateThread(QThread):
    """
    Background thread for updating stock data.
    
    支持多数据源：
    - tushare: 使用 Tushare API（需要 token）
    - xtquant: 使用 xtquant/miniQMT（需要本地运行 miniQMT）
    
    支持多周期：
    - 1d: 日线
    - 1m/5m/15m/30m/60m: 分钟线
    """
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)  # success, message
    result_signal = pyqtSignal(object)  # DataUpdateResult

    def __init__(
        self,
        data_dir: str,
        stocklist_path: str,
        tushare_token: str = "",
        full_update: bool = False,
        exclude_boards: List[str] = None,
        max_workers: int = 6,
        codes: List[str] = None,
        start_date: str = None,
        data_source: str = "tushare",  # "tushare" or "xtquant"
        period: str = "1d",  # "1d", "1m", "5m", "15m", "30m", "60m"
    ):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.stocklist_path = Path(stocklist_path) if stocklist_path else None
        self.tushare_token = tushare_token
        self.full_update = full_update
        self.exclude_boards = set(exclude_boards) if exclude_boards else set()
        self.max_workers = max_workers
        self.codes = codes
        self.start_date = start_date
        self.data_source = data_source
        self.period = period
        self._is_running = True
        self.last_result = DataUpdateResult(ok=False, message="尚未执行")

    def _finish(self, result: DataUpdateResult):
        self.last_result = result
        self.result_signal.emit(result)
        self.finished_signal.emit(*result.to_legacy_tuple())

    def run(self):
        try:
            if self.data_source == "tushare":
                self._run_tushare()
            elif self.data_source == "xtquant":
                self._run_xtquant()
            else:
                self._finish(DataUpdateResult(ok=False, message=f"未知数据源: {self.data_source}"))
        except Exception as e:
            self._finish(DataUpdateResult(ok=False, message=f"发生错误: {str(e)}"))

    def _run_tushare(self):
        """使用 Tushare 数据源更新"""
        if not HAS_TUSHARE:
            self._finish(DataUpdateResult(ok=False, message="Tushare 未安装，请执行: pip install tushare"))
            return
        
        self.log_message.emit("正在初始化 Tushare API...")
        if not self.tushare_token:
            self._finish(DataUpdateResult(ok=False, message="Tushare Token 未提供"))
            return

        ts.set_token(self.tushare_token)
        pro = ts.pro_api()
        fetch_kline.set_api(pro)

        # 获取股票代码列表
        codes = self._get_stock_codes(fetch_kline.load_codes_from_stocklist)
        if codes is None:
            return

        total_stocks = len(codes)
        if not self.codes:
            self.log_message.emit(
                f"找到 {total_stocks} 只股票（排除板块: {', '.join(self.exclude_boards) or '无'}）。开始更新..."
            )

        # 确保输出目录存在
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 确定获取函数（Tushare 只支持日线）
        if self.period != "1d":
            self.log_message.emit("注意: Tushare 数据源仅支持日线，已自动切换为日线模式")
        
        fetch_func = fetch_kline.fetch_one_full if self.full_update else fetch_kline.fetch_one

        # 日期范围
        start_date = self.start_date if self.start_date else "20190101"
        end_date = pd.Timestamp.now().strftime("%Y%m%d")

        # 执行更新
        self._execute_update(codes, fetch_func, start_date, end_date)

    def _run_xtquant(self):
        """使用 xtquant/miniQMT 数据源更新"""
        if not fetch_kline_xtquant.check_xtquant_available():
            self._finish(DataUpdateResult(ok=False, message="xtquant 未安装，请从迅投官网下载安装"))
            return

        self.log_message.emit("正在检查 miniQMT 连接状态...")
        connected, msg = fetch_kline_xtquant.check_connection()
        if not connected:
            self._finish(DataUpdateResult(ok=False, message=f"miniQMT 连接失败: {msg}"))
            return
        
        self.log_message.emit("miniQMT 连接正常")

        self.log_message.emit("正在验证 miniQMT 历史K线是否更新到最新交易日...")
        history_ok, history_msg = _run_xtquant_daily_history_precheck()
        if not history_ok:
            self._finish(DataUpdateResult(ok=False, message=history_msg))
            return
        self.log_message.emit(history_msg)

        # 获取股票代码列表
        codes = self._get_stock_codes(fetch_kline_xtquant.load_codes_from_stocklist)
        if codes is None:
            return

        total_stocks = len(codes)
        period_name = self._get_period_name()
        if not self.codes:
            self.log_message.emit(
                f"找到 {total_stocks} 只股票（排除板块: {', '.join(self.exclude_boards) or '无'}）。"
                f"开始更新 {period_name} 数据..."
            )

        # 确保输出目录存在
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 确定获取函数
        if self.full_update:
            fetch_func = lambda code, start, end, out_dir: \
                fetch_kline_xtquant.fetch_one_full(code, start, end, out_dir, self.period)
        else:
            fetch_func = lambda code, start, end, out_dir: \
                fetch_kline_xtquant.fetch_one(code, start, end, out_dir, self.period)

        # 日期范围
        start_date = self.start_date if self.start_date else "20190101"
        end_date = pd.Timestamp.now().strftime("%Y%m%d")

        # 执行更新
        self._execute_update(codes, fetch_func, start_date, end_date)

    def _get_stock_codes(self, load_func) -> Optional[List[str]]:
        """获取股票代码列表"""
        if self.codes:
            self.log_message.emit(f"正在更新 {len(self.codes)} 只指定股票...")
            return self.codes
        elif self.stocklist_path:
            self.log_message.emit(f"正在从 {self.stocklist_path} 加载股票列表...")
            codes = load_func(self.stocklist_path, self.exclude_boards)
            if not codes:
                self._finish(DataUpdateResult(ok=False, message="未找到股票代码"))
                return None
            return codes
        else:
            self._finish(DataUpdateResult(ok=False, message="未提供股票代码或股票列表路径"))
            return None

    def _get_period_name(self) -> str:
        """获取周期的中文名称"""
        period_names = {
            "1d": "日线",
            "1m": "1分钟",
            "5m": "5分钟",
            "15m": "15分钟",
            "30m": "30分钟",
            "60m": "60分钟",
        }
        return period_names.get(self.period, self.period)

    def _execute_update(self, codes: List[str], fetch_func, start_date: str, end_date: str):
        """执行数据更新"""
        total_stocks = len(codes)
        completed_count = 0
        failed_items: List[str] = []

        # 分批提交任务，避免一次性向线程池塞入数千个任务导致无法及时停止
        batch_size = 50
        for i in range(0, total_stocks, batch_size):
            if not self._is_running:
                break
                
            batch_codes = codes[i:i+batch_size]
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(fetch_func, code, start_date, end_date, self.data_dir): code
                    for code in batch_codes
                }

                for future in as_completed(futures):
                    if not self._is_running:
                        executor.shutdown(wait=False)
                        self._finish(DataUpdateResult(ok=False, message="更新已取消"))
                        return

                    code = futures[future]
                    completed_count += 1
                    try:
                        future.result()
                        msg = f"已更新 {code} ({completed_count}/{total_stocks})"
                    except Exception as e:
                        msg = f"更新 {code} 失败: {str(e)}"
                        failed_items.append(f"{code}: {e}")
                        self.log_message.emit(msg)

                    self.progress_updated.emit(completed_count, total_stocks, msg)

        if not self._is_running:
            self._finish(DataUpdateResult(ok=False, message="更新已停止"))
        elif failed_items:
            preview = "；".join(failed_items[:8])
            suffix = f"；另有 {len(failed_items) - 8} 只" if len(failed_items) > 8 else ""
            self._finish(DataUpdateResult(
                ok=False,
                failed_codes=[item.split(":", 1)[0] for item in failed_items],
                message=f"部分股票更新失败: {preview}{suffix}",
            ))
        elif self.data_source == "xtquant" and self.period == "1d":
            check_codes = list(self.codes or [])
            if not check_codes:
                code_set = {str(code).strip().upper().split(".", 1)[0] for code in codes}
                check_codes = [code for code in _STOCK_FRESHNESS_PROBE_CODES if code in code_set]
            stale_items = []
            for code in check_codes:
                fresh, info = _check_update_output_freshness(self.data_dir, code)
                if not fresh:
                    stale_items.append(f"{code}: {info}")
            if stale_items:
                preview = "；".join(stale_items[:8])
                suffix = f"；另有 {len(stale_items) - 8} 只" if len(stale_items) > 8 else ""
                self._finish(DataUpdateResult(
                    ok=False,
                    stale_codes=[item.split(":", 1)[0] for item in stale_items],
                    message=f"股票数据更新后仍未达到最新交易日: {preview}{suffix}",
                ))
            else:
                self._finish(DataUpdateResult(ok=True, updated_stocks=completed_count, message="数据更新完成"))
        else:
            self._finish(DataUpdateResult(ok=True, updated_stocks=completed_count, message="数据更新完成"))

    def stop(self):
        self._is_running = False
        self.wait()


class ETFUpdateThread(QThread):
    """
    Background thread for updating ETF data.
    使用 xtquant/miniQMT 获取ETF数据
    """
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)  # success, message
    result_signal = pyqtSignal(object)  # DataUpdateResult

    def __init__(
        self,
        data_dir: str,
        etf_config_path: str = None,
        full_update: bool = False,
        max_workers: int = 4,
        codes: List[str] = None,
        start_date: str = None,
    ):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.etf_config_path = Path(etf_config_path) if etf_config_path else None
        self.full_update = full_update
        self.max_workers = max_workers
        self.codes = codes
        self.start_date = start_date
        self._is_running = True
        self.last_result = DataUpdateResult(ok=False, message="尚未执行")

    def _finish(self, result: DataUpdateResult):
        self.last_result = result
        self.result_signal.emit(result)
        self.finished_signal.emit(*result.to_legacy_tuple())

    def run(self):
        try:
            self._run_etf_update()
        except Exception as e:
            self._finish(DataUpdateResult(ok=False, message=f"发生错误: {str(e)}"))

    def _run_etf_update(self):
        """使用 xtquant 更新ETF数据"""
        if not fetch_kline_xtquant.check_xtquant_available():
            self._finish(DataUpdateResult(ok=False, message="xtquant 未安装，请从迅投官网下载安装"))
            return

        self.log_message.emit("正在检查 miniQMT 连接状态...")
        connected, msg = fetch_kline_xtquant.check_connection()
        if not connected:
            self._finish(DataUpdateResult(ok=False, message=f"miniQMT 连接失败: {msg}"))
            return
        
        self.log_message.emit("miniQMT 连接正常")

        self.log_message.emit("正在验证 miniQMT 历史K线是否更新到最新交易日...")
        history_ok, history_msg = _run_xtquant_daily_history_precheck()
        if not history_ok:
            self._finish(DataUpdateResult(ok=False, message=history_msg))
            return
        self.log_message.emit(history_msg)

        # 获取ETF代码列表
        strict_freshness_check = bool(self.codes)
        if self.codes:
            codes = self.codes
            self.log_message.emit(f"正在更新 {len(codes)} 只指定ETF...")
        elif self.etf_config_path and self.etf_config_path.exists():
            codes = fetch_kline_xtquant.load_etf_codes_from_config(self.etf_config_path)
            if not codes:
                self._finish(DataUpdateResult(ok=False, message="未找到ETF代码"))
                return
            self.log_message.emit(f"从配置文件找到 {len(codes)} 只ETF，开始更新...")
        else:
            self._finish(DataUpdateResult(ok=False, message="未提供ETF代码或配置文件路径"))
            return

        # 确保输出目录存在
        etf_dir = self.data_dir / "etf"
        etf_dir.mkdir(parents=True, exist_ok=True)

        # 确定获取函数
        if self.full_update:
            fetch_func = fetch_kline_xtquant.fetch_etf_one_full
        else:
            fetch_func = fetch_kline_xtquant.fetch_etf_one

        # 日期范围
        start_date = self.start_date if self.start_date else "20190101"
        end_date = pd.Timestamp.now().strftime("%Y%m%d")

        # 执行更新
        total_etfs = len(codes)
        completed_count = 0
        failed_items: List[str] = []

        batch_size = 20
        for i in range(0, total_etfs, batch_size):
            if not self._is_running:
                break
                
            batch_codes = codes[i:i+batch_size]
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(fetch_func, code, start_date, end_date, self.data_dir): code
                    for code in batch_codes
                }

                for future in as_completed(futures):
                    if not self._is_running:
                        executor.shutdown(wait=False)
                        self._finish(DataUpdateResult(ok=False, message="ETF更新已取消"))
                        return

                    code = futures[future]
                    completed_count += 1
                    try:
                        future.result()
                        msg = f"已更新ETF {code} ({completed_count}/{total_etfs})"
                    except Exception as e:
                        msg = f"更新ETF {code} 失败: {str(e)}"
                        failed_items.append(f"{code}: {e}")
                        self.log_message.emit(msg)

                    self.progress_updated.emit(completed_count, total_etfs, msg)

        if not self._is_running:
            self._finish(DataUpdateResult(ok=False, message="ETF更新已停止"))
        elif failed_items:
            preview = "；".join(failed_items[:8])
            suffix = f"；另有 {len(failed_items) - 8} 只" if len(failed_items) > 8 else ""
            self._finish(DataUpdateResult(
                ok=False,
                failed_codes=[item.split(":", 1)[0] for item in failed_items],
                message=f"部分ETF更新失败: {preview}{suffix}",
            ))
        else:
            stale_items = []
            for code in codes:
                fresh, info = _check_update_output_freshness(self.data_dir, code, subdir="etf")
                if not fresh:
                    stale_items.append(f"{code}: {info}")
            if stale_items:
                preview = "；".join(stale_items[:8])
                suffix = f"；另有 {len(stale_items) - 8} 只" if len(stale_items) > 8 else ""
                stale_codes = [item.split(":", 1)[0] for item in stale_items]
                if strict_freshness_check:
                    self._finish(DataUpdateResult(
                        ok=False,
                        stale_codes=stale_codes,
                        message=f"ETF数据更新后仍未达到最新交易日: {preview}{suffix}",
                    ))
                else:
                    warning = f"ETF数据更新完成，共更新 {completed_count} 只；{len(stale_items)} 只ETF无最新行情或为空文件，已跳过: {preview}{suffix}"
                    self.log_message.emit(f"⚠ {warning}")
                    self._finish(DataUpdateResult(
                        ok=True,
                        updated_etfs=completed_count,
                        message=warning,
                        details={"stale_etfs": "；".join(stale_codes[:50])},
                    ))
            else:
                self._finish(DataUpdateResult(ok=True, updated_etfs=completed_count, message=f"ETF数据更新完成，共更新 {completed_count} 只"))

    def stop(self):
        self._is_running = False
        self.wait()


class IndexUpdateThread(QThread):
    """
    Background thread for updating index data.
    Uses xtquant/miniQMT to get index data
    """
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)  # success, message
    result_signal = pyqtSignal(object)  # DataUpdateResult

    def __init__(
        self,
        data_dir: str,
        index_config_path: str = None,
        index_codes: Optional[List[Dict[str, Any]]] = None,
        full_update: bool = False,
        max_workers: int = 4,
        start_date: str = None,
    ):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.index_config_path = Path(index_config_path) if index_config_path else None
        self.index_codes = list(index_codes or [])
        self.full_update = full_update
        self.max_workers = max_workers
        self.start_date = start_date
        self._is_running = True
        self.last_result = DataUpdateResult(ok=False, message="尚未执行")

    def _finish(self, result: DataUpdateResult):
        self.last_result = result
        self.result_signal.emit(result)
        self.finished_signal.emit(*result.to_legacy_tuple())

    def run(self):
        try:
            self._run_index_update()
        except Exception as e:
            self._finish(DataUpdateResult(ok=False, message=f"Error occurred: {str(e)}"))

    def _run_index_update(self):
        """Update index data using xtquant"""
        if not fetch_kline_xtquant.check_xtquant_available():
            self._finish(DataUpdateResult(ok=False, message="xtquant not installed, please download from XT website"))
            return

        self.log_message.emit("Checking miniQMT connection status...")
        connected, msg = fetch_kline_xtquant.check_connection()
        if not connected:
            self._finish(DataUpdateResult(ok=False, message=f"miniQMT connection failed: {msg}"))
            return
        
        self.log_message.emit("miniQMT connected successfully")

        self.log_message.emit("正在验证 miniQMT 历史K线是否更新到最新交易日...")
        history_ok, history_msg = _run_xtquant_daily_history_precheck()
        if not history_ok:
            self._finish(DataUpdateResult(ok=False, message=history_msg))
            return
        self.log_message.emit(history_msg)

        # Get index list
        indices = self.index_codes or fetch_kline_xtquant.load_index_codes_from_config(self.index_config_path)
        if not indices:
            self._finish(DataUpdateResult(ok=False, message="No index codes found"))
            return
        
        self.log_message.emit(f"Found {len(indices)} indices to update...")

        # Ensure output directory exists
        index_dir = self.data_dir / "index"
        index_dir.mkdir(parents=True, exist_ok=True)

        # Determine fetch function
        if self.full_update:
            fetch_func = fetch_kline_xtquant.fetch_index_one_full
        else:
            fetch_func = fetch_kline_xtquant.fetch_index_one

        # Date range
        start_date = self.start_date if self.start_date else "19900101"
        end_date = pd.Timestamp.now().strftime("%Y%m%d")

        # Execute update
        total_indices = len(indices)
        completed_count = 0
        failed_items: List[str] = []

        batch_size = 10
        for i in range(0, total_indices, batch_size):
            if not self._is_running:
                break
                
            batch_indices = indices[i:i+batch_size]
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(
                        fetch_func, 
                        idx.get("code"), 
                        start_date, 
                        end_date, 
                        self.data_dir, 
                        "1d",
                        idx.get("exchange")
                    ): idx 
                    for idx in batch_indices
                }

                for future in as_completed(futures):
                    if not self._is_running:
                        executor.shutdown(wait=False)
                        self._finish(DataUpdateResult(ok=False, message="Index update cancelled"))
                        return

                    idx = futures[future]
                    completed_count += 1
                    try:
                        future.result()
                        msg = f"Updated index {idx.get('code')} ({idx.get('name', '')}) ({completed_count}/{total_indices})"
                    except Exception as e:
                        msg = f"Update index {idx.get('code')} failed: {str(e)}"
                        failed_items.append(f"{idx.get('code')}: {e}")
                        self.log_message.emit(msg)

                    self.progress_updated.emit(completed_count, total_indices, msg)

        if not self._is_running:
            self._finish(DataUpdateResult(ok=False, message="Index update stopped"))
        elif failed_items:
            preview = "；".join(failed_items[:8])
            suffix = f"；另有 {len(failed_items) - 8} 个" if len(failed_items) > 8 else ""
            self._finish(DataUpdateResult(
                ok=False,
                failed_codes=[item.split(":", 1)[0] for item in failed_items],
                message=f"部分指数更新失败: {preview}{suffix}",
            ))
        else:
            stale_items = []
            for idx in indices:
                code = str(idx.get("code", "") or "")
                fresh, info = _check_update_output_freshness(self.data_dir, code, subdir="index")
                if not fresh:
                    stale_items.append(f"{code}: {info}")
            if stale_items:
                preview = "；".join(stale_items[:8])
                suffix = f"；另有 {len(stale_items) - 8} 个" if len(stale_items) > 8 else ""
                self._finish(DataUpdateResult(
                    ok=False,
                    stale_codes=[item.split(":", 1)[0] for item in stale_items],
                    message=f"指数数据更新后仍未达到最新交易日: {preview}{suffix}",
                ))
            else:
                self._finish(DataUpdateResult(ok=True, updated_indices=completed_count, message=f"Index data update completed, updated {completed_count} indices"))

    def stop(self):
        self._is_running = False
        self.wait()
