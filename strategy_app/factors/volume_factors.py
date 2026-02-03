"""
Volume factors implementation
"""
from typing import Optional
import pandas as pd
import numpy as np
from .base_factor import BaseFactor
from .registry import factor_registry


@factor_registry.register
class Turnover20D(BaseFactor):
    """20-day average turnover factor (normalized)"""
    
    @property
    def name(self) -> str:
        return "turnover_20d"
    
    @property
    def category(self) -> str:
        return "volume"
    
    @property
    def description(self) -> str:
        return "20-day average volume relative to long-term average (normalized)"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        if 'volume' not in df.columns:
            return pd.Series(0, index=df.index)
        w = window or self.default_window
        # Calculate 20-day rolling mean
        vol_ma_short = df['volume'].rolling(w).mean()
        # Normalize by long-term (60-day) average to get relative turnover
        vol_ma_long = df['volume'].rolling(60).mean()
        # Return ratio (typically around 1.0)
        return vol_ma_short / vol_ma_long.replace(0, np.nan)


@factor_registry.register
class VolumeRatio(BaseFactor):
    """Volume ratio factor"""
    
    @property
    def name(self) -> str:
        return "volume_ratio"
    
    @property
    def category(self) -> str:
        return "volume"
    
    @property
    def description(self) -> str:
        return "Current volume / 20-day average volume"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        if 'volume' not in df.columns:
            return pd.Series(1, index=df.index)
        w = window or self.default_window
        vol_ma = df['volume'].rolling(w).mean()
        return df['volume'] / vol_ma.replace(0, np.nan)


@factor_registry.register
class VolumeStd20D(BaseFactor):
    """20-day volume coefficient of variation factor (normalized)"""
    
    @property
    def name(self) -> str:
        return "volume_std_20d"
    
    @property
    def category(self) -> str:
        return "volume"
    
    @property
    def description(self) -> str:
        return "20-day volume coefficient of variation (std/mean, normalized)"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        if 'volume' not in df.columns:
            return pd.Series(0, index=df.index)
        w = window or self.default_window
        # Use coefficient of variation (CV = std/mean) for normalization
        vol_std = df['volume'].rolling(w).std()
        vol_mean = df['volume'].rolling(w).mean()
        # Return CV (typically 0.1 ~ 1.0 range)
        return vol_std / vol_mean.replace(0, np.nan)


@factor_registry.register
class VolumeChange5D(BaseFactor):
    """5-day volume change factor"""
    
    @property
    def name(self) -> str:
        return "volume_change_5d"
    
    @property
    def category(self) -> str:
        return "volume"
    
    @property
    def description(self) -> str:
        return "5-day volume change rate"
    
    @property
    def default_window(self) -> int:
        return 5
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        if 'volume' not in df.columns:
            return pd.Series(0, index=df.index)
        w = window or self.default_window
        return df['volume'].pct_change(w)


@factor_registry.register
class AmountRatio(BaseFactor):
    """Amount ratio factor (turnover value)"""
    
    @property
    def name(self) -> str:
        return "amount_ratio"
    
    @property
    def category(self) -> str:
        return "volume"
    
    @property
    def description(self) -> str:
        return "Current amount / 20-day average amount"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        
        # Calculate amount (price * volume)
        if 'amount' in df.columns:
            amount = df['amount']
        elif 'volume' in df.columns:
            amount = df['close'] * df['volume']
        else:
            return pd.Series(1, index=df.index)
        
        amount_ma = amount.rolling(w).mean()
        return amount / amount_ma.replace(0, np.nan)


@factor_registry.register
class VWAP20D(BaseFactor):
    """20-day Volume Weighted Average Price factor"""
    
    @property
    def name(self) -> str:
        return "vwap_20d"
    
    @property
    def category(self) -> str:
        return "volume"
    
    @property
    def description(self) -> str:
        return "Price deviation from 20-day VWAP"
    
    @property
    def default_window(self) -> int:
        return 20
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        
        if 'volume' not in df.columns:
            return pd.Series(0, index=df.index)
        
        # Calculate VWAP
        if 'amount' in df.columns:
            amount = df['amount']
        else:
            # Use typical price * volume as amount
            typical_price = (df['high'] + df['low'] + df['close']) / 3 if 'high' in df.columns else df['close']
            amount = typical_price * df['volume']
        
        rolling_amount = amount.rolling(w).sum()
        rolling_volume = df['volume'].rolling(w).sum()
        
        vwap = rolling_amount / rolling_volume.replace(0, np.nan)
        
        # Return price deviation from VWAP
        return (df['close'] - vwap) / vwap.replace(0, np.nan)
