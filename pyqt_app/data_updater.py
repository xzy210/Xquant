import sys
import os
import logging
from pathlib import Path
from typing import List, Optional
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QThread, pyqtSignal

# Import from root directory
try:
    import fetch_kline
    import fetch_kline_xtquant
except ImportError:
    # If running from pyqt_app directory, add parent to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import fetch_kline
    import fetch_kline_xtquant

# Tushare 延迟导入（可能未安装）
try:
    import tushare as ts
    HAS_TUSHARE = True
except ImportError:
    HAS_TUSHARE = False
    ts = None


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

    def run(self):
        try:
            if self.data_source == "tushare":
                self._run_tushare()
            elif self.data_source == "xtquant":
                self._run_xtquant()
            else:
                self.finished_signal.emit(False, f"未知数据源: {self.data_source}")
        except Exception as e:
            self.finished_signal.emit(False, f"发生错误: {str(e)}")

    def _run_tushare(self):
        """使用 Tushare 数据源更新"""
        if not HAS_TUSHARE:
            self.finished_signal.emit(False, "Tushare 未安装，请执行: pip install tushare")
            return
        
        self.log_message.emit("正在初始化 Tushare API...")
        if not self.tushare_token:
            self.finished_signal.emit(False, "Tushare Token 未提供")
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
            self.finished_signal.emit(
                False, 
                "xtquant 未安装，请从迅投官网下载安装"
            )
            return

        self.log_message.emit("正在检查 miniQMT 连接状态...")
        connected, msg = fetch_kline_xtquant.check_connection()
        if not connected:
            self.finished_signal.emit(False, f"miniQMT 连接失败: {msg}")
            return
        
        self.log_message.emit("miniQMT 连接正常")

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
                self.finished_signal.emit(False, "未找到股票代码")
                return None
            return codes
        else:
            self.finished_signal.emit(False, "未提供股票代码或股票列表路径")
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
                        self.finished_signal.emit(False, "更新已取消")
                        return

                    code = futures[future]
                    completed_count += 1
                    try:
                        future.result()
                        msg = f"已更新 {code} ({completed_count}/{total_stocks})"
                    except Exception as e:
                        msg = f"更新 {code} 失败: {str(e)}"
                        self.log_message.emit(msg)

                    self.progress_updated.emit(completed_count, total_stocks, msg)

        if not self._is_running:
            self.finished_signal.emit(False, "更新已停止")
        else:
            self.finished_signal.emit(True, "数据更新完成")

    def stop(self):
        self._is_running = False
        self.wait()
