import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces
import sys
import os
from pathlib import Path
from ta.trend import MACD, SMAIndicator
from ta.momentum import RSIIndicator

# Add project root to path to import common.data_portal
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common.data_portal import get_data_portal

class StockTradingEnv(gym.Env):
    """
    A股股票交易环境，支持 Action Masking
    
    支持 MaskablePPO：实现 action_masks() 方法
    动作空间：
        0 = Hold (总是有效)
        1 = Buy 50% (用50%可用资金买入)
        2 = Buy 100% (用100%可用资金买入)
        3 = Sell 50% (卖出50%持仓)
        4 = Sell 100% (卖出100%持仓)
    
    改进点：
        1. 真正的 Action Masking 支持
        2. 分批买卖支持50%和100%两种比例
        3. 改进的奖励函数
    """
    metadata = {'render_modes': ['human']}

    def __init__(self, 
                 stock_code: str, 
                 data_dir: str = "data", 
                 initial_balance: float = 100000.0, 
                 lookback_window: int = 30,
                 render_mode: str = None,
                 buy_rate: float = 0.0001,
                 buy_min: float = 5.0,
                 sell_rate: float = 0.0001,
                 sell_min: float = 5.0,
                 stamp_duty: float = 0.0005,
                 lot_size: int = 100,
                 trade_amount_percent: float = 0.5):  # 每次交易使用可用资金/持仓的比例
        super(StockTradingEnv, self).__init__()

        self.stock_code = stock_code
        self.data_dir = data_dir
        self.initial_balance = initial_balance
        self.lookback_window = lookback_window
        self.render_mode = render_mode
        self.lot_size = lot_size
        self.trade_amount_percent = trade_amount_percent
        
        # Fee Config
        self.buy_rate = buy_rate
        self.buy_min = buy_min
        self.sell_rate = sell_rate
        self.sell_min = sell_min
        self.stamp_duty = stamp_duty

        # Load Data
        self.df = self._load_data()
        if self.df is None:
            raise ValueError(f"Could not load data for stock code: {stock_code}")
        
        # Add technical indicators
        self.df = self._add_indicators(self.df)
        self.df = self.df.dropna().reset_index(drop=True)
        
        # Define Action Space: 0=Hold, 1=Buy50%, 2=Buy100%, 3=Sell50%, 4=Sell100%
        self.action_space = spaces.Discrete(5)

        # Define Observation Space
        # Features: 
        # 1. Open, High, Low, Close, Volume (Normalized by Close)
        # 2. MACD, Signal, Hist, RSI, SMA (Normalized)
        # 3. Balance ratio, Shares held ratio, Cost basis ratio
        # 4. Has position (Binary: 0 or 1)
        # 5. Action feasibility flags: can_buy, can_sell (Binary)
        # Total features = 5 (OHLCV) + 5 (Indicators) + 3 (Account) + 3 (Binary) = 16
        self.num_features = 16
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, 
            shape=(lookback_window, self.num_features), 
            dtype=np.float32
        )

        # State variables
        self.current_step = 0
        self.balance = initial_balance
        self.shares_held = 0
        self.cost_basis = 0
        self.net_worth = initial_balance
        self.max_net_worth = initial_balance
        self.trades = []
        
        # 缓存当前价格用于 action_masks
        self._current_price = None
        
    def _load_data(self):
        return get_data_portal().get_daily_bars(
            self.stock_code,
            data_dir=self.data_dir,
            adjust="qfq",
            asset_type="stock",
        )

    def _add_indicators(self, df):
        df = df.copy()
        
        # MACD
        macd = MACD(close=df["close"], window_slow=26, window_fast=12, window_sign=9)
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["macd_diff"] = macd.macd_diff()
        
        # RSI
        rsi = RSIIndicator(close=df["close"], window=14)
        df["rsi"] = rsi.rsi()
        
        # SMA
        sma = SMAIndicator(close=df["close"], window=20)
        df["sma"] = sma.sma_indicator()
        
        return df

    def _get_current_price(self):
        """获取当前价格"""
        if self.current_step < len(self.df):
            return self.df.iloc[self.current_step]["close"]
        return self.df.iloc[-1]["close"]
    
    def _can_buy(self, price=None):
        """判断是否可以买入至少1手"""
        if price is None:
            price = self._get_current_price()
        min_cash_needed = self.lot_size * price * (1 + self.buy_rate) + self.buy_min
        return self.balance >= min_cash_needed
    
    def _can_sell(self):
        """判断是否可以卖出"""
        return self.shares_held >= self.lot_size

    def action_masks(self) -> np.ndarray:
        """
        返回有效动作的掩码，供 MaskablePPO 使用
        
        Returns:
            np.ndarray: shape (5,), dtype=bool
                [True, can_buy, can_buy, can_sell, can_sell] 
                - Hold (0) 总是有效
                - Buy 50% (1) 需要有足够资金
                - Buy 100% (2) 需要有足够资金
                - Sell 50% (3) 需要持有股票
                - Sell 100% (4) 需要持有股票
        """
        price = self._get_current_price()
        can_buy = self._can_buy(price)
        can_sell = self._can_sell()
        return np.array([
            True,       # Hold 总是有效
            can_buy,    # Buy 50% 需要资金
            can_buy,    # Buy 100% 需要资金
            can_sell,   # Sell 50% 需要持仓
            can_sell    # Sell 100% 需要持仓
        ], dtype=bool)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        
        self.current_step = self.lookback_window
        self.balance = self.initial_balance
        self.shares_held = 0
        self.cost_basis = 0
        self.net_worth = self.initial_balance
        self.max_net_worth = self.initial_balance
        self.trades = []
        
        observation = self._next_observation()
        info = {"action_mask": self.action_masks()}
        return observation, info

    def _next_observation(self):
        frame = self.df.iloc[self.current_step - self.lookback_window : self.current_step]
        
        last_close = frame["close"].iloc[-1]
        if last_close == 0: 
            last_close = 1
        
        obs = np.zeros((self.lookback_window, self.num_features))
        
        # 1. Prices & Volume
        obs[:, 0] = frame["open"] / last_close
        obs[:, 1] = frame["high"] / last_close
        obs[:, 2] = frame["low"] / last_close
        obs[:, 3] = frame["close"] / last_close
        obs[:, 4] = frame["volume"] / (frame["volume"].mean() + 1e-5)
        
        # 2. Indicators
        obs[:, 5] = frame["macd"] / (last_close + 1e-5)
        obs[:, 6] = frame["macd_signal"] / (last_close + 1e-5)
        obs[:, 7] = frame["macd_diff"] / (last_close + 1e-5)
        obs[:, 8] = frame["rsi"] / 100.0
        obs[:, 9] = frame["sma"] / last_close
        
        # 3. Account State (归一化)
        obs[:, 10] = self.balance / self.initial_balance
        obs[:, 11] = (self.shares_held * last_close) / self.initial_balance
        obs[:, 12] = self.cost_basis / last_close if self.cost_basis > 0 else 0
        
        # 4. 持仓状态
        obs[:, 13] = 1.0 if self.shares_held > 0 else 0.0
        
        # 5. 动作可行性标志
        min_cash_needed = self.lot_size * last_close * (1 + self.buy_rate) + self.buy_min
        obs[:, 14] = 1.0 if self.balance >= min_cash_needed else 0.0
        obs[:, 15] = 1.0 if self.shares_held >= self.lot_size else 0.0
        
        return obs.astype(np.float32)

    def step(self, action):
        current_price = self._get_current_price()
        date = self.df.iloc[self.current_step]["date"]
        
        action_type = int(action)
        original_action = action_type  # 记录原始动作用于惩罚
        
        trade_happened = False
        invalid_action = False
        masked_from = None

        # 获取动作掩码
        action_mask = self.action_masks()
        can_buy = action_mask[1]  # Buy 50% 和 Buy 100% 共用
        can_sell = action_mask[3]  # Sell 50% 和 Sell 100% 共用

        # Action Masking：无效动作转为 Hold
        if action_type in [1, 2] and not can_buy:  # Buy 50% 或 Buy 100%
            masked_from = action_type
            action_type = 0
            invalid_action = True
        elif action_type in [3, 4] and not can_sell:  # Sell 50% 或 Sell 100%
            masked_from = action_type
            action_type = 0
            invalid_action = True
        
        if action_type in [1, 2]:  # Buy 50% 或 Buy 100%
            # 确定买入比例：1=50%, 2=100%
            buy_percent = 0.5 if action_type == 1 else 1.0
            available_for_trade = self.balance * buy_percent
            
            est_shares = int(available_for_trade // (current_price * (1 + self.buy_rate)))
            shares_to_buy = (est_shares // self.lot_size) * self.lot_size
            
            # 确保至少能买1手
            min_cash_needed = self.lot_size * current_price * (1 + self.buy_rate) + self.buy_min
            if shares_to_buy < self.lot_size and self.balance >= min_cash_needed:
                shares_to_buy = self.lot_size
            
            if shares_to_buy >= self.lot_size:
                amount = shares_to_buy * current_price
                commission = max(amount * self.buy_rate, self.buy_min)
                total_cost = amount + commission
                
                if total_cost > self.balance:
                    shares_to_buy = ((self.balance - self.buy_min) // (current_price * (1 + self.buy_rate)) // self.lot_size) * self.lot_size
                    if shares_to_buy >= self.lot_size:
                        amount = shares_to_buy * current_price
                        commission = max(amount * self.buy_rate, self.buy_min)
                        total_cost = amount + commission
                
                if shares_to_buy >= self.lot_size and total_cost <= self.balance:
                    self.balance -= total_cost
                    
                    total_shares = self.shares_held + shares_to_buy
                    if self.shares_held > 0:
                        self.cost_basis = (self.cost_basis * self.shares_held + current_price * shares_to_buy) / total_shares
                    else:
                        self.cost_basis = current_price
                    
                    self.shares_held = total_shares
                    self.trades.append({
                        'date': date, 'type': 'buy', 
                        'price': current_price, 'shares': shares_to_buy, 
                        'fee': commission,
                        'percent': int(buy_percent * 100)
                    })
                    trade_happened = True
                    
        elif action_type in [3, 4]:  # Sell 50% 或 Sell 100%
            # 确定卖出比例：3=50%, 4=100%
            sell_percent = 0.5 if action_type == 3 else 1.0
            shares_to_sell = int(self.shares_held * sell_percent)
            shares_to_sell = (shares_to_sell // self.lot_size) * self.lot_size
            
            # 确保至少卖1手
            if shares_to_sell < self.lot_size and self.shares_held >= self.lot_size:
                shares_to_sell = self.lot_size
            
            # 如果是100%卖出，确保卖完所有持仓（只要能凑整手）
            if action_type == 4:
                shares_to_sell = (self.shares_held // self.lot_size) * self.lot_size
            
            if shares_to_sell >= self.lot_size:
                amount = shares_to_sell * current_price
                
                commission = max(amount * self.sell_rate, self.sell_min)
                stamp_tax = amount * self.stamp_duty
                total_fee = commission + stamp_tax
                
                revenue = amount - total_fee
                self.balance += revenue
                self.shares_held -= shares_to_sell
                
                if self.shares_held == 0:
                    self.cost_basis = 0
                    
                self.trades.append({
                    'date': date, 'type': 'sell', 
                    'price': current_price, 'shares': shares_to_sell, 
                    'fee': total_fee,
                    'percent': int(sell_percent * 100)
                })
                trade_happened = True
        
        # Update Step
        self.current_step += 1
        
        # 计算新的净值
        new_net_worth = self.balance + self.shares_held * current_price
        
        # ==================== 改进的奖励函数 (引入超额收益 + 风控) ====================
        # 获取上一步价格用于计算基准收益 (Buy & Hold)
        prev_price = self.df.iloc[self.current_step - 2]["close"] if self.current_step > 1 else current_price
        if prev_price <= 0: prev_price = current_price
        
        # 1. 核心奖励：Alpha + Beta (混合奖励)
        # 策略收益率 (相对于上一步净值)
        strategy_return = (new_net_worth - self.net_worth) / self.net_worth if self.net_worth > 0 else 0
        # 基准收益率 (股票本身涨跌幅)
        stock_return = (current_price - prev_price) / prev_price
        
        # 计算 Alpha (超额收益)
        alpha = strategy_return - stock_return
        
        # 混合奖励公式 (增加了 clip 防止梯度爆炸)
        # Alpha * 50: 鼓励跑赢大盘
        # Strategy * 10: 鼓励绝对收益
        reward = np.clip(alpha, -0.1, 0.1) * 50.0 + strategy_return * 10.0
        
        # 2. 波动率惩罚 (新增：持有高波动股票要扣分)
        # 这鼓励 AI 在波动率低的时候持有，波动率高的时候空仓
        if self.shares_held > 0:
            volatility = self.df.iloc[max(0, self.current_step-5):self.current_step]["close"].pct_change().std()
            if not np.isnan(volatility):
                reward -= volatility * 2.0 

        # 3. 盈利卖出奖励 (系数降低，作为辅助)
        if action_type in [3, 4] and trade_happened:  # Sell 50% 或 Sell 100%
            if current_price > self.cost_basis and self.cost_basis > 0:
                profit_rate = (current_price - self.cost_basis) / self.cost_basis
                reward += profit_rate * 1.0  # 从 5.0 降到 1.0，防止短视

        # 4. 交易成本 (稍微提高，防止为了微薄 Alpha 频繁操作)
        if trade_happened:
            reward -= 0.1

        # 5. 浮亏惩罚 (加大力度，作为止损机制)
        if self.shares_held > 0 and self.cost_basis > 0:
            unrealized_pnl_rate = (current_price - self.cost_basis) / self.cost_basis
            if unrealized_pnl_rate < -0.1:  # 亏损超过 10% 就开始重罚
                reward -= 0.05  # 每天扣 0.05，迫使它尽快割肉
        
        # 5. 新高奖励（鼓励创造新高）
        if new_net_worth > self.max_net_worth:
            reward += 0.1
        
        self.net_worth = new_net_worth
        if self.net_worth > self.max_net_worth:
            self.max_net_worth = self.net_worth
            
        # 终止条件
        terminated = False
        if self.current_step >= len(self.df) - 1:
            terminated = True
            # Episode 结束时的总体奖励
            total_return = (self.net_worth - self.initial_balance) / self.initial_balance
            reward += total_return * 5  # 最终收益加权
        
        if self.net_worth < self.initial_balance * 0.3:  # 亏损30%终止
            terminated = True
            reward = -10
            
        truncated = False
        
        info = {
            'net_worth': self.net_worth,
            'balance': self.balance,
            'shares_held': self.shares_held,
            'date': date,
            'can_buy': can_buy,
            'can_sell': can_sell,
            'trade_happened': trade_happened,
            'action': action_type,
            'original_action': original_action,
            'masked_from': masked_from,
            'invalid_action': invalid_action,
            'action_mask': self.action_masks()  # 为 MaskablePPO 提供
        }
        
        return self._next_observation(), reward, terminated, truncated, info

    def render(self):
        if self.render_mode == 'human':
            print(f"Step: {self.current_step}, Net Worth: {self.net_worth:.2f}, "
                  f"Balance: {self.balance:.2f}, Shares: {self.shares_held}")
