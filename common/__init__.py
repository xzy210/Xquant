# common package
"""
共享模块包

包含 trading_app 和 strategy_app 共享的基础功能：
- data_loader: 数据加载
- indicators: 技术指标计算
"""

from .data_loader import (
    StockDataCache,
    get_stock_cache,
    load_stock_data,
    get_stock_list,
    load_stock_name_map,
    get_date_range,
    ETFDataCache,
    get_etf_cache,
    load_etf_data,
    get_etf_list,
    load_etf_name_map,
    load_etf_categories,
    get_etf_date_range,
)

from .data_portal import (
    AssetMetadata,
    BarsMetadata,
    BarsResult,
    CacheRefreshResult,
    DailyDataStatus,
    DataPortal,
    FreshnessStatus,
    MarketDataBundle,
    StrategyDataView,
    get_data_portal,
    set_data_portal,
)

from .execution_contract import (
    FillReport,
    OrderExecutionReport,
    OrderIntent,
    PortfolioPlanner,
    RebalanceIntent,
    StrategySignal,
    TargetPortfolio,
)

from .strategy_spec import (
    StrategySpec,
    normalize_strategy_symbol,
)

from .indicators import (
    compute_ma,
    compute_ema,
    compute_macd,
    compute_kdj,
    compute_bbi,
    compute_boll,
    compute_rsi,
    compute_volume_ma,
    attach_all_indicators,
)

__all__ = [
    # Data loader
    'StockDataCache',
    'get_stock_cache',
    'load_stock_data',
    'get_stock_list',
    'load_stock_name_map',
    'get_date_range',
    'ETFDataCache',
    'get_etf_cache',
    'load_etf_data',
    'get_etf_list',
    'load_etf_name_map',
    'load_etf_categories',
    'get_etf_date_range',
    # Data portal
    'AssetMetadata',
    'BarsMetadata',
    'BarsResult',
    'CacheRefreshResult',
    'DailyDataStatus',
    'DataPortal',
    'FreshnessStatus',
    'MarketDataBundle',
    'StrategyDataView',
    'get_data_portal',
    'set_data_portal',
    # Execution contract
    'FillReport',
    'OrderExecutionReport',
    'OrderIntent',
    'PortfolioPlanner',
    'RebalanceIntent',
    'StrategySignal',
    'TargetPortfolio',
    # Strategy spec
    'StrategySpec',
    'normalize_strategy_symbol',
    # Indicators
    'compute_ma',
    'compute_ema',
    'compute_macd',
    'compute_kdj',
    'compute_bbi',
    'compute_boll',
    'compute_rsi',
    'compute_volume_ma',
    'attach_all_indicators',
]
