import pandas as pd
from typing import Dict
from .broker import SimulationBroker
from .context import Context

class CrossSectionalEngine:
    """
    截面回测引擎 (Cross-Sectional Backtest Engine)
    
    适用于多因子选股等需要在特定时间点对全市场股票进行横向比较的策略。
    区别于事件驱动的逐K线回测，本引擎侧重于周期性调仓 (Rebalancing)。
    """
    
    def __init__(self, initial_cash: float = 1000000.0, broker: SimulationBroker = None):
        self.initial_cash = initial_cash
        self.broker = broker
    
    def run(self, strategy, data_dict: Dict[str, pd.DataFrame], benchmark_code: str = None):
        """
        运行回测
        
        :param strategy: 策略实例，需实现 prepare_factors 和 on_rebalance 方法
        :param data_dict: 股票数据字典 {code: dataframe}
        :param benchmark_code: 基准指数代码 (可选)
        """
        # 1. 初始化上下文
        context = Context(self.initial_cash, broker=self.broker)
        strategy.initialize(context)
        
        # 2. 数据预处理 & 因子计算
        print("正在准备因子数据...")
        # 确保所有DataFrame按时间排序并设置索引
        clean_data = {}
        all_dates = set()
        
        for code, df in data_dict.items():
            if df is None or df.empty:
                continue
            df = df.sort_values('date').reset_index(drop=True)
            df.set_index('date', inplace=False) # 保持 reset_index 状态用于后续处理
            clean_data[code] = df
            all_dates.update(df['date'].tolist())
            
        # 让策略预计算因子 (返回一个 MultiIndex DataFrame 或 字典)
        # 格式建议: Index=[date, code], Columns=[factor1, factor2, ...]
        factor_data = strategy.prepare_factors(clean_data)
        
        # 3. 准备时间轴
        sorted_dates = sorted(list(all_dates))
        equity_curve = []
        
        print(f"开始回测，时间跨度: {sorted_dates[0]} 至 {sorted_dates[-1]}")
        
        # 4. 时间步进循环
        for current_date in sorted_dates:
            context.current_dt = current_date
            
            # 4.1 更新当日价格快照
            current_prices = {}
            current_bars = {}
            valid_codes = [] # 当日有交易的股票
            
            for code, df in clean_data.items():
                # 寻找当日数据 (这里为了性能可以用索引查找，此处简化逻辑)
                # 假设 df 已经包含 date 列
                day_data = df[df['date'] == current_date]
                if not day_data.empty:
                    day_row = day_data.iloc[0]
                    price = day_row['close']
                    current_prices[code] = price
                    current_bars[code] = day_row
                    valid_codes.append(code)
            
            context.current_prices = current_prices
            context.before_trading_day(current_date, current_bars)
            
            # 4.2 检查是否需要调仓
            # 策略需要自行判断今天是否是调仓日
            if hasattr(strategy, 'on_rebalance'):
                # 提取当日因子数据
                if isinstance(factor_data, pd.DataFrame) and 'date' in factor_data.index.names:
                    try:
                        # 尝试获取当日截面因子
                        daily_factors = factor_data.xs(current_date, level='date')
                    except KeyError:
                        daily_factors = pd.DataFrame()
                else:
                    # 备用方案：如果策略没有返回标准的 MultiIndex
                    daily_factors = None

                strategy.on_rebalance(context, valid_codes, daily_factors)
            
            # 4.3 每日结算
            market_value = 0.0
            for code, pos in context.positions.items():
                price = current_prices.get(code, pos.last_price or pos.avg_price) # 如果今日停牌，用成本价或最后价格
                pos.last_price = price
                market_value += pos.quantity * price
            
            total_asset = context.cash + market_value
            
            equity_curve.append({
                'date': current_date,
                'total_asset': total_asset,
                'cash': context.cash,
                'market_value': market_value,
                'holdings_count': len(context.positions)
            })
            
        return {
            'equity_curve': pd.DataFrame(equity_curve),
            'trades': context.trade_history,
            'closed_trades': context.closed_trades,
            'final_value': equity_curve[-1]['total_asset'] if equity_curve else self.initial_cash
        }
