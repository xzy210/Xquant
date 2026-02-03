from .base_strategy import BaseStrategy
import pandas as pd
from typing import Dict, Any, Optional

class ContinuousDropReboundStrategy(BaseStrategy):
    """
    连续下跌后的拐头阳策略 (完整版)
    包含选股逻辑(check)和回测交易逻辑(on_bar)
    
    逻辑：
    1. 近期（过去N天）有较大幅度的下跌趋势。
    2. 最近4天K线形态为：阴、阴、阳、阳。
    3. 最后一根阳线的收盘价格高于第一根阴线的开盘价格。
    """
    
    def __init__(self):
        super().__init__()
        self.name = "连续下跌后拐头阳"
        self.description = "寻找近期大幅下跌后，出现强力反转信号（阴阴阳阳且反包）的股票"
        self.params = {
            "trend_days": 10,      # 趋势判断天数
            "drop_threshold": 0.10, # 下跌幅度阈值 (10%)
            "hold_days": 5,        # 最大持仓天数 (回测用)
            "stop_loss": 0.05,     # 止损幅度 (回测用)
            "take_profit": 0.10    # 止盈幅度 (回测用)
        }
        # 回测临时变量
        self.entry_dates = {} # 记录买入日期 {code: date}

    def check(self, code: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        选股逻辑: 判断当下是否满足买入条件
        """
        if self._is_buy_signal(data):
            # 重新计算跌幅用于展示
            trend_data = data.iloc[-4-self.params["trend_days"] : -4]
            start_price = trend_data.iloc[0]['open']
            end_price = trend_data.iloc[-1]['close']
            drop_rate = (start_price - end_price) / start_price
            
            day_0 = data.iloc[-1]
            return {
                "code": code,
                "name": "",
                "date": day_0['date'].strftime("%Y-%m-%d"),
                "close": day_0['close'],
                "drop_rate": f"{drop_rate*100:.2f}%",
                "info": f"满足模型, 前期跌幅{drop_rate*100:.0f}%"
            }
        return None

    def initialize(self, context):
        """回测初始化"""
        self.entry_dates = {}

    def on_bar(self, context, bars: Dict[str, Any], history: Dict[str, pd.DataFrame] = None):
        """
        回测交易逻辑
        """
        for code, bar in bars.items():
            current_price = bar['close']
            current_date = bar['date']
            
            # --- 1. 持仓管理 (卖出逻辑) ---
            if code in context.positions:
                pos = context.positions[code]
                if pos.quantity > 0:
                    # 计算收益率
                    pnl_pct = (current_price - pos.avg_price) / pos.avg_price
                    
                    # A. 止盈止损
                    if pnl_pct <= -self.params['stop_loss']:
                        context.order(code, -pos.quantity, reason=f"止损 {pnl_pct*100:.2f}%")
                        self.entry_dates.pop(code, None)
                        continue
                    elif pnl_pct >= self.params['take_profit']:
                        context.order(code, -pos.quantity, reason=f"止盈 {pnl_pct*100:.2f}%")
                        self.entry_dates.pop(code, None)
                        continue
                        
                    # B. 时间止损 (持有超过 N 天)
                    # 简单示例：如果想加时间止损，可以在这里判断日期差
                    pass
                
                # 如果有持仓，暂不加仓
                continue

            # --- 2. 信号检测 (买入逻辑) ---
            # 获取该股票截止当前的历史数据
            if history and code in history:
                hist_data = history[code]
                
                # 判断是否有买入信号
                if self._is_buy_signal(hist_data):
                    # 简单的资金管理：每次用剩余资金的 90% 买入 (All in 模式)
                    # 或者固定金额：target_amount = 20000
                    target_amount = context.cash * 0.9 
                    qty = int(target_amount / current_price / 100) * 100
                    
                    if qty >= 100:
                        if context.order(code, qty, reason="触发买入信号"):
                            self.entry_dates[code] = current_date

    def _is_buy_signal(self, data: pd.DataFrame) -> bool:
        """
        核心形态判断逻辑
        """
        min_len = 4 + self.params["trend_days"]
        if len(data) < min_len:
            return False
            
        # 取最后4天
        last_4_days = data.iloc[-4:]
        day_3 = last_4_days.iloc[0] # Day -3 (阴)
        day_2 = last_4_days.iloc[1] # Day -2 (阴)
        day_1 = last_4_days.iloc[2] # Day -1 (阳)
        day_0 = last_4_days.iloc[3] # Day 0 (阳)
        
        # 形态: 阴 阴 阳 阳
        is_pattern_match = (
            day_3['close'] < day_3['open'] and 
            day_2['close'] < day_2['open'] and 
            day_1['close'] > day_1['open'] and 
            day_0['close'] > day_0['open']
        )
        
        if not is_pattern_match:
            return False
            
        # 反包: 最后一根阳线收盘价 > 第一根阴线开盘价
        if day_0['close'] <= day_3['open']:
            return False
            
        # 前期跌幅判断
        # 取出 trend_days 这一段
        trend_end_idx = -4
        trend_start_idx = -4 - self.params["trend_days"]
        trend_data = data.iloc[trend_start_idx:trend_end_idx]
        
        if trend_data.empty: return False
            
        start_price = trend_data.iloc[0]['open']
        end_price = trend_data.iloc[-1]['close']
        
        if start_price == 0: return False
        
        drop_rate = (start_price - end_price) / start_price
        
        return drop_rate >= self.params["drop_threshold"]
