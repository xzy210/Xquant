from .base_strategy import BaseStrategy
import pandas as pd
from typing import Dict, Any, Optional

class ContinuousDropReboundStrategy(BaseStrategy):
    """
    连续下跌后的拐头阳策略
    
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
            "drop_threshold": 0.10 # 下跌幅度阈值 (10%)
        }

    def check(self, code: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        # 1. 数据长度检查
        # 需要至少 4 + trend_days 天的数据
        min_len = 4 + self.params["trend_days"]
        if len(data) < min_len:
            return None
            
        # 获取最近4天数据
        last_4_days = data.iloc[-4:]
        day_3 = last_4_days.iloc[0] # Day -3 (第一根阴线)
        day_2 = last_4_days.iloc[1] # Day -2
        day_1 = last_4_days.iloc[2] # Day -1
        day_0 = last_4_days.iloc[3] # Day 0 (最后一根阳线)
        
        # 2. 检查K线形态: 阴 阴 阳 阳
        # 阴线: Close < Open
        # 阳线: Close > Open
        is_yin_3 = day_3['close'] < day_3['open']
        is_yin_2 = day_2['close'] < day_2['open']
        is_yang_1 = day_1['close'] > day_1['open']
        is_yang_0 = day_0['close'] > day_0['open']
        
        if not (is_yin_3 and is_yin_2 and is_yang_1 and is_yang_0):
            return None
            
        # 3. 检查反包条件: 最后一根阳线收盘价 > 第一根阴线开盘价
        if day_0['close'] <= day_3['open']:
            return None
            
        # 4. 检查前期下跌趋势
        # 获取趋势判断区间的数据 (不包含最近4天)
        trend_end_idx = -4
        trend_start_idx = -4 - self.params["trend_days"]
        trend_data = data.iloc[trend_start_idx:trend_end_idx]
        
        if trend_data.empty:
            return None
            
        # 计算跌幅: (区间最高价 - 区间最低价) / 区间最高价 
        # 或者更严格一点: (区间起始Open - 区间结束Close) / 区间起始Open
        # 这里采用区间起始Open到区间结束Close的跌幅，更能反映趋势
        start_price = trend_data.iloc[0]['open']
        end_price = trend_data.iloc[-1]['close']
        
        if start_price == 0:
            return None
            
        drop_rate = (start_price - end_price) / start_price
        
        if drop_rate < self.params["drop_threshold"]:
            return None
            
        # 符合所有条件
        return {
            "code": code,
            "name": "", # 名称将在外部填充
            "date": day_0['date'].strftime("%Y-%m-%d"),
            "close": day_0['close'],
            "drop_rate": f"{drop_rate*100:.2f}%",
            "info": "满足阴阴阳阳且反包，前期跌幅: " + f"{drop_rate*100:.2f}%"
        }
