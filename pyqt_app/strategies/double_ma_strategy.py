from .base_strategy import BaseStrategy
import pandas as pd
from typing import Dict, Any, Optional

class DoubleMAStrategy(BaseStrategy):
    """
    双均线趋势策略 (Double Moving Average)
    
    逻辑：
    1. 计算两条均线：短期均线 (short_window) 和 长期均线 (long_window)。
    2. 金叉 (Short > Long) 且昨日未金叉 -> 买入信号。
    3. 死叉 (Short < Long) 且昨日未死叉 -> 卖出信号。
    """
    
    def __init__(self):
        super().__init__()
        self.name = "双均线趋势策略 (MA金叉死叉)"
        self.description = "最经典的趋势跟踪策略：短期均线上穿长期均线买入(金叉)，下穿卖出(死叉)。"
        self.params = {
            "short_window": 5,   # 短期均线周期 (默认5日)
            "long_window": 20    # 长期均线周期 (默认20日)
        }

    def check(self, code: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        选股模式：检查今天是否刚好发生金叉
        """
        if len(data) < self.params["long_window"] + 2:
            return None
            
        # 计算均线
        short_ma = data['close'].rolling(window=self.params["short_window"]).mean()
        long_ma = data['close'].rolling(window=self.params["long_window"]).mean()
        
        # 获取最后两天的数据
        curr_short = short_ma.iloc[-1]
        curr_long = long_ma.iloc[-1]
        prev_short = short_ma.iloc[-2]
        prev_long = long_ma.iloc[-2]
        
        # 判断金叉：今天短>长，且昨天短<=长
        is_golden_cross = (curr_short > curr_long) and (prev_short <= prev_long)
        
        if is_golden_cross:
            day_0 = data.iloc[-1]
            return {
                "code": code,
                "name": "",
                "date": day_0['date'].strftime("%Y-%m-%d"),
                "close": day_0['close'],
                "info": f"MA{self.params['short_window']} 上穿 MA{self.params['long_window']} 形成金叉"
            }
        return None

    def initialize(self, context):
        """回测初始化"""
        print(f"策略初始化: {self.name}")

    def on_bar(self, context, bars: Dict[str, Any], history: Dict[str, pd.DataFrame] = None):
        """
        回测交易逻辑
        """
        for code, bar in bars.items():
            # 1. 获取历史数据计算指标
            if not history or code not in history:
                continue
                
            hist_data = history[code]
            if len(hist_data) < self.params["long_window"] + 2:
                continue
                
            # 计算均线
            short_win = self.params["short_window"]
            long_win = self.params["long_window"]
            
            # 使用 pandas 计算 (在回测循环中这样计算效率一般，但逻辑清晰)
            # 实际高性能回测会预先计算好所有指标
            closes = hist_data['close']
            ma_short = closes.rolling(window=short_win).mean().iloc[-1]
            ma_long = closes.rolling(window=long_win).mean().iloc[-1]
            
            prev_ma_short = closes.rolling(window=short_win).mean().iloc[-2]
            prev_ma_long = closes.rolling(window=long_win).mean().iloc[-2]
            
            current_price = bar['close']
            
            # --- 交易信号判断 ---
            
            # 2. 持仓管理 (检查是否需要卖出)
            if code in context.positions:
                pos = context.positions[code]
                if pos.quantity > 0:
                    # 死叉卖出 (Short 下穿 Long)
                    if ma_short < ma_long and prev_ma_short >= prev_ma_long:
                        context.order(code, -pos.quantity, reason=f"死叉卖出 (MA{short_win} < MA{long_win})")
                        continue
            
            # 3. 进场管理 (检查是否需要买入)
            else:
                # 金叉买入 (Short 上穿 Long)
                if ma_short > ma_long and prev_ma_short <= prev_ma_long:
                    # 全仓买入 (简单粗暴)
                    amount = context.cash * 0.95
                    qty = int(amount / current_price / 100) * 100
                    if qty >= 100:
                        context.order(code, qty, reason=f"金叉买入 (MA{short_win} > MA{long_win})")

