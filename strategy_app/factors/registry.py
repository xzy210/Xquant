"""
Factor Registry - Singleton pattern for managing all registered factors
"""
from typing import Dict, List, Type, Optional
import pandas as pd
from .base_factor import BaseFactor


class FactorRegistry:
    """
    Factor Registry - Singleton pattern
    
    Manages all registered factors and provides methods for computing factors.
    
    Usage:
from trading_app.factors import factor_registry
        
        # Compute single factor
        momentum = factor_registry.compute('momentum_20d', df)
        
        # Batch compute multiple factors
        df_with_factors = factor_registry.compute_batch(
            ['momentum_20d', 'volatility_20d'], df
        )
        
        # List all factors
        all_factors = factor_registry.list_factors()
        
        # List factors by category
        momentum_factors = factor_registry.list_factors(category='momentum')
    """
    
    _instance = None
    _factors: Dict[str, BaseFactor] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._factors = {}
        return cls._instance
    
    def register(self, factor_class: Type[BaseFactor]):
        """
        Register a factor class
        
        Can be used as a decorator:
            @factor_registry.register
            class MyFactor(BaseFactor):
                ...
        
        Args:
            factor_class: Factor class to register
            
        Returns:
            The registered factor class (for decorator usage)
        """
        factor = factor_class()
        self._factors[factor.name] = factor
        return factor_class
    
    def get(self, name: str) -> Optional[BaseFactor]:
        """
        Get factor instance by name
        
        Args:
            name: Factor name
            
        Returns:
            BaseFactor instance or None if not found
        """
        return self._factors.get(name)
    
    def list_factors(self, category: Optional[str] = None) -> List[str]:
        """
        List all registered factor names
        
        Args:
            category: Filter by category (optional)
            
        Returns:
            List of factor names
        """
        if category:
            return [name for name, f in self._factors.items() if f.category == category]
        return list(self._factors.keys())
    
    def list_categories(self) -> List[str]:
        """
        List all factor categories
        
        Returns:
            List of unique category names
        """
        return list(set(f.category for f in self._factors.values()))
    
    def get_factor_info(self, name: str) -> Optional[Dict]:
        """
        Get detailed factor information
        
        Args:
            name: Factor name
            
        Returns:
            Dict with factor info or None if not found
        """
        factor = self._factors.get(name)
        if factor is None:
            return None
        return {
            'name': factor.name,
            'category': factor.category,
            'description': factor.description,
            'default_window': factor.default_window
        }
    
    def get_all_factor_info(self) -> List[Dict]:
        """
        Get info for all registered factors
        
        Returns:
            List of factor info dicts
        """
        return [self.get_factor_info(name) for name in self._factors.keys()]
    
    def compute(self, name: str, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        """
        Compute a single factor
        
        Args:
            name: Factor name
            df: DataFrame with OHLCV data
            window: Calculation window (optional)
            
        Returns:
            pd.Series with computed factor values
            
        Raises:
            ValueError: If factor name is not found
        """
        factor = self._factors.get(name)
        if factor is None:
            raise ValueError(f"Unknown factor: {name}. Available factors: {self.list_factors()}")
        return factor.compute(df, window)
    
    def compute_batch(self, names: List[str], df: pd.DataFrame, 
                      windows: Optional[Dict[str, int]] = None) -> pd.DataFrame:
        """
        Batch compute multiple factors
        
        Args:
            names: List of factor names to compute
            df: DataFrame with OHLCV data
            windows: Dict mapping factor names to custom windows (optional)
            
        Returns:
            DataFrame with original data plus computed factor columns
        """
        result = df.copy()
        windows = windows or {}
        
        for name in names:
            window = windows.get(name)
            result[name] = self.compute(name, df, window)
        
        return result
    
    def __len__(self) -> int:
        return len(self._factors)
    
    def __contains__(self, name: str) -> bool:
        return name in self._factors


# Global singleton instance
factor_registry = FactorRegistry()
