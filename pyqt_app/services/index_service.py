# index_service.py - Index data service
"""
Index data service module for fetching and caching index (benchmark) data
Supports major indices like SSE 50, CSI 300, etc.
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import pandas as pd

logger = logging.getLogger(__name__)

# Major indices list
INDEX_LIST = [
    {"code": "000001", "name": "上证指数", "market": "sh"},
    {"code": "000016", "name": "上证50", "market": "sh"},
    {"code": "000300", "name": "沪深300", "market": "sh"},
    {"code": "000905", "name": "中证500", "market": "sh"},
    {"code": "000852", "name": "中证1000", "market": "sh"},
    {"code": "399001", "name": "深证成指", "market": "sz"},
    {"code": "399006", "name": "创业板指", "market": "sz"},
    {"code": "399005", "name": "中小板指", "market": "sz"},
    {"code": "000688", "name": "科创50", "market": "sh"},
]


def get_index_list() -> List[Dict[str, str]]:
    """
    Get the list of available indices
    
    Returns:
        List of dict with code, name, market
    """
    return INDEX_LIST.copy()


def get_index_name_map() -> Dict[str, str]:
    """
    Get index code to name mapping
    
    Returns:
        {index_code: index_name} dict
    """
    return {item["code"]: item["name"] for item in INDEX_LIST}


def _fetch_index_data_akshare(
    code: str,
    start_date: str = "20100101",
    end_date: Optional[str] = None
) -> Optional[pd.DataFrame]:
    """
    Fetch index data from akshare
    
    Args:
        code: Index code (e.g., "000001", "000300")
        start_date: Start date in YYYYMMDD format
        end_date: End date in YYYYMMDD format (default: today)
    
    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed. Please run: pip install akshare")
        return None
    
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    
    try:
        # Use akshare to get index daily data
        df = ak.stock_zh_index_daily(symbol=f"sh{code}" if code.startswith("0") else f"sz{code}")
        
        if df is None or df.empty:
            logger.warning(f"No data returned for index {code}")
            return None
        
        # Standardize column names
        # akshare returns: date, open, high, low, close, volume
        df = df.rename(columns={
            "date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume"
        })
        
        # Ensure date column is datetime type
        df["date"] = pd.to_datetime(df["date"])
        
        # Filter by date range
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        
        # Keep only necessary columns
        required_cols = ["date", "open", "high", "low", "close", "volume"]
        df = df[[c for c in required_cols if c in df.columns]]
        
        # Convert numeric columns
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df.sort_values("date").reset_index(drop=True)
        
        return df
        
    except Exception as e:
        logger.error(f"Failed to fetch index data for {code}: {e}")
        return None


