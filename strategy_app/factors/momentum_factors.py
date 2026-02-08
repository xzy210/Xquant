"""
Momentum factors implementation
"""
from typing import Optional
import pandas as pd
from .base_factor import BaseFactor
from .registry import factor_registry


@factor_registry.register
class Momentum20D(BaseFactor):
    """20-day momentum factor"""
    
    @property
    def name(self) -> str:
        return "momentum_20d"
    
    @property
    def category(self) -> str:
        return "momentum"
    
    @property
    def description(self) -> str:
        return "20-day price momentum (return rate)"
    
    @property
    def default_window(self) -> int:
        return 20
    
    @property
    def neutralizable(self) -> bool:
        return True
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        return df['close'].pct_change(w)


@factor_registry.register
class Momentum60D(BaseFactor):
    """60-day momentum factor"""
    
    @property
    def name(self) -> str:
        return "momentum_60d"
    
    @property
    def category(self) -> str:
        return "momentum"
    
    @property
    def description(self) -> str:
        return "60-day price momentum (return rate)"
    
    @property
    def default_window(self) -> int:
        return 60
    
    @property
    def neutralizable(self) -> bool:
        return True
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        return df['close'].pct_change(w)


@factor_registry.register
class Reversal5D(BaseFactor):
    """5-day reversal factor"""
    
    @property
    def name(self) -> str:
        return "reversal_5d"
    
    @property
    def category(self) -> str:
        return "momentum"
    
    @property
    def description(self) -> str:
        return "5-day short-term reversal factor"
    
    @property
    def default_window(self) -> int:
        return 5
    
    @property
    def neutralizable(self) -> bool:
        return True
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        return df['close'].pct_change(w)


@factor_registry.register
class Momentum10D(BaseFactor):
    """10-day momentum factor"""
    
    @property
    def name(self) -> str:
        return "momentum_10d"
    
    @property
    def category(self) -> str:
        return "momentum"
    
    @property
    def description(self) -> str:
        return "10-day price momentum (return rate)"
    
    @property
    def default_window(self) -> int:
        return 10
    
    @property
    def neutralizable(self) -> bool:
        return True
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        return df['close'].pct_change(w)


@factor_registry.register
class Momentum120D(BaseFactor):
    """120-day momentum factor"""
    
    @property
    def name(self) -> str:
        return "momentum_120d"
    
    @property
    def category(self) -> str:
        return "momentum"
    
    @property
    def description(self) -> str:
        return "120-day price momentum (return rate)"
    
    @property
    def default_window(self) -> int:
        return 120
    
    @property
    def neutralizable(self) -> bool:
        return True
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        w = window or self.default_window
        return df['close'].pct_change(w)
