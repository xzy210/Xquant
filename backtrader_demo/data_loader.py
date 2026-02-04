"""
Data Loader for Backtrader Demo
================================
Adapts existing project data (Parquet format) to backtrader's DataFeed format.
"""

import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, List

import pandas as pd
import backtrader as bt


# Add project root to path for importing common modules
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_stock_data_for_bt(
    code: str,
    data_dir: str = None,
    start_date: str = None,
    end_date: str = None,
) -> Optional[pd.DataFrame]:
    """
    Load stock data from Parquet file and prepare it for backtrader.
    
    Args:
        code: Stock code (e.g., '000001.SZ')
        data_dir: Path to data directory (default: ../data relative to project root)
        start_date: Start date filter (YYYY-MM-DD)
        end_date: End date filter (YYYY-MM-DD)
    
    Returns:
        DataFrame with columns: datetime(index), open, high, low, close, volume
    """
    if data_dir is None:
        data_dir = PROJECT_ROOT / "data"
    else:
        data_dir = Path(data_dir)
    
    parquet_path = data_dir / f"{code}.parquet"
    
    if not parquet_path.exists():
        print(f"[ERROR] Data file not found: {parquet_path}")
        return None
    
    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"[ERROR] Failed to read Parquet file: {e}")
        return None
    
    if df.empty:
        print(f"[ERROR] Empty data for {code}")
        return None
    
    # Ensure date column is datetime
    df["date"] = pd.to_datetime(df["date"])
    
    # Filter by date range
    if start_date:
        df = df[df["date"] >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df["date"] <= pd.to_datetime(end_date)]
    
    if df.empty:
        print(f"[ERROR] No data in the specified date range for {code}")
        return None
    
    # Prepare DataFrame for backtrader
    # Backtrader expects: datetime as index, and OHLCV columns
    df = df.sort_values("date").reset_index(drop=True)
    
    # Rename and select required columns
    result = pd.DataFrame({
        "datetime": df["date"],
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
        "openinterest": 0,  # Required by backtrader but not used for stocks
    })
    
    # Set datetime as index
    result.set_index("datetime", inplace=True)
    
    # Drop any rows with NaN values in OHLC
    result = result.dropna(subset=["open", "high", "low", "close"])
    
    return result


def create_bt_data_feed(
    code: str,
    data_dir: str = None,
    start_date: str = None,
    end_date: str = None,
) -> Optional[bt.feeds.PandasData]:
    """
    Create a backtrader DataFeed from stock data.
    
    Args:
        code: Stock code (e.g., '000001.SZ')
        data_dir: Path to data directory
        start_date: Start date filter (YYYY-MM-DD)
        end_date: End date filter (YYYY-MM-DD)
    
    Returns:
        bt.feeds.PandasData object ready to be added to Cerebro
    """
    df = load_stock_data_for_bt(code, data_dir, start_date, end_date)
    
    if df is None:
        return None
    
    # Create backtrader data feed
    data = bt.feeds.PandasData(
        dataname=df,
        datetime=None,  # Use index as datetime
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        openinterest="openinterest",
    )
    
    return data


def get_available_stocks(data_dir: str = None) -> List[str]:
    """
    Get list of available stock codes from data directory.
    
    Args:
        data_dir: Path to data directory
    
    Returns:
        List of stock codes
    """
    if data_dir is None:
        data_dir = PROJECT_ROOT / "data"
    else:
        data_dir = Path(data_dir)
    
    if not data_dir.exists():
        print(f"[ERROR] Data directory not found: {data_dir}")
        return []
    
    stocks = [f.stem for f in data_dir.glob("*.parquet")]
    return sorted(stocks)


def load_stock_list(list_name: str = "沪深300成分股") -> List[str]:
    """
    Load stock codes from stocklist CSV file.
    
    Args:
        list_name: Name of the stock list (without extension)
    
    Returns:
        List of stock codes
    """
    list_path = PROJECT_ROOT / "stocklist" / f"{list_name}_股票列表.csv"
    
    if not list_path.exists():
        print(f"[ERROR] Stock list not found: {list_path}")
        return []
    
    try:
        df = pd.read_csv(list_path, header=None, names=["code", "name"])
        return df["code"].tolist()
    except Exception as e:
        print(f"[ERROR] Failed to read stock list: {e}")
        return []


if __name__ == "__main__":
    # Test the data loader
    print("Testing data loader...")
    
    # Get available stocks
    stocks = get_available_stocks()
    print(f"Available stocks: {len(stocks)}")
    if stocks:
        print(f"Sample stocks: {stocks[:5]}")
    
    # Load a sample stock
    if stocks:
        test_code = stocks[0]
        print(f"\nLoading data for {test_code}...")
        df = load_stock_data_for_bt(test_code)
        if df is not None:
            print(f"Data shape: {df.shape}")
            print(f"Date range: {df.index[0]} to {df.index[-1]}")
            print(df.head())
