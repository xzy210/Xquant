# data_loader.py - 数据加载模块
"""
数据加载模块，负责从CSV文件加载股票数据
"""
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np


def load_stock_data(
    code: str,
    data_dir: str = "../data",
    adj: str = "qfq",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """
    加载股票数据。
    
    数据文件有两种格式：
    1. 新格式：直接存储前复权数据（无 adj_factor 列），数据更准确
    2. 旧格式：存储不复权数据 + adj_factor 列，需要动态计算
    
    Args:
        code: 股票代码
        data_dir: 数据目录路径
        adj: 复权类型（主要用于旧格式数据的兼容）
            - "qfq": 前复权（默认）
            - "hfq": 后复权
            - None 或其他: 不复权
        start_date: 起始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
    
    Returns:
        pd.DataFrame: 包含 date/open/high/low/close/volume 的 DataFrame
                      如果文件不存在或为空则返回 None
    """
    csv_path = Path(data_dir) / f"{code}.csv"
    if not csv_path.exists():
        return None
    
    try:
        df = pd.read_csv(csv_path, parse_dates=["date"])
    except Exception:
        return None
    
    if df.empty:
        return None
    
    df = df.sort_values("date").reset_index(drop=True)
    
    # 转换数值类型
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    
    df = df.dropna(subset=["open", "high", "low", "close"])
    
    if df.empty:
        return None
    
    # 兼容旧格式：如果存在 adj_factor 列，动态计算复权价格
    if "adj_factor" in df.columns:
        df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce")
        
        if adj in ("qfq", "hfq") and df["adj_factor"].notna().any():
            if adj == "qfq":
                # 前复权：以最新价格为基准
                latest_factor = df["adj_factor"].iloc[-1]
                if pd.notna(latest_factor) and latest_factor != 0:
                    ratio = df["adj_factor"] / latest_factor
                    for c in ["open", "high", "low", "close"]:
                        df[c] = df[c] * ratio
            elif adj == "hfq":
                # 后复权：以最早价格为基准
                earliest_factor = df["adj_factor"].iloc[0]
                if pd.notna(earliest_factor) and earliest_factor != 0:
                    ratio = df["adj_factor"] / earliest_factor
                    for c in ["open", "high", "low", "close"]:
                        df[c] = df[c] * ratio
        
        # 移除 adj_factor 列
        df = df.drop(columns=["adj_factor"])
    
    # 新格式数据已经是前复权的，直接使用
    
    # 日期过滤
    if start_date:
        df = df[df["date"] >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df["date"] <= pd.to_datetime(end_date)]
    
    return df.reset_index(drop=True)


def get_stock_list(data_dir: str = "../data") -> List[str]:
    """
    扫描数据目录，获取所有可用股票代码
    
    Args:
        data_dir: 数据目录路径
    
    Returns:
        股票代码列表（已排序）
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        return []
    csv_files = sorted(data_path.glob("*.csv"))
    return [f.stem for f in csv_files]


def load_stock_name_map(stocklist_path: str = "../stocklist.csv") -> Dict[str, str]:
    """
    加载股票代码到名称的映射
    
    Args:
        stocklist_path: 股票列表文件路径
    
    Returns:
        {股票代码: 股票名称} 字典
    """
    try:
        file_path = Path(stocklist_path)
        if not file_path.exists():
            return {}
        
        # 强制将 symbol 列读取为字符串类型
        df = pd.read_csv(file_path, dtype={'symbol': str})
        
        if 'symbol' not in df.columns or 'name' not in df.columns:
            return {}
        
        name_map = {str(code).strip(): name for code, name in zip(df['symbol'], df['name'])}
        return name_map
    except Exception:
        return {}


def get_date_range(code: str, data_dir: str = "../data") -> Optional[Tuple[str, str]]:
    """
    获取股票数据的日期范围
    
    Args:
        code: 股票代码
        data_dir: 数据目录路径
    
    Returns:
        (start_date, end_date) 元组，如果数据不存在返回 None
    """
    df = load_stock_data(code, data_dir, adj=None)
    if df is None or df.empty:
        return None
    
    start_date = df["date"].min().strftime("%Y-%m-%d")
    end_date = df["date"].max().strftime("%Y-%m-%d")
    return (start_date, end_date)

