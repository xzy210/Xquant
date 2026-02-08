"""
Factor base class definition
"""
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd


class BaseFactor(ABC):
    """
    Base class for all factors
    
    All factor implementations should inherit from this class and implement
    the required abstract methods.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """
        Factor unique identifier name
        
        Returns:
            str: Factor name (e.g., 'momentum_20d')
        """
        pass
    
    @property
    @abstractmethod
    def category(self) -> str:
        """
        Factor category
        
        Returns:
            str: Category name (e.g., 'momentum', 'volatility', 'volume', 'value')
        """
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """
        Factor description
        
        Returns:
            str: Human-readable description of the factor
        """
        pass
    
    @property
    def default_window(self) -> int:
        """
        Default calculation window
        
        Returns:
            int: Default window size for rolling calculations
        """
        return 20
    
    @property
    def neutralizable(self) -> bool:
        """
        Whether this factor should be neutralized (e.g., market-cap neutralization).
        
        Factors that have significant correlation with market capitalization
        should return True. Factors that are already normalized/standardized
        (e.g., RSI, KDJ) or are market-cap itself should return False.
        
        Returns:
            bool: True if factor should be neutralized, False otherwise
        """
        return False
    
    @abstractmethod
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        """
        Compute factor values
        
        Args:
            df: DataFrame containing at least OHLCV columns
                Required columns: 'close'
                Optional columns: 'open', 'high', 'low', 'volume'
            window: Calculation window (optional, uses default_window if not specified)
        
        Returns:
            pd.Series: Computed factor values with same index as input df
        """
        pass
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', category='{self.category}')"
