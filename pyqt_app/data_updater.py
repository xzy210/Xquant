import sys
import os
import logging
from pathlib import Path
from typing import List, Optional
import pandas as pd
import tushare as ts
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QThread, pyqtSignal

# Import from root directory
try:
    import fetch_kline
except ImportError:
    # If running from pyqt_app directory, add parent to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import fetch_kline

class DataUpdateThread(QThread):
    """
    Background thread for updating stock data.
    """
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)  # success, message

    def __init__(self, data_dir: str, stocklist_path: str, tushare_token: str, 
                 full_update: bool = False, exclude_boards: List[str] = None, max_workers: int = 6):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.stocklist_path = Path(stocklist_path)
        self.tushare_token = tushare_token
        self.full_update = full_update
        self.exclude_boards = set(exclude_boards) if exclude_boards else set()
        self.max_workers = max_workers
        self._is_running = True

    def run(self):
        try:
            self.log_message.emit("Initializing Tushare API...")
            if not self.tushare_token:
                self.finished_signal.emit(False, "Tushare token is missing.")
                return

            ts.set_token(self.tushare_token)
            pro = ts.pro_api()
            fetch_kline.set_api(pro)

            self.log_message.emit(f"Loading stock list from {self.stocklist_path}...")
            # Exclude boards logic
            codes = fetch_kline.load_codes_from_stocklist(self.stocklist_path, self.exclude_boards)
            
            if not codes:
                self.finished_signal.emit(False, "No stock codes found.")
                return

            total_stocks = len(codes)
            self.log_message.emit(f"Found {total_stocks} stocks (Excluded: {', '.join(self.exclude_boards) or 'None'}). Starting update...")

            # Ensure output directory exists
            self.data_dir.mkdir(parents=True, exist_ok=True)

            # Determine fetch function
            fetch_func = fetch_kline.fetch_one_full if self.full_update else fetch_kline.fetch_one
            
            # Date range
            start_date = "20190101"
            end_date = pd.Timestamp.now().strftime("%Y%m%d")

            completed_count = 0
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(fetch_func, code, start_date, end_date, self.data_dir): code 
                    for code in codes
                }
                
                for future in as_completed(futures):
                    if not self._is_running:
                        executor.shutdown(wait=False)
                        return

                    code = futures[future]
                    completed_count += 1
                    try:
                        future.result()
                        msg = f"Updated {code} ({completed_count}/{total_stocks})"
                    except Exception as e:
                        msg = f"Failed to update {code}: {str(e)}"
                        self.log_message.emit(msg)
                    
                    self.progress_updated.emit(completed_count, total_stocks, msg)

            self.finished_signal.emit(True, "Data update completed successfully.")

        except Exception as e:
            self.finished_signal.emit(False, f"An error occurred: {str(e)}")

    def stop(self):
        self._is_running = False
        self.wait()