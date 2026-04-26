"""
多股票交易环境

支持在训练过程中使用多只股票的数据，每个episode随机选择一只股票，
这样模型可以学习到更通用的交易策略，而不是只针对单只股票。
"""
import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces
import sys
import os
import random
from pathlib import Path
from ta.trend import MACD, SMAIndicator
from ta.momentum import RSIIndicator

# Add project root to path to import trading_app.data_loader
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from trading_app.data_loader import load_stock_data


def filter_stock_codes(stock_codes: list, 
                       exclude_cyb: bool = True,      # 排除创业板 (300xxx, 301xxx)
                       exclude_kcb: bool = True,      # 排除科创板 (688xxx)
                       exclude_bse: bool = True,      # 排除北交所 (8xxxxx, 43xxxx, 87xxxx)
                       exclude_st: bool = True,       # 排除ST股票
                       stocklist_df: pd.DataFrame = None) -> list:
    """
    根据条件筛选股票代码
    
    Args:
        stock_codes: 股票代码列表
        exclude_cyb: 是否排除创业板 (300xxx, 301xxx)
        exclude_kcb: 是否排除科创板 (688xxx)
        exclude_bse: 是否排除北交所 (8xxxxx, 43xxxx, 87xxxx)
        exclude_st: 是否排除ST股票
        stocklist_df: 股票列表DataFrame（包含股票名称，用于排除ST）
    
    Returns:
        筛选后的股票代码列表
    """
    filtered = []
    
    # 创建股票名称映射（用于ST检查）
    name_map = {}
    if stocklist_df is not None and exclude_st:
        name_map = dict(zip(stocklist_df['symbol'].astype(str).str.zfill(6), stocklist_df['name']))
    
    for code in stock_codes:
        code_str = str(code).zfill(6)
        
        # 排除创业板 (300xxx, 301xxx)
        if exclude_cyb and (code_str.startswith('300') or code_str.startswith('301')):
            continue
        
        # 排除科创板 (688xxx)
        if exclude_kcb and code_str.startswith('688'):
            continue
        
        # 排除北交所 (8xxxxx, 43xxxx, 87xxxx)
        if exclude_bse:
            if code_str.startswith('8') or code_str.startswith('43') or code_str.startswith('87'):
                continue
        
        # 排除ST股票
        if exclude_st and name_map:
            name = name_map.get(code_str, '')
            if 'ST' in name or '*ST' in name:
                continue
        
        filtered.append(code_str)
    
    return filtered


def get_available_stock_codes(data_dir: str, 
                              stocklist_path: str = None,
                              exclude_cyb: bool = True,
                              exclude_kcb: bool = True,
                              exclude_bse: bool = True,
                              exclude_st: bool = True,
                              min_data_days: int = 500) -> list:
    """
    获取可用的股票代码列表
    
    Args:
        data_dir: 数据目录
        stocklist_path: 股票列表CSV路径
        exclude_cyb: 是否排除创业板
        exclude_kcb: 是否排除科创板
        exclude_bse: 是否排除北交所
        exclude_st: 是否排除ST股票
        min_data_days: 最少数据天数要求
    
    Returns:
        可用股票代码列表
    """
    # 获取数据目录下所有 Parquet 文件
    data_path = Path(data_dir)
    parquet_files = list(data_path.glob("*.parquet"))
    
    stock_codes = []
    for f in parquet_files:
        code = f.stem  # 文件名（不含扩展名）
        if len(code) == 6 and code.isdigit():
            stock_codes.append(code)
    
    # 加载股票列表（用于ST检查）
    stocklist_df = None
    if stocklist_path and os.path.exists(stocklist_path):
        try:
            stocklist_df = pd.read_csv(stocklist_path)
        except:
            pass
    
    # 筛选股票
    filtered_codes = filter_stock_codes(
        stock_codes, 
        exclude_cyb=exclude_cyb,
        exclude_kcb=exclude_kcb,
        exclude_bse=exclude_bse,
        exclude_st=exclude_st,
        stocklist_df=stocklist_df
    )
    
    # 检查数据量
    valid_codes = []
    for code in filtered_codes:
        # 使用 load_stock_data 检查，它会自动优先选择 parquet
        try:
            df = load_stock_data(code, data_dir=data_dir, use_cache=False)
            if df is not None and len(df) >= min_data_days:
                valid_codes.append(code)
        except:
            pass
    
    return sorted(valid_codes)


