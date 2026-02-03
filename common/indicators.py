# indicators.py - 技术指标计算模块
"""
技术指标计算模块，提供常用技术指标的计算函数

此模块位于 common/ 目录，被 pyqt_app 和 strategy_app 共享使用
"""
import pandas as pd
import numpy as np
from typing import List, Optional


def compute_ma(df: pd.DataFrame, windows: List[int], col: str = "close") -> pd.DataFrame:
    """
    计算移动平均线
    
    Args:
        df: 包含价格数据的DataFrame
        windows: 均线周期列表，如 [5, 10, 20]
        col: 用于计算的列名
    
    Returns:
        添加了均线列的DataFrame
    """
    df = df.copy()
    for w in windows:
        if w > 1 and len(df) >= w:
            df[f"MA{w}"] = df[col].rolling(window=w, min_periods=1).mean()
    return df


def compute_ema(series: pd.Series, span: int, adjust: bool = False) -> pd.Series:
    """
    计算指数移动平均
    
    Args:
        series: 价格序列
        span: EMA周期
        adjust: 是否调整
    
    Returns:
        EMA序列
    """
    return series.ewm(span=span, adjust=adjust).mean()


def compute_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    price_col: str = "close",
    factor: float = 2.0,
) -> pd.DataFrame:
    """
    计算MACD指标
    
    Args:
        df: 包含价格数据的DataFrame
        fast: 快线周期
        slow: 慢线周期
        signal: 信号线周期
        price_col: 价格列名
        factor: MACD柱乘数（A股常用2.0）
    
    Returns:
        添加了 DIF/DEA/MACD 列的DataFrame
    """
    df = df.copy()
    price = pd.to_numeric(df[price_col], errors="coerce")
    ema_fast = price.ewm(span=fast, adjust=False).mean()
    ema_slow = price.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd = factor * (dif - dea)
    
    df["DIF"] = dif
    df["DEA"] = dea
    df["MACD"] = macd
    return df


def compute_kdj(df: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    """
    计算KDJ指标
    
    Args:
        df: 包含 high/low/close 的DataFrame
        n: RSV周期
    
    Returns:
        添加了 K/D/J 列的DataFrame
    """
    df = df.copy()
    if df.empty:
        df["K"] = np.nan
        df["D"] = np.nan
        df["J"] = np.nan
        return df
    
    low_n = df["low"].rolling(window=n, min_periods=1).min()
    high_n = df["high"].rolling(window=n, min_periods=1).max()
    rsv = (df["close"] - low_n) / (high_n - low_n + 1e-9) * 100
    
    K = np.zeros(len(df), dtype=float)
    D = np.zeros(len(df), dtype=float)
    
    for i in range(len(df)):
        if i == 0:
            K[i] = D[i] = 50.0
        else:
            K[i] = 2 / 3 * K[i - 1] + 1 / 3 * rsv.iloc[i]
            D[i] = 2 / 3 * D[i - 1] + 1 / 3 * K[i]
    
    J = 3 * K - 2 * D
    
    df["K"] = K
    df["D"] = D
    df["J"] = J
    return df


def compute_bbi(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算BBI多空指标
    
    BBI = (MA3 + MA6 + MA12 + MA24) / 4
    
    Args:
        df: 包含 close 的DataFrame
    
    Returns:
        添加了 BBI 列的DataFrame
    """
    df = df.copy()
    ma3 = df["close"].rolling(3, min_periods=1).mean()
    ma6 = df["close"].rolling(6, min_periods=1).mean()
    ma12 = df["close"].rolling(12, min_periods=1).mean()
    ma24 = df["close"].rolling(24, min_periods=1).mean()
    df["BBI"] = (ma3 + ma6 + ma12 + ma24) / 4
    return df


def compute_boll(
    df: pd.DataFrame,
    n: int = 20,
    k: float = 2.0,
    price_col: str = "close"
) -> pd.DataFrame:
    """
    计算布林带指标
    
    Args:
        df: 包含价格数据的DataFrame
        n: 周期
        k: 标准差倍数
        price_col: 价格列名
    
    Returns:
        添加了 BOLL_MID/BOLL_UP/BOLL_DOWN 列的DataFrame
    """
    df = df.copy()
    price = df[price_col]
    mid = price.rolling(window=n, min_periods=1).mean()
    std = price.rolling(window=n, min_periods=1).std()
    
    df["BOLL_MID"] = mid
    df["BOLL_UP"] = mid + k * std
    df["BOLL_DOWN"] = mid - k * std
    return df


def compute_rsi(
    df: pd.DataFrame,
    n: int = 14,
    price_col: str = "close"
) -> pd.DataFrame:
    """
    计算RSI相对强弱指标
    
    Args:
        df: 包含价格数据的DataFrame
        n: 周期
        price_col: 价格列名
    
    Returns:
        添加了 RSI 列的DataFrame
    """
    df = df.copy()
    delta = df[price_col].diff()
    
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    
    avg_gain = gain.ewm(span=n, adjust=False).mean()
    avg_loss = loss.ewm(span=n, adjust=False).mean()
    
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    
    df[f"RSI{n}"] = rsi
    return df


def compute_volume_ma(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    计算成交量均线
    
    Args:
        df: 包含 volume 的DataFrame
        window: 均线周期
    
    Returns:
        添加了成交量均线列的DataFrame
    """
    df = df.copy()
    if window > 1 and len(df) >= window:
        df[f"VOL_MA{window}"] = df["volume"].rolling(window=window, min_periods=1).mean()
    return df


def attach_all_indicators(
    df: pd.DataFrame,
    ma_windows: Optional[List[int]] = None,
    include_macd: bool = True,
    include_kdj: bool = True,
    include_bbi: bool = True,
    vol_ma_window: int = 5
) -> pd.DataFrame:
    """
    为DataFrame添加所有技术指标
    
    Args:
        df: 原始OHLCV数据
        ma_windows: 均线周期列表
        include_macd: 是否包含MACD
        include_kdj: 是否包含KDJ
        include_bbi: 是否包含BBI
        vol_ma_window: 成交量均线周期
    
    Returns:
        添加了所有指标的DataFrame
    """
    if ma_windows is None:
        ma_windows = [5, 10, 20]
    
    df = compute_ma(df, ma_windows)
    
    if include_macd:
        df = compute_macd(df)
    
    if include_kdj:
        df = compute_kdj(df)
    
    if include_bbi:
        df = compute_bbi(df)
    
    if vol_ma_window > 0:
        df = compute_volume_ma(df, vol_ma_window)
    
    return df
