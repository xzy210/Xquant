# common package
"""
共享模块包

包含 trading_app 和 strategy_app 共享的基础功能：
- data_portal: 统一数据入口
- indicators: 技术指标计算
"""

from .data_portal import (
    AssetMetadata,
    BarsMetadata,
    BarsResult,
    CacheRefreshResult,
    CorporateActionRecord,
    DailyDataStatus,
    DataPortal,
    DataVersionAudit,
    ETFDataCache,
    FreshnessStatus,
    MarketDataBundle,
    ParquetSidecarMetadata,
    StockDataCache,
    StrategyDataView,
    TradingCalendarDay,
    get_data_portal,
    get_date_range,
    get_etf_cache,
    get_etf_date_range,
    get_etf_list,
    get_stock_cache,
    get_stock_list,
    load_etf_categories,
    load_etf_data,
    load_etf_name_map,
    load_stock_data,
    load_stock_name_map,
    set_data_portal,
)

from .daily_update_policy import (
    DailyHistoryPrecheckResult,
    DailyUpdatePolicy,
    DailyUpdateWindow,
    get_daily_update_policy,
    set_daily_update_policy,
)

from .market_data_policy import (
    TickFreshness,
    evaluate_tick_freshness,
    extract_tick_datetime,
    is_etf_like_code,
    is_tick_fresh,
    is_trading_session,
    latest_expected_trading_day,
    normalize_symbol_code,
)

from .xtquant_data_health import (
    FreshnessCheckResult,
    XtquantFreshnessReport,
    evaluate_xtquant_data_freshness,
    test_xtquant_data_freshness,
)

from .kline_update_engine import (
    BatchUpdateSummary,
    check_xtquant_ready,
    run_batched_updates,
    run_xtquant_daily_history_precheck,
    update_rotation_etf_pool,
    update_rotation_single_etf,
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

from .broker_interface import (
    BrokerCancelResult,
    BrokerOrderRequest,
    BrokerProtocol,
    BrokerSubmitResult,
    LiveBrokerAdapter,
)

from .strategy_spec import (
    StrategySpec,
    normalize_strategy_symbol,
)

from .strategy_spec_service import (
    StrategySpecService,
    get_strategy_spec_service,
)

from .strategy_registry_service import (
    StrategyRegistration,
    StrategyRegistryService,
    get_strategy_registry_service,
)

from .strategy_constants import (
    AI_STOCK_STRATEGY_ID,
    AI_STOCK_STRATEGY_NAME,
    AI_STOCK_VIRTUAL_ACCOUNT_ID,
    OWNER_TYPE_AI,
    OWNER_TYPE_ETF_ROTATION,
    OWNER_TYPE_OTHER,
    OWNER_TYPE_UNMANAGED,
    UNMANAGED_STRATEGY_ID,
    UNMANAGED_STRATEGY_NAME,
    UNMANAGED_VIRTUAL_ACCOUNT_ID,
    load_default_etf_rotation_profile,
    normalize_symbol_code,
)

from .events import (
    BacktestEvent,
    EventBus,
    EventHandler,
)

from .experiment_store import (
    ExperimentRecord,
    ExperimentStore,
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
    # Data portal helpers
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
    'CorporateActionRecord',
    'DailyDataStatus',
    'DataPortal',
    'DataVersionAudit',
    'FreshnessStatus',
    'MarketDataBundle',
    'ParquetSidecarMetadata',
    'StrategyDataView',
    'TradingCalendarDay',
    'get_data_portal',
    'set_data_portal',
    # Daily update policy
    'DailyHistoryPrecheckResult',
    'DailyUpdatePolicy',
    'DailyUpdateWindow',
    'get_daily_update_policy',
    'set_daily_update_policy',
    # Market data policy
    'TickFreshness',
    'evaluate_tick_freshness',
    'extract_tick_datetime',
    'is_etf_like_code',
    'is_tick_fresh',
    'is_trading_session',
    'latest_expected_trading_day',
    'normalize_symbol_code',
    # xtquant data health
    'FreshnessCheckResult',
    'XtquantFreshnessReport',
    'evaluate_xtquant_data_freshness',
    'test_xtquant_data_freshness',
    # K-line update engine
    'BatchUpdateSummary',
    'check_xtquant_ready',
    'run_batched_updates',
    'run_xtquant_daily_history_precheck',
    'update_rotation_etf_pool',
    'update_rotation_single_etf',
    # Execution contract
    'FillReport',
    'OrderExecutionReport',
    'OrderIntent',
    'PortfolioPlanner',
    'RebalanceIntent',
    'StrategySignal',
    'TargetPortfolio',
    # Broker interface
    'BrokerCancelResult',
    'BrokerOrderRequest',
    'BrokerProtocol',
    'BrokerSubmitResult',
    'LiveBrokerAdapter',
    # Strategy spec
    'StrategySpec',
    'normalize_strategy_symbol',
    'StrategySpecService',
    'get_strategy_spec_service',
    'StrategyRegistration',
    'StrategyRegistryService',
    'get_strategy_registry_service',
    'AI_STOCK_STRATEGY_ID',
    'AI_STOCK_STRATEGY_NAME',
    'AI_STOCK_VIRTUAL_ACCOUNT_ID',
    'UNMANAGED_STRATEGY_ID',
    'UNMANAGED_STRATEGY_NAME',
    'UNMANAGED_VIRTUAL_ACCOUNT_ID',
    'OWNER_TYPE_AI',
    'OWNER_TYPE_ETF_ROTATION',
    'OWNER_TYPE_UNMANAGED',
    'OWNER_TYPE_OTHER',
    'normalize_symbol_code',
    'load_default_etf_rotation_profile',
    # Events
    'BacktestEvent',
    'EventBus',
    'EventHandler',
    # Experiment store
    'ExperimentRecord',
    'ExperimentStore',
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
