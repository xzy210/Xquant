"""
ATR波动率突破策略

核心逻辑：
1. 计算N日ATR（平均真实波幅）
2. 上轨 = 今日开盘价 + ATR × multiplier
3. 下轨 = 今日开盘价 - ATR × multiplier
4. 价格突破上轨 → 买入信号
5. 价格跌破下轨或触发止损 → 卖出

适用场景：捕捉趋势启动的大行情
"""

from .base_strategy import BaseStrategy
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional


class VolatilityBreakoutStrategy(BaseStrategy):
    """
    ATR波动率突破策略（海龟交易法简化版）
    """
    
    def __init__(self):
        super().__init__()
        self.name = "ATR波动率突破"
        self.description = "基于ATR的波动率突破策略，捕捉趋势启动行情"
        self.params = {
            "atr_period": 20,          # ATR计算周期
            "multiplier": 2.0,         # 突破倍数（2倍ATR）
            "stop_loss_atr": 2.0,      # 止损倍数（2倍ATR）
            "take_profit_atr": 4.0,    # 止盈倍数（4倍ATR）
            "trailing_stop": True,     # 是否启用移动止损
            "trailing_atr": 3.0,       # 移动止损倍数
            "max_hold_days": 20,       # 最大持仓天数
            "position_pct": 0.95,      # 每次使用资金比例
        }
        # 回测状态跟踪
        self.entry_prices = {}       # 入场价格 {code: price}
        self.entry_dates = {}        # 入场日期 {code: date}
        self.highest_prices = {}     # 持仓期间最高价 {code: price}
        self.atr_values = {}         # 入场时的ATR值 {code: atr}
    
    def _calculate_atr(self, data: pd.DataFrame, period: int = 20) -> pd.Series:
        """
        计算ATR（平均真实波幅）
        """
        high = data['high']
        low = data['low']
        close = data['close']
        
        # 真实波幅 = max(high-low, |high-preclose|, |low-preclose|)
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        return atr
    
    def _get_signals(self, data: pd.DataFrame) -> Dict[str, Any]:
        """
        计算突破信号
        
        Returns:
            {
                'atr': float,           # 当前ATR值
                'upper_band': float,    # 上轨
                'lower_band': float,    # 下轨
                'breakout_up': bool,    # 向上突破
                'breakout_down': bool,  # 向下突破
                'trend_strength': float # 趋势强度(0-100)
            }
        """
        if len(data) < self.params['atr_period'] + 5:
            return {}
        
        atr = self._calculate_atr(data, self.params['atr_period'])
        current_atr = atr.iloc[-1]
        
        if pd.isna(current_atr) or current_atr == 0:
            return {}
        
        # 今日开盘价（用昨日收盘价近似，或实际开盘数据）
        today_open = data.iloc[-1]['open']
        
        # 上下轨
        multiplier = self.params['multiplier']
        upper_band = today_open + current_atr * multiplier
        lower_band = today_open - current_atr * multiplier
        
        # 今日价格
        today_high = data.iloc[-1]['high']
        today_low = data.iloc[-1]['low']
        today_close = data.iloc[-1]['close']
        
        # 突破判断
        breakout_up = today_high > upper_band
        breakout_down = today_low < lower_band
        
        # 计算趋势强度（ADX简化版）
        dm_plus = data['high'].diff()
        dm_minus = -data['low'].diff()
        
        dm_plus[dm_plus < 0] = 0
        dm_minus[dm_minus < 0] = 0
        
        di_plus = dm_plus.rolling(window=self.params['atr_period']).mean()
        di_minus = dm_minus.rolling(window=self.params['atr_period']).mean()
        
        dx = abs(di_plus - di_minus) / (di_plus + di_minus) * 100
        trend_strength = dx.iloc[-1] if not pd.isna(dx.iloc[-1]) else 50
        
        return {
            'atr': current_atr,
            'upper_band': upper_band,
            'lower_band': lower_band,
            'breakout_up': breakout_up,
            'breakout_down': breakout_down,
            'trend_strength': trend_strength,
            'today_close': today_close,
            'today_high': today_high,
            'today_low': today_low,
            'today_open': today_open
        }
    
    def check(self, code: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        选股模式：检测是否出现向上突破信号
        """
        signals = self._get_signals(data)
        
        if not signals:
            return None
        
        # 只选向上突破的
        if not signals['breakout_up']:
            return None
        
        # 可选：过滤趋势强度
        if signals['trend_strength'] < 25:
            return None  # 趋势太弱，可能是假突破
        
        return {
            "code": code,
            "name": "",
            "date": data.iloc[-1]['date'],
            "close": signals['today_close'],
            "atr": round(signals['atr'], 3),
            "upper_band": round(signals['upper_band'], 2),
            "trend_strength": round(signals['trend_strength'], 1),
            "info": f"ATR突破 | 强度:{signals['trend_strength']:.0f} | ATR:{signals['atr']:.2f}"
        }
    
    def initialize(self, context):
        """
        回测初始化
        """
        self.entry_prices = {}
        self.entry_dates = {}
        self.highest_prices = {}
        self.atr_values = {}
    
    def on_bar(self, context, bars: Dict[str, Any], history: Dict[str, pd.DataFrame] = None):
        """
        回测交易逻辑
        """
        for code, bar in bars.items():
            current_price = bar['close']
            current_date = bar['date']
            current_high = bar['high']
            current_low = bar['low']
            
            # 获取历史数据计算信号
            hist_data = history.get(code) if history else None
            if hist_data is None or len(hist_data) < self.params['atr_period'] + 5:
                continue
            
            signals = self._get_signals(hist_data)
            if not signals:
                continue
            
            current_atr = signals['atr']
            
            # ========== 1. 持仓管理（卖出逻辑） ==========
            if code in context.positions and context.positions[code].quantity > 0:
                pos = context.positions[code]
                entry_price = self.entry_prices.get(code, pos.avg_price)
                highest = self.highest_prices.get(code, entry_price)
                
                # 更新持仓期间最高价
                if current_high > highest:
                    self.highest_prices[code] = current_high
                    highest = current_high
                
                # 计算当前盈亏
                pnl_pct = (current_price - entry_price) / entry_price
                
                # A. 固定止损（入场价 - N倍ATR）
                stop_price = entry_price - self.params['stop_loss_atr'] * self.atr_values.get(code, current_atr)
                if current_low <= stop_price:
                    context.order(code, -pos.quantity, reason=f"固定止损 {pnl_pct*100:.2f}%")
                    self._clear_position_data(code)
                    continue
                
                # B. 固定止盈（入场价 + N倍ATR）
                take_profit_price = entry_price + self.params['take_profit_atr'] * self.atr_values.get(code, current_atr)
                if current_high >= take_profit_price:
                    context.order(code, -pos.quantity, reason=f"固定止盈 {pnl_pct*100:.2f}%")
                    self._clear_position_data(code)
                    continue
                
                # C. 移动止损（跟踪最高价回撤）
                if self.params['trailing_stop']:
                    trailing_price = highest - self.params['trailing_atr'] * self.atr_values.get(code, current_atr)
                    if current_low <= trailing_price and current_price > entry_price:
                        context.order(code, -pos.quantity, reason=f"移动止盈 {pnl_pct*100:.2f}%")
                        self._clear_position_data(code)
                        continue
                
                # D. 时间止损（持仓超过最大天数）
                if code in self.entry_dates:
                    hold_days = (pd.to_datetime(current_date) - pd.to_datetime(self.entry_dates[code])).days
                    if hold_days >= self.params['max_hold_days']:
                        context.order(code, -pos.quantity, reason=f"时间止损 持有{hold_days}天")
                        self._clear_position_data(code)
                        continue
                
                # 有持仓时不再开新仓
                continue
            
            # ========== 2. 信号检测（买入逻辑） ==========
            # 判断是否有向上突破信号
            if signals['breakout_up']:
                # 趋势强度过滤
                if signals['trend_strength'] < 25:
                    continue
                
                # 计算买入数量
                available_cash = context.cash * self.params['position_pct']
                
                # 风控：单只股票最大仓位不超过总资金的20%
                max_single_position = context.initial_cash * 0.20
                available_cash = min(available_cash, max_single_position)
                
                qty = int(available_cash / current_price / 100) * 100
                
                if qty >= 100:
                    if context.order(code, qty, reason=f"ATR突破 强度:{signals['trend_strength']:.0f}"):
                        self.entry_prices[code] = current_price
                        self.entry_dates[code] = current_date
                        self.highest_prices[code] = current_high
                        self.atr_values[code] = current_atr
    
    def _clear_position_data(self, code: str):
        """清空持仓相关数据"""
        self.entry_prices.pop(code, None)
        self.entry_dates.pop(code, None)
        self.highest_prices.pop(code, None)
        self.atr_values.pop(code, None)
    
    def get_default_params_grid(self) -> Dict[str, list]:
        """
        返回参数优化网格（用于参数寻优）
        """
        return {
            "atr_period": [10, 14, 20, 30],
            "multiplier": [1.5, 2.0, 2.5, 3.0],
            "stop_loss_atr": [1.5, 2.0, 2.5],
            "take_profit_atr": [3.0, 4.0, 5.0],
            "trailing_stop": [True, False],
            "max_hold_days": [10, 15, 20, 30]
        }
