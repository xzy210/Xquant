# widgets模块
"""
trading_app 主应用的 UI 组件

注意：策略相关的 widget（选股、回测、因子库、AI训练、ETF网格）
已迁移到 strategy_app/widgets/
"""
from .kline_widget import KLineWidget
from .stock_list_widget import StockListWidget
from .trading_simulator_widget import TradingSimulatorWidget
from .etf_list_widget import ETFListWidget
from .watchlist_widget import WatchlistWidget
from .conditional_order_dialog import ConditionalOrderWidget, AddConditionalOrderDialog
from .index_list_widget import IndexListWidget

__all__ = [
    'KLineWidget',
    'StockListWidget',
    'TradingSimulatorWidget',
    'ETFListWidget',
    'WatchlistWidget',
    'ConditionalOrderWidget',
    'AddConditionalOrderDialog',
    'IndexListWidget',
]
