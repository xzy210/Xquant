# data_loader.py - 数据加载模块
"""
数据加载模块，负责从CSV文件加载股票数据
支持数据预加载和内存缓存，优化切换股票时的响应速度

此模块位于 common/ 目录，被 trading_app 和 strategy_app 共享使用
"""
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed


def _normalize_symbol_code(code: str) -> str:
    value = str(code or "").strip().upper()
    return value.split(".", 1)[0] if "." in value else value


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

    def reload_all(
        self,
        data_dir: str = None,
        stock_codes: List[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        max_workers: int = 8
    ) -> int:
        """
        重新加载所有缓存数据（用于全量同步后刷新缓存）

        Args:
            data_dir: 数据目录（如果为None则使用初始化时的目录）
            stock_codes: 要加载的股票代码列表（如果为None则使用当前缓存的代码）
            progress_callback: 进度回调函数 (current, total, code)
            max_workers: 并行加载的线程数

        Returns:
            成功加载的股票数量
        """
        dir_path = data_dir or self._data_dir
        codes = stock_codes if stock_codes is not None else list(self._cache.keys())

        if not codes:
            return 0

        # 清空旧缓存
        self._cache.clear()

        total = len(codes)
        loaded_count = 0

        def load_one(code: str) -> Tuple[str, Optional[pd.DataFrame]]:
            """加载单只股票数据"""
            df = _load_stock_data_from_csv(code, dir_path)
            return code, df

        # 使用线程池并行加载
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(load_one, code): code for code in codes}

            for i, future in enumerate(as_completed(futures)):
                code, df = future.result()
                if df is not None:
                    self._cache[code] = df
                    loaded_count += 1

                if progress_callback:
                    progress_callback(i + 1, total, code)

        self._is_loaded = True
        self._data_dir = dir_path
        return loaded_count


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
    从本地存储加载股票数据 (仅限 Parquet)
    
    Args:
        code: 股票代码
        data_dir: 数据目录路径
        adj: 复权类型
    
    Returns:
        pd.DataFrame 或 None
    """
    normalized_code = _normalize_symbol_code(code)
    data_path = Path(data_dir)
    parquet_path = data_path / f"{normalized_code}.parquet"
    
    if not parquet_path.exists():
        return None
        
    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"读取 Parquet 失败 {parquet_path}: {e}")
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
    code = _normalize_symbol_code(code)
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
    
    # 仅扫描 parquet 文件
    parquet_files = sorted(data_path.glob("*.parquet"))
    return [f.stem for f in parquet_files]


def load_stock_name_map(stocklist_path: str = "../stocklist/stocklist.csv") -> Dict[str, str]:
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


# ==================== ETF 数据加载模块 ==================== #

class ETFDataCache:
    """
    ETF数据缓存管理器
    在应用启动时预加载所有ETF数据到内存，加速切换ETF时的显示速度
    """
    
    def __init__(self):
        self._cache: Dict[str, pd.DataFrame] = {}  # {ETF代码: DataFrame}
        self._data_dir: str = ""
        self._is_loaded: bool = False
    
    def preload_all(
        self,
        data_dir: str,
        etf_codes: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        max_workers: int = 8
    ) -> int:
        """
        预加载所有ETF数据到内存
        
        Args:
            data_dir: 数据目录路径
            etf_codes: 要加载的ETF代码列表
            progress_callback: 进度回调函数 (current, total, code)
            max_workers: 并行加载的线程数
        
        Returns:
            成功加载的ETF数量
        """
        self._data_dir = data_dir
        self._cache.clear()
        
        total = len(etf_codes)
        loaded_count = 0
        
        def load_one(code: str) -> Tuple[str, Optional[pd.DataFrame]]:
            """加载单只ETF数据"""
            df = _load_etf_data_from_parquet(code, data_dir)
            return code, df
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(load_one, code): code for code in etf_codes}
            
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
        从缓存获取ETF数据
        """
        if code not in self._cache:
            return None
        
        df = self._cache[code].copy()
        
        if start_date:
            df = df[df["date"] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df["date"] <= pd.to_datetime(end_date)]
        
        return df.reset_index(drop=True) if not df.empty else None
    
    def is_loaded(self) -> bool:
        """检查缓存是否已加载"""
        return self._is_loaded
    
    def get_cached_codes(self) -> List[str]:
        """获取已缓存的ETF代码列表"""
        return list(self._cache.keys())
    
    def clear(self):
        """清空缓存"""
        self._cache.clear()
        self._is_loaded = False
    
    def reload_etf(self, code: str, data_dir: str = None) -> bool:
        """
        重新加载单只ETF数据
        """
        dir_path = data_dir or self._data_dir
        df = _load_etf_data_from_parquet(code, dir_path)
        if df is not None:
            self._cache[code] = df
            return True
        return False

    def reload_all(
        self,
        data_dir: str = None,
        etf_codes: List[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        max_workers: int = 8
    ) -> int:
        """
        重新加载所有ETF缓存数据（用于全量同步后刷新缓存）

        Args:
            data_dir: 数据目录（如果为None则使用初始化时的目录）
            etf_codes: 要加载的ETF代码列表（如果为None则使用当前缓存的代码）
            progress_callback: 进度回调函数 (current, total, code)
            max_workers: 并行加载的线程数

        Returns:
            成功加载的ETF数量
        """
        dir_path = data_dir or self._data_dir
        codes = etf_codes if etf_codes is not None else list(self._cache.keys())

        if not codes:
            return 0

        # 清空旧缓存
        self._cache.clear()

        total = len(codes)
        loaded_count = 0

        def load_one(code: str) -> Tuple[str, Optional[pd.DataFrame]]:
            """加载单只ETF数据"""
            df = _load_etf_data_from_parquet(code, dir_path)
            return code, df

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(load_one, code): code for code in codes}

            for i, future in enumerate(as_completed(futures)):
                code, df = future.result()
                if df is not None:
                    self._cache[code] = df
                    loaded_count += 1

                if progress_callback:
                    progress_callback(i + 1, total, code)

        self._is_loaded = True
        self._data_dir = dir_path
        return loaded_count