class MultiStockTradingEnv(gym.Env):
    """
    多股票交易环境
    
    支持在训练过程中使用多只股票的数据，每个episode随机选择一只股票。
    这样模型可以学习到更通用的交易策略。
    
    特点：
    1. 每个episode开始时随机选择一只股票
    2. 支持 MaskablePPO 的 Action Masking
    3. 动作空间与单股票环境一致
    """
    metadata = {'render_modes': ['human']}

    def __init__(self, 
                 stock_codes: list,  # 股票代码列表
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
                 trade_amount_percent: float = 0.5,
                 random_stock_per_episode: bool = True):
        """
        Args:
            stock_codes: 股票代码列表
            data_dir: 数据目录
            initial_balance: 初始资金
            lookback_window: 观察窗口大小
            render_mode: 渲染模式
            buy_rate: 买入佣金率
            buy_min: 买入最低佣金
            sell_rate: 卖出佣金率
            sell_min: 卖出最低佣金
            stamp_duty: 印花税率
            lot_size: 每手股数
            trade_amount_percent: 交易比例
            random_stock_per_episode: 每个episode是否随机选择股票
        """
        super(MultiStockTradingEnv, self).__init__()

        self.stock_codes = list(stock_codes)
        if not self.stock_codes:
            raise ValueError("stock_codes cannot be empty")
        
        self.data_dir = data_dir
        self.initial_balance = initial_balance
        self.lookback_window = lookback_window
        self.render_mode = render_mode
        self.lot_size = lot_size
        self.trade_amount_percent = trade_amount_percent
        self.random_stock_per_episode = random_stock_per_episode
        
        # Fee Config
        self.buy_rate = buy_rate
        self.buy_min = buy_min
        self.sell_rate = sell_rate
        self.sell_min = sell_min
        self.stamp_duty = stamp_duty

        # 预加载所有股票数据
        self.stock_data = {}
        self._load_all_data()
        
        # 当前使用的股票
        self.current_stock_code = self.stock_codes[0]
        self.df = self.stock_data[self.current_stock_code]
        
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
        
        # 统计信息
        self.episode_count = 0
        self.stock_episode_count = {code: 0 for code in self.stock_codes}
        
    def _load_all_data(self):
        """预加载所有股票数据"""
        valid_codes = []
        
        for code in self.stock_codes:
            try:
                df = load_stock_data(code, data_dir=self.data_dir, adj="qfq")
                if df is not None and len(df) > self.lookback_window + 100:
                    df = self._add_indicators(df)
                    df = df.dropna().reset_index(drop=True)
                    if len(df) > self.lookback_window + 100:
                        self.stock_data[code] = df
                        valid_codes.append(code)
            except Exception as e:
                print(f"Warning: Could not load data for {code}: {e}")
        
        if not valid_codes:
            raise ValueError("No valid stock data found")
        
        self.stock_codes = valid_codes
        print(f"[MultiStockEnv] Loaded {len(self.stock_codes)} stocks successfully")
        
    def _add_indicators(self, df):
        """添加技术指标"""
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
        
        # 每个episode随机选择一只股票
        if self.random_stock_per_episode:
            self.current_stock_code = random.choice(self.stock_codes)
        else:
            # 轮流使用每只股票
            idx = self.episode_count % len(self.stock_codes)
            self.current_stock_code = self.stock_codes[idx]
        
        self.df = self.stock_data[self.current_stock_code]
        
        # 更新统计
        self.episode_count += 1
        self.stock_episode_count[self.current_stock_code] += 1
        
        # 随机起始点（确保有足够数据）
        max_start = len(self.df) - self.lookback_window - 1
        if max_start > self.lookback_window:
            # 有一定概率从头开始，有一定概率随机起始
            if random.random() < 0.3:
                self.current_step = self.lookback_window
            else:
                self.current_step = random.randint(self.lookback_window, max_start)
        else:
            self.current_step = self.lookback_window
        
        self.balance = self.initial_balance
        self.shares_held = 0
        self.cost_basis = 0
        self.net_worth = self.initial_balance
        self.max_net_worth = self.initial_balance
        self.trades = []
        
        observation = self._next_observation()
        info = {
            "action_mask": self.action_masks(),
            "stock_code": self.current_stock_code
        }
        return observation, info

    def _next_observation(self):
        """构建观察空间"""
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
        original_action = action_type
        
        trade_happened = False
        invalid_action = False
        masked_from = None

        # 获取动作掩码
        action_mask = self.action_masks()
        can_buy = action_mask[1]
        can_sell = action_mask[3]

        # Action Masking：无效动作转为 Hold
        if action_type in [1, 2] and not can_buy:
            masked_from = action_type
            action_type = 0
            invalid_action = True
        elif action_type in [3, 4] and not can_sell:
            masked_from = action_type
            action_type = 0
            invalid_action = True
        
        if action_type in [1, 2]:  # Buy 50% 或 Buy 100%
            buy_percent = 0.5 if action_type == 1 else 1.0
            available_for_trade = self.balance * buy_percent
            
            est_shares = int(available_for_trade // (current_price * (1 + self.buy_rate)))
            shares_to_buy = (est_shares // self.lot_size) * self.lot_size
            
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
                        'percent': int(buy_percent * 100),
                        'stock_code': self.current_stock_code
                    })
                    trade_happened = True
                    
        elif action_type in [3, 4]:  # Sell 50% 或 Sell 100%
            sell_percent = 0.5 if action_type == 3 else 1.0
            shares_to_sell = int(self.shares_held * sell_percent)
            shares_to_sell = (shares_to_sell // self.lot_size) * self.lot_size
            
            if shares_to_sell < self.lot_size and self.shares_held >= self.lot_size:
                shares_to_sell = self.lot_size
            
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
                    'percent': int(sell_percent * 100),
                    'stock_code': self.current_stock_code
                })
                trade_happened = True
        
        # Update Step
        self.current_step += 1
        
        # 计算新的净值
        new_net_worth = self.balance + self.shares_held * current_price
        
        # ==================== 奖励函数优化 (引入超额收益 + 风控) ====================
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
        if action_type in [3, 4] and trade_happened:
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
        
        # 5. 新高奖励
        if new_net_worth > self.max_net_worth:
            reward += 0.1
        
        self.net_worth = new_net_worth
        if self.net_worth > self.max_net_worth:
            self.max_net_worth = self.net_worth
            
        # 终止条件
        terminated = False
        if self.current_step >= len(self.df) - 1:
            terminated = True
            total_return = (self.net_worth - self.initial_balance) / self.initial_balance
            reward += total_return * 5
        
        if self.net_worth < self.initial_balance * 0.3:
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
            'action_mask': self.action_masks(),
            'stock_code': self.current_stock_code
        }
        
        return self._next_observation(), reward, terminated, truncated, info

    def render(self):
        if self.render_mode == 'human':
            print(f"[{self.current_stock_code}] Step: {self.current_step}, "
                  f"Net Worth: {self.net_worth:.2f}, Balance: {self.balance:.2f}, "
                  f"Shares: {self.shares_held}")
    
    def get_stock_stats(self):
        """获取各股票的训练统计"""
        return {
            'episode_count': self.episode_count,
            'stock_episode_count': self.stock_episode_count.copy()
        }

