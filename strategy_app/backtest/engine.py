from typing import Any

import pandas as pd
from common.data_portal import MarketDataBundle
from .broker import SimulationBroker
from .context import Context

class BacktestEngine:
    """
    通用回测引擎
    """
    def __init__(self, initial_cash=100000.0, broker: SimulationBroker = None):
        self.initial_cash = initial_cash
        self.broker = broker
        
    def run(self, strategy, data: Any, code: str = "UNKNOWN"):
        """
        运行回测
        :param strategy: 策略实例
        :param data: MarketDataBundle 或历史数据 DataFrame (需包含 date, open, close, high, low)
        :param code: 回测的标的代码
        """
        contract_info = None
        if isinstance(data, MarketDataBundle):
            bundle = data
            if hasattr(strategy, 'on_data_bundle'):
                strategy.on_data_bundle(bundle)
            code = bundle.primary_symbol or (bundle.symbols[0] if bundle.symbols else code)
            view = bundle.require(code)
            if hasattr(strategy, 'prepare_data_view'):
                data = strategy.prepare_data_view(view)
            else:
                data = view.to_frame()
            contract_info = {
                "schema_version": bundle.schema_version,
                "symbols": bundle.symbols,
                "primary_symbol": code,
                "benchmark_symbol": bundle.benchmark_symbol,
            }

        # 1. 初始化
        context = Context(self.initial_cash, broker=self.broker)
        strategy.initialize(context)
        
        # 2. 准备数据迭代
        # 确保数据按时间排序
        if 'date' in data.columns:
            data = data.sort_values('date').reset_index(drop=True)
            
        equity_curve = []
        
        # 3. 时间步进循环
        for index, row in data.iterrows():
            current_date = row['date']
            current_price = row['close']
            
            # 更新上下文状态
            context.current_dt = current_date
            context.current_prices[code] = current_price
            context.before_trading_day(current_date, {code: row})
            
            # 准备历史数据切片 (截止到当前时刻)
            # 在实际高性能回测中，这部分会优化，比如只传索引
            # 但为了策略逻辑简单，我们传过去N天的切片
            history_slice = data.iloc[:index+1]
            
            # 执行策略的 on_bar 逻辑
            strategy.on_bar(context, {code: row}, {code: history_slice})
            
            # 记录每日资产
            market_value = 0
            for pos_code, pos in context.positions.items():
                p = context.current_prices.get(pos_code, pos.last_price or pos.avg_price)
                pos.last_price = p
                market_value += pos.quantity * p
                
            total_asset = context.cash + market_value
            equity_curve.append({
                'date': current_date,
                'total_asset': total_asset,
                'cash': context.cash,
                'market_value': market_value,
                'close': current_price
            })
            
        return {
            'equity_curve': pd.DataFrame(equity_curve),
            'trades': context.trade_history,
            'closed_trades': context.closed_trades,
            'execution_reports': context.execution_reports,
            'final_value': equity_curve[-1]['total_asset'] if equity_curve else self.initial_cash,
            'data_contract': contract_info,
        }
