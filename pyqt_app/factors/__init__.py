"""
Factor Library Module

This module provides a centralized factor registry for computing stock factors.
All factors are automatically registered when the module is imported.

Usage:
    from pyqt_app.factors import factor_registry
    
    # Compute single factor
    momentum = factor_registry.compute('momentum_20d', df)
    
    # Batch compute multiple factors
    df_with_factors = factor_registry.compute_batch(
        ['momentum_20d', 'volatility_20d', 'volume_ratio'], 
        df
    )
    
    # List all available factors
    all_factors = factor_registry.list_factors()
    
    # List factors by category
    momentum_factors = factor_registry.list_factors(category='momentum')
    
    # Get factor information
    info = factor_registry.get_factor_info('momentum_20d')
    
    # Check if factor exists
    if 'momentum_20d' in factor_registry:
        ...

Available Categories:
    - momentum: Price momentum factors (momentum_20d, momentum_60d, reversal_5d, etc.)
    - volatility: Volatility factors (volatility_20d, bias_20, atr_20, etc.)
    - volume: Volume-related factors (turnover_20d, volume_ratio, vwap_20d, etc.)
    - technical: Technical indicators (rsi_14, macd, kdj_k, bollinger_position, etc.)
"""

from .registry import FactorRegistry, factor_registry
from .base_factor import BaseFactor

# Import all factor modules to trigger registration
from . import momentum_factors
from . import volatility_factors
from . import volume_factors
from . import technical_factors

__all__ = [
    'FactorRegistry',
    'factor_registry',
    'BaseFactor',
]
