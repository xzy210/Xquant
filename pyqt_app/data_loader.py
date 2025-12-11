# data_loader.py - 数据加载模块
"""
数据加载模块，负责从CSV文件加载股票数据
支持数据预加载和内存缓存，优化切换股票时的响应速度
"""
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed


class StockDataCache:
    """
    股票数据缓存管理器
    在应用启动时预加载所有股票数据到内存，加速切换股票时的显示速度
    """
    
    def __init__(self):
        self._cache: Dict[str, pd.DataFrame] = {}  # {股票代码: DataFrame}
        self._data_dir: str = ""
        self._is_loaded: bool = False
    
    def preload_all(
        self,
        data_dir: str,
        stock_codes: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        max_workers: int = 8
    ) -> int:
        """
        预加载所有股票数据到内存
        
        Args:
            data_dir: 数据目录路径
            stock_codes: 要加载的股票代码列表
            progress_callback: 进度回调函数 (current, total, code)
            max_workers: 并行加载的线程数
        
        Returns:
            成功加载的股票数量
        """
        self._data_dir = data_dir
        self._cache.clear()
        
        total = len(stock_codes)
        loaded_count = 0
        
        def load_one(code: str) -> Tuple[str, Optional[pd.DataFrame]]:
            """加载单只股票数据"""
            df = _load_stock_data_from_csv(code, data_dir)
            return code, df
        
        # 使用线程池并行加载
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(load_one, code): code for code in stock_codes}
            
            for i, future in enumerate(as_completed(futures)):
                code, df = future.result()
                if df is not None:
                    self._cache[code] = df
                    loaded_count += 1
                
                if progress_callback:
                    progress_callback(i + 1, total, code)
        
        self._is_loaded = True
        return loaded_count
    
    def get(self, code: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """
        从缓存获取股票数据
        
        Args:
            code: 股票代码
            start_date: 起始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
        
        Returns:
            DataFrame 副本（按日期过滤后）
        """
        if code not in self._cache:
            return None
        
        df = self._cache[code].copy()
        
        # 日期过滤
        if start_date:
            df = df[df["date"] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df["date"] <= pd.to_datetime(end_date)]
        
        return df.reset_index(drop=True) if not df.empty else None
    
    def is_loaded(self) -> bool:
        """检查缓存是否已加载"""
        return self._is_loaded
    
    def get_cached_codes(self) -> List[str]:
        """获取已缓存的股票代码列表"""
        return list(self._cache.keys())
    
    def clear(self):
        """清空缓存"""
        self._cache.clear()
        self._is_loaded = False
    
    def reload_stock(self, code: str, data_dir: str = None) -> bool:
        """
        重新加载单只股票数据（用于更新后刷新缓存）
        
        Args:
            code: 股票代码
            data_dir: 数据目录（如果为None则使用初始化时的目录）
        
        Returns:
            是否加载成功
        """
        dir_path = data_dir or self._data_dir
        df = _load_stock_data_from_csv(code, dir_path)
        if df is not None:
            self._cache[code] = df
            return True
        return False


# 全局缓存实例
_stock_cache = StockDataCache()


def get_stock_cache() -> StockDataCache:
    """获取全局股票数据缓存实例"""
    return _stock_cache


def _load_stock_data_from_csv(
    code: str,
    data_dir: str = "../data",
    adj: str = "qfq",
) -> Optional[pd.DataFrame]:
    """
    从CSV文件加载股票数据（内部函数，不带日期过滤）
    
    Args:
        code: 股票代码
        data_dir: 数据目录路径
        adj: 复权类型
    
    Returns:
        pd.DataFrame 或 None
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
                latest_factor = df["adj_factor"].iloc[-1]
                if pd.notna(latest_factor) and latest_factor != 0:
                    ratio = df["adj_factor"] / latest_factor
                    for c in ["open", "high", "low", "close"]:
                        df[c] = df[c] * ratio
            elif adj == "hfq":
                earliest_factor = df["adj_factor"].iloc[0]
                if pd.notna(earliest_factor) and earliest_factor != 0:
                    ratio = df["adj_factor"] / earliest_factor
                    for c in ["open", "high", "low", "close"]:
                        df[c] = df[c] * ratio
        
        df = df.drop(columns=["adj_factor"])
    
    return df


def load_stock_data(
    code: str,
    data_dir: str = "../data",
    adj: str = "qfq",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    use_cache: bool = True,
) -> Optional[pd.DataFrame]:
    """
    加载股票数据。
    
    优先从内存缓存读取，如果缓存中没有则从CSV文件读取。
    
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
        use_cache: 是否使用缓存（默认True）
    
    Returns:
        pd.DataFrame: 包含 date/open/high/low/close/volume 的 DataFrame
                      如果文件不存在或为空则返回 None
    """
    # 优先从缓存读取
    if use_cache and _stock_cache.is_loaded():
        df = _stock_cache.get(code, start_date, end_date)
        if df is not None:
            return df
    
    # 缓存中没有，从CSV读取
    df = _load_stock_data_from_csv(code, data_dir, adj)
    
    if df is None:
        return None
    
    # 日期过滤
    if start_date:
        df = df[df["date"] >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df["date"] <= pd.to_datetime(end_date)]
    
    return df.reset_index(drop=True) if not df.empty else None


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