# 全局ETF缓存实例
_etf_cache = ETFDataCache()


def get_etf_cache() -> ETFDataCache:
    """获取全局ETF数据缓存实例"""
    return _etf_cache


def _load_etf_data_from_parquet(
    code: str,
    data_dir: str = "../data",
) -> Optional[pd.DataFrame]:
    """
    从本地存储加载ETF数据
    
    Args:
        code: ETF代码
        data_dir: 数据目录路径
    
    Returns:
        pd.DataFrame 或 None
    """
    normalized_code = _normalize_symbol_code(code)
    data_path = Path(data_dir)
    # ETF数据存储在 data/etf/ 目录下
    parquet_path = data_path / "etf" / f"{normalized_code}.parquet"
    
    if not parquet_path.exists():
        return None
        
    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"读取 ETF Parquet 失败 {parquet_path}: {e}")
        return None
    
    if df.empty:
        return None
    
    df = df.sort_values("date").reset_index(drop=True)
    
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    
    df = df.dropna(subset=["open", "high", "low", "close"])
    
    if df.empty:
        return None
    
    return df


def load_etf_data(
    code: str,
    data_dir: str = "../data",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    use_cache: bool = True,
) -> Optional[pd.DataFrame]:
    """
    加载ETF数据
    
    优先从内存缓存读取，如果缓存中没有则从Parquet文件读取。
    
    Args:
        code: ETF代码
        data_dir: 数据目录路径
        start_date: 起始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        use_cache: 是否使用缓存（默认True）
    
    Returns:
        pd.DataFrame: 包含 date/open/high/low/close/volume 的 DataFrame
                      如果文件不存在或为空则返回 None
    """
    code = _normalize_symbol_code(code)
    # 优先从缓存读取
    if use_cache and _etf_cache.is_loaded():
        df = _etf_cache.get(code, start_date, end_date)
        if df is not None:
            return df
    
    # 缓存中没有，从文件读取
    df = _load_etf_data_from_parquet(code, data_dir)
    
    if df is None:
        return None
    
    # 日期过滤
    if start_date:
        df = df[df["date"] >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df["date"] <= pd.to_datetime(end_date)]
    
    return df.reset_index(drop=True) if not df.empty else None


def get_etf_list(data_dir: str = "../data") -> List[str]:
    """
    扫描数据目录，获取所有可用ETF代码
    
    Args:
        data_dir: 数据目录路径
    
    Returns:
        ETF代码列表（已排序）
    """
    etf_path = Path(data_dir) / "etf"
    if not etf_path.exists():
        return []
    
    parquet_files = sorted(etf_path.glob("*.parquet"))
    return [f.stem for f in parquet_files]


def load_etf_name_map(config_path: str = None) -> Dict[str, str]:
    """
    加载ETF代码到名称的映射
    
    Args:
        config_path: ETF配置文件路径，如果为None则尝试多个默认路径
    
    Returns:
        {ETF代码: ETF名称} 字典
    """
    import json
    
    # 尝试多个可能的路径
    possible_paths = []
    if config_path:
        possible_paths.append(Path(config_path))
    else:
        # 默认尝试路径（相对于common目录的不同层级）
        current_file_dir = Path(__file__).parent
        possible_paths = [
current_file_dir / ".." / "trading_app" / "config" / "etf_list.json",
            current_file_dir / ".." / "strategy_app" / "config" / "etf_list.json",
            current_file_dir / ".." / "config" / "etf_list.json",
        ]
    
    for config_path in possible_paths:
        try:
            config_path = config_path.resolve()
            if not config_path.exists():
                continue
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            name_map = {}
            for category in config.get("categories", []):
                for etf in category.get("etfs", []):
                    code = etf.get("code", "")
                    name = etf.get("name", "")
                    if code and name:
                        name_map[code] = name
            
            return name_map
        except Exception:
            continue
    
    return {}


def load_etf_categories(config_path: str = None) -> List[Dict]:
    """
    加载ETF分类信息
    
    Args:
        config_path: ETF配置文件路径
    
    Returns:
        分类列表，每个分类包含 name 和 etfs
    """
    import json
    
    # 尝试多个可能的路径
    possible_paths = []
    if config_path:
        possible_paths.append(Path(config_path))
    else:
        current_file_dir = Path(__file__).parent
        possible_paths = [
current_file_dir / ".." / "trading_app" / "config" / "etf_list.json",
            current_file_dir / ".." / "strategy_app" / "config" / "etf_list.json",
            current_file_dir / ".." / "config" / "etf_list.json",
        ]
    
    for config_path in possible_paths:
        try:
            config_path = config_path.resolve()
            if not config_path.exists():
                continue
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            return config.get("categories", [])
        except Exception:
            continue
    
    return []


def get_etf_date_range(code: str, data_dir: str = "../data") -> Optional[Tuple[str, str]]:
    """
    获取ETF数据的日期范围
    
    Args:
        code: ETF代码
        data_dir: 数据目录路径
    
    Returns:
        (start_date, end_date) 元组，如果数据不存在返回 None
    """
    df = load_etf_data(code, data_dir)
    if df is None or df.empty:
        return None
    
    start_date = df["date"].min().strftime("%Y-%m-%d")
    end_date = df["date"].max().strftime("%Y-%m-%d")
    return (start_date, end_date)
