"""
Technical indicator factors implementation
"""
from typing import Optional
import pandas as pd
import numpy as np
from .base_factor import BaseFactor
from .registry import factor_registry


@factor_registry.register
class RSI14(BaseFactor):
    """14-day RSI factor"""
    
    @property
    def name(self) -> str:
        return "rsi_14"
    
    @property
    def category(self) -> str:
        return "technical"
    
    @property
    def description(self) -> str:
        return "14-day Relative Strength Index"
    
    @property
    def default_window(self) -> int:
        return 14
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        delta = df['close'].diff()
        
        gain = delta.where(delta > 0, 0)
        loss = (-delta).where(delta < 0, 0)
        
        avg_gain = gain.rolling(w).mean()
        avg_loss = loss.rolling(w).mean()
        
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        
        return rsi


@factor_registry.register
class MACD(BaseFactor):
    """MACD factor (MACD line value)"""
    
    @property
    def name(self) -> str:
        return "macd"
    
    @property
    def category(self) -> str:
        return "technical"
    
    @property
    def description(self) -> str:
        return "MACD line (12-day EMA - 26-day EMA)"
    
    @property
    def neutralizable(self) -> bool:
        return True
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        close = df['close']
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        return ema12 - ema26


@factor_registry.register
class MACDHist(BaseFactor):
    """MACD Histogram factor"""
    
    @property
    def name(self) -> str:
        return "macd_hist"
    
    @property
    def category(self) -> str:
        return "technical"
    
    @property
    def description(self) -> str:
        return "MACD Histogram (MACD - Signal)"
    
    @property
    def neutralizable(self) -> bool:
        return True
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        close = df['close']
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        return macd - signal


@factor_registry.register
class KDJ_K(BaseFactor):
    """KDJ K value factor"""
    
    @property
    def name(self) -> str:
        return "kdj_k"
    
    @property
    def category(self) -> str:
        return "technical"
    
    @property
    def description(self) -> str:
        return "KDJ indicator K value"
    
    @property
    def default_window(self) -> int:
        return 9
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        
        high = df['high'] if 'high' in df.columns else df['close']
        low = df['low'] if 'low' in df.columns else df['close']
        close = df['close']
        
        lowest_low = low.rolling(w).min()
        highest_high = high.rolling(w).max()
        
        rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100
        
        # K = 2/3 * prev_K + 1/3 * RSV
        k = rsv.ewm(com=2, adjust=False).mean()
        
        return k


@factor_registry.register
class KDJ_D(BaseFactor):
    """KDJ D value factor"""
    
    @property
    def name(self) -> str:
        return "kdj_d"
    
    @property
    def category(self) -> str:
        return "technical"
    
    @property
    def description(self) -> str:
        return "KDJ indicator D value"
    
    @property
    def default_window(self) -> int:
        return 9
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        
        high = df['high'] if 'high' in df.columns else df['close']
        low = df['low'] if 'low' in df.columns else df['close']
        close = df['close']
        
        lowest_low = low.rolling(w).min()
        highest_high = high.rolling(w).max()
        
        rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100
        
        k = rsv.ewm(com=2, adjust=False).mean()
        # D = 2/3 * prev_D + 1/3 * K
        d = k.ewm(com=2, adjust=False).mean()
        
        return d


@factor_registry.register
class BollingerPosition(BaseFactor):
    """Bollinger Bands position factor"""
    
    @property
    def name(self) -> str:
        return "bollinger_position"
    
    @property
    def category(self) -> str:
        return "technical"
    
    @property
    def description(self) -> str:
        return "Price position within Bollinger Bands (0=lower, 1=upper)"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        close = df['close']
        
        ma = close.rolling(w).mean()
        std = close.rolling(w).std()
        
        upper = ma + 2 * std
        lower = ma - 2 * std
        
        # Position: 0 at lower band, 1 at upper band
        position = (close - lower) / (upper - lower).replace(0, np.nan)
        
        return position


@factor_registry.register
class MA5MA20Ratio(BaseFactor):
    """MA5/MA20 ratio factor"""
    
    @property
    def name(self) -> str:
        return "ma5_ma20_ratio"
    
    @property
    def category(self) -> str:
        return "technical"
    
    @property
    def description(self) -> str:
        return "5-day MA / 20-day MA ratio"
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        close = df['close']
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        return ma5 / ma20.replace(0, np.nan)


@factor_registry.register
class PricePosition(BaseFactor):
    """Price position in N-day range"""
    
    @property
    def name(self) -> str:
        return "price_position_20d"
    
    @property
    def category(self) -> str:
        return "technical"
    
    @property
    def description(self) -> str:
        return "Price position within 20-day high-low range (0=low, 1=high)"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        close = df['close']
        
        high = df['high'] if 'high' in df.columns else close
        low = df['low'] if 'low' in df.columns else close
        
        rolling_high = high.rolling(w).max()
        rolling_low = low.rolling(w).min()
        
        position = (close - rolling_low) / (rolling_high - rolling_low).replace(0, np.nan)
        
        return position
