"""
Volatility factors implementation
"""
from typing import Optional
import pandas as pd
import numpy as np
from .base_factor import BaseFactor
from .registry import factor_registry


@factor_registry.register
class Volatility20D(BaseFactor):
    """20-day volatility factor"""
    
    @property
    def name(self) -> str:
        return "volatility_20d"
    
    @property
    def category(self) -> str:
        return "volatility"
    
    @property
    def description(self) -> str:
        return "20-day rolling standard deviation of daily returns"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        return df['close'].pct_change().rolling(w).std()


@factor_registry.register
class Volatility60D(BaseFactor):
    """60-day volatility factor"""
    
    @property
    def name(self) -> str:
        return "volatility_60d"
    
    @property
    def category(self) -> str:
        return "volatility"
    
    @property
    def description(self) -> str:
        return "60-day rolling standard deviation of daily returns"
    
    @property
    def default_window(self) -> int:
        return 60
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        return df['close'].pct_change().rolling(w).std()


@factor_registry.register
class Bias20(BaseFactor):
    """20-day bias factor"""
    
    @property
    def name(self) -> str:
        return "bias_20"
    
    @property
    def category(self) -> str:
        return "volatility"
    
    @property
    def description(self) -> str:
        return "Price deviation from 20-day moving average"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        ma = df['close'].rolling(w).mean()
        return (df['close'] - ma) / ma.replace(0, np.nan)


@factor_registry.register
class Bias60(BaseFactor):
    """60-day bias factor"""
    
    @property
    def name(self) -> str:
        return "bias_60"
    
    @property
    def category(self) -> str:
        return "volatility"
    
    @property
    def description(self) -> str:
        return "Price deviation from 60-day moving average"
    
    @property
    def default_window(self) -> int:
        return 60
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        ma = df['close'].rolling(w).mean()
        return (df['close'] - ma) / ma.replace(0, np.nan)


@factor_registry.register
class ATR20(BaseFactor):
    """20-day Average True Range factor"""
    
    @property
    def name(self) -> str:
        return "atr_20"
    
    @property
    def category(self) -> str:
        return "volatility"
    
    @property
    def description(self) -> str:
        return "20-day Average True Range (ATR)"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        
        high = df['high'] if 'high' in df.columns else df['close']
        low = df['low'] if 'low' in df.columns else df['close']
        close = df['close']
        
        prev_close = close.shift(1)
        
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return true_range.rolling(w).mean()


@factor_registry.register
class HighLowRange20D(BaseFactor):
    """20-day high-low range factor"""
    
    @property
    def name(self) -> str:
        return "high_low_range_20d"
    
    @property
    def category(self) -> str:
        return "volatility"
    
    @property
    def description(self) -> str:
        return "20-day high-low price range ratio"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        
        high = df['high'] if 'high' in df.columns else df['close']
        low = df['low'] if 'low' in df.columns else df['close']
        
        rolling_high = high.rolling(w).max()
        rolling_low = low.rolling(w).min()
        
        return (rolling_high - rolling_low) / rolling_low.replace(0, np.nan)