def _fetch_index_data_akshare_em(
    code: str,
    start_date: str = "20100101",
    end_date: Optional[str] = None
) -> Optional[pd.DataFrame]:
    """
    Fetch index data from akshare using East Money interface
    More reliable for recent data
    
    Args:
        code: Index code (e.g., "000001", "000300")
        start_date: Start date in YYYYMMDD format
        end_date: End date in YYYYMMDD format (default: today)
    
    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed. Please run: pip install akshare")
        return None
    
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    
    try:
        # Determine market prefix
        if code.startswith("399"):
            symbol = f"sz{code}"
        else:
            symbol = f"sh{code}"
        
        # Use East Money interface for index data
        df = ak.stock_zh_index_daily_em(symbol=symbol)
        
        if df is None or df.empty:
            logger.warning(f"No data returned for index {code} from EM")
            return None
        
        # Standardize column names (EM format)
        col_mapping = {
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_change",
            "涨跌额": "change",
            "换手率": "turnover"
        }
        df = df.rename(columns=col_mapping)
        
        # Ensure date column is datetime type
        df["date"] = pd.to_datetime(df["date"])
        
        # Filter by date range
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        
        # Keep only necessary columns
        required_cols = ["date", "open", "high", "low", "close", "volume"]
        df = df[[c for c in required_cols if c in df.columns]]
        
        # Convert numeric columns
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df.sort_values("date").reset_index(drop=True)
        
        return df
        
    except Exception as e:
        logger.error(f"Failed to fetch index data for {code} from EM: {e}")
        return None


def fetch_index_data(
    code: str,
    data_dir: str = "../data",
    start_date: str = "20100101",
    end_date: Optional[str] = None,
    force_update: bool = False
) -> Optional[pd.DataFrame]:
    """
    Fetch and save index data
    
    Args:
        code: Index code
        data_dir: Data directory path
        start_date: Start date in YYYYMMDD format
        end_date: End date in YYYYMMDD format
        force_update: Force re-download even if local data exists
    
    Returns:
        DataFrame or None
    """
    data_path = Path(data_dir)
    index_dir = data_path / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    
    parquet_path = index_dir / f"{code}.parquet"
    
    # Check if we need to update
    need_download = force_update or not parquet_path.exists()
    
    if not need_download:
        # Check if existing data is recent enough
        try:
            existing_df = pd.read_parquet(parquet_path)
            if not existing_df.empty:
                last_date = existing_df["date"].max()
                # If last data is more than 1 day old, do incremental update
                if (datetime.now() - pd.to_datetime(last_date)).days > 1:
                    need_download = True
                    # Incremental update from last date
                    start_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime("%Y%m%d")
        except Exception:
            need_download = True
    
    if need_download:
        # Try EM interface first (more reliable)
        df = _fetch_index_data_akshare_em(code, start_date, end_date)
        
        if df is None:
            # Fallback to standard interface
            df = _fetch_index_data_akshare(code, start_date, end_date)
        
        if df is not None and not df.empty:
            # If incremental update, merge with existing data
            if parquet_path.exists() and not force_update:
                try:
                    existing_df = pd.read_parquet(parquet_path)
                    df = pd.concat([existing_df, df], ignore_index=True)
                    df = df.drop_duplicates(subset="date", keep="last")
                    df = df.sort_values("date").reset_index(drop=True)
                except Exception as e:
                    logger.warning(f"Failed to merge with existing data: {e}")
            
            # Save to parquet
            df.to_parquet(parquet_path, index=False)
            logger.info(f"Saved index data for {code}: {len(df)} rows")
        
        return df
    
    return None


def load_index_data(
    code: str,
    data_dir: str = "../data",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """
    Load index data from local storage
    
    Args:
        code: Index code
        data_dir: Data directory path
        start_date: Start date (YYYY-MM-DD format)
        end_date: End date (YYYY-MM-DD format)
    
    Returns:
        DataFrame or None
    """
    data_path = Path(data_dir)
    parquet_path = data_path / "index" / f"{code}.parquet"
    
    if not parquet_path.exists():
        return None
    
    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        logger.error(f"Failed to read index parquet {parquet_path}: {e}")
        return None
    
    if df.empty:
        return None
    
    df = df.sort_values("date").reset_index(drop=True)
    
    # Convert numeric columns
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    
    df = df.dropna(subset=["open", "high", "low", "close"])
    
    # Filter by date range
    if start_date:
        df = df[df["date"] >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df["date"] <= pd.to_datetime(end_date)]
    
    if df.empty:
        return None
    
    return df.reset_index(drop=True)


def update_all_indices(
    data_dir: str = "../data",
    progress_callback=None
) -> Dict[str, bool]:
    """
    Update all index data
    
    Args:
        data_dir: Data directory path
        progress_callback: Callback function (current, total, code, success)
    
    Returns:
        Dict of {code: success} results
    """
    results = {}
    indices = get_index_list()
    total = len(indices)
    
    for i, item in enumerate(indices):
        code = item["code"]
        try:
            df = fetch_index_data(code, data_dir)
            success = df is not None and not df.empty
            results[code] = success
        except Exception as e:
            logger.error(f"Failed to update index {code}: {e}")
            results[code] = False
        
        if progress_callback:
            progress_callback(i + 1, total, code, results.get(code, False))
    
    return results
