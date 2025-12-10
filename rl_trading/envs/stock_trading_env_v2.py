import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces
import sys
import os
from pathlib import Path
from ta.trend import MACD, SMAIndicator
from ta.momentum import RSIIndicator

# Add project root to path to import data_loader
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pyqt_app.data_loader import load_stock_data


class StockTradingEnvV2(gym.Env):
    """
    A股股票交易环境 V2 - 优化版本
    
    主要改进：
    1. 扩展动作空间：支持多种买卖比例，允许快速满仓/清仓
    2. 改进奖励函数：添加频繁交易惩罚、回撤惩罚、持仓奖励
    3. 添加止损止盈机制
    
    动作空间 (7个动作):
        0 = Hold (持有观望)
        1 = Buy 25% (小仓买入)
        2 = Buy 50% (半仓买入)
        3 = Buy 100% (满仓买入)
        4 = Sell 25% (小仓卖出)
        5 = Sell 50% (半仓卖出)
        6 = Sell 100% (清仓卖出)
    """
    metadata = {'render_modes': ['human']}

    # 动作常量
    ACTION_HOLD = 0
    ACTION_BUY_25 = 1
    ACTION_BUY_50 = 2
    ACTION_BUY_100 = 3
    ACTION_SELL_25 = 4
    ACTION_SELL_50 = 5
    ACTION_SELL_100 = 6
    
    # 各动作对应的交易比例
    TRADE_RATIOS = {
        ACTION_BUY_25: 0.25,
        ACTION_BUY_50: 0.50,
        ACTION_BUY_100: 1.0,
        ACTION_SELL_25: 0.25,
        ACTION_SELL_50: 0.50,
        ACTION_SELL_100: 1.0,
    }

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
                 # === 新增参数 ===
                 enable_stop_loss: bool = False,  # 是否启用自动止损
                 stop_loss_pct: float = 0.08,     # 止损线 (8%亏损)
                 take_profit_pct: float = 0.20,   # 止盈线 (20%盈利)
                 trading_cooldown: int = 3,       # 交易冷却期（天数）
                 drawdown_penalty_threshold: float = 0.1,  # 回撤惩罚阈值
                 max_position_days: int = 60):    # 最大持仓天数警告
        super(StockTradingEnvV2, self).__init__()

        self.stock_code = stock_code
        self.data_dir = data_dir
        self.initial_balance = initial_balance
        self.lookback_window = lookback_window
        self.render_mode = render_mode
        self.lot_size = lot_size
        
        # Fee Config
        self.buy_rate = buy_rate
        self.buy_min = buy_min
        self.sell_rate = sell_rate
        self.sell_min = sell_min
        self.stamp_duty = stamp_duty
        
        # 新增配置
        self.enable_stop_loss = enable_stop_loss
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trading_cooldown = trading_cooldown
        self.drawdown_penalty_threshold = drawdown_penalty_threshold
        self.max_position_days = max_position_days

        # Load Data
        self.df = self._load_data()
        if self.df is None:
            raise ValueError(f"Could not load data for stock code: {stock_code}")
        
        # Add technical indicators
        self.df = self._add_indicators(self.df)
        self.df = self.df.dropna().reset_index(drop=True)
        
        # 扩展动作空间: 0=Hold, 1-3=Buy(不同比例), 4-6=Sell(不同比例)
        self.action_space = spaces.Discrete(7)

        # Define Observation Space
        # Features: 
        # 1. Open, High, Low, Close, Volume (Normalized by Close)
        # 2. MACD, Signal, Hist, RSI, SMA (Normalized)
        # 3. Balance ratio, Shares held ratio, Cost basis ratio
        # 4. Has position (Binary: 0 or 1)
        # 5. Action feasibility: can_buy, can_sell (Binary)
        # 6. 新增: 持仓天数, 浮动盈亏率, 距上次交易天数, 当前回撤率
        # Total features = 16 + 4 = 20
        self.num_features = 20
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, 
            shape=(lookback_window, self.num_features), 
            dtype=np.float32
        )

        # State variables
        self._reset_state()
        
    def _reset_state(self):
        """重置所有状态变量"""
        self.current_step = 0
        self.balance = self.initial_balance
        self.shares_held = 0
        self.cost_basis = 0
        self.net_worth = self.initial_balance
        self.max_net_worth = self.initial_balance
        self.trades = []
        
        # 新增状态跟踪
        self.position_entry_step = None  # 建仓时的step
        self.last_trade_step = 0         # 上次交易的step
        self.consecutive_trades = 0       # 连续交易次数
        self.total_fees_paid = 0          # 累计手续费
        self._current_price = None

    def _load_data(self):
        return load_stock_data(self.stock_code, data_dir=self.data_dir, adj="qfq")

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
    
    def _can_buy(self, price=None, ratio=0.25):
        """判断是否可以买入至少1手"""
        if price is None:
            price = self._get_current_price()
        available = self.balance * ratio
        min_cash_needed = self.lot_size * price * (1 + self.buy_rate) + self.buy_min
        return available >= min_cash_needed
    
    def _can_sell(self, ratio=0.25):
        """判断是否可以卖出"""
        shares_to_sell = int(self.shares_held * ratio)
        shares_to_sell = (shares_to_sell // self.lot_size) * self.lot_size
        return shares_to_sell >= self.lot_size

    def action_masks(self) -> np.ndarray:
        """
        返回有效动作的掩码，供 MaskablePPO 使用
        
        Returns:
            np.ndarray: shape (7,), dtype=bool
        """
        price = self._get_current_price()
        
        # 计算各买入比例是否可行
        can_buy_25 = self._can_buy(price, 0.25)
        can_buy_50 = self._can_buy(price, 0.50)
        can_buy_100 = self._can_buy(price, 1.0)
        
        # 计算各卖出比例是否可行
        can_sell_25 = self._can_sell(0.25)
        can_sell_50 = self._can_sell(0.50)
        can_sell_100 = self._can_sell(1.0)
        
        return np.array([
            True,           # Hold 总是有效
            can_buy_25,     # Buy 25%
            can_buy_50,     # Buy 50%
            can_buy_100,    # Buy 100%
            can_sell_25,    # Sell 25%
            can_sell_50,    # Sell 50%
            can_sell_100,   # Sell 100%
        ], dtype=bool)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        
        self._reset_state()
        self.current_step = self.lookback_window
        
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
        
        # 6. 新增特征
        # 持仓天数 (归一化)
        if self.position_entry_step is not None:
            position_days = (self.current_step - self.position_entry_step)
            obs[:, 16] = min(position_days / self.max_position_days, 2.0)
        else:
            obs[:, 16] = 0.0
            
        # 浮动盈亏率
        if self.shares_held > 0 and self.cost_basis > 0:
            unrealized_pnl = (last_close - self.cost_basis) / self.cost_basis
            obs[:, 17] = np.clip(unrealized_pnl, -1.0, 1.0)
        else:
            obs[:, 17] = 0.0
            
        # 距上次交易天数 (归一化)
        days_since_trade = self.current_step - self.last_trade_step
        obs[:, 18] = min(days_since_trade / 30.0, 2.0)
        
        # 当前回撤率
        if self.max_net_worth > 0:
            current_drawdown = (self.max_net_worth - self.net_worth) / self.max_net_worth
            obs[:, 19] = min(current_drawdown, 0.5)
        else:
            obs[:, 19] = 0.0
        
        return obs.astype(np.float32)

    def _execute_buy(self, current_price, ratio):
        """执行买入操作"""
        available_for_trade = self.balance * ratio
        
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
                
                # 更新持仓
                was_empty = self.shares_held == 0
                total_shares = self.shares_held + shares_to_buy
                if self.shares_held > 0:
                    self.cost_basis = (self.cost_basis * self.shares_held + current_price * shares_to_buy) / total_shares
                else:
                    self.cost_basis = current_price
                
                self.shares_held = total_shares
                
                # 记录建仓时间
                if was_empty:
                    self.position_entry_step = self.current_step
                
                self.total_fees_paid += commission
                
                return True, shares_to_buy, commission
        
        return False, 0, 0

    def _execute_sell(self, current_price, ratio, date):
        """执行卖出操作"""
        shares_to_sell = int(self.shares_held * ratio)
        shares_to_sell = (shares_to_sell // self.lot_size) * self.lot_size
        
        # 如果是清仓，确保全部卖出
        if ratio >= 0.99:
            shares_to_sell = (self.shares_held // self.lot_size) * self.lot_size
        
        # 确保至少卖1手
        if shares_to_sell < self.lot_size and self.shares_held >= self.lot_size:
            shares_to_sell = self.lot_size
        
        if shares_to_sell >= self.lot_size:
            amount = shares_to_sell * current_price
            commission = max(amount * self.sell_rate, self.sell_min)
            stamp_tax = amount * self.stamp_duty
            total_fee = commission + stamp_tax
            
            revenue = amount - total_fee
            self.balance += revenue
            self.shares_held -= shares_to_sell
            
            profit_per_share = current_price - self.cost_basis if self.cost_basis > 0 else 0
            
            if self.shares_held == 0:
                self.cost_basis = 0
                self.position_entry_step = None
            
            self.total_fees_paid += total_fee
            
            return True, shares_to_sell, total_fee, profit_per_share
        
        return False, 0, 0, 0

    def step(self, action):
        current_price = self._get_current_price()
        date = self.df.iloc[self.current_step]["date"]
        
        action_type = int(action)
        original_action = action_type
        
        trade_happened = False
        invalid_action = False
        trade_fee = 0
        trade_profit = 0
        
        # 获取动作掩码
        action_mask = self.action_masks()
        
        # Action Masking：无效动作转为 Hold
        if not action_mask[action_type]:
            invalid_action = True
            action_type = self.ACTION_HOLD
        
        # 执行动作
        if action_type == self.ACTION_HOLD:
            pass  # 不操作
            
        elif action_type in [self.ACTION_BUY_25, self.ACTION_BUY_50, self.ACTION_BUY_100]:
            ratio = self.TRADE_RATIOS[action_type]
            success, shares, fee = self._execute_buy(current_price, ratio)
            if success:
                trade_happened = True
                trade_fee = fee
                self.trades.append({
                    'date': date, 'type': 'buy', 
                    'price': current_price, 'shares': shares, 
                    'fee': fee, 'ratio': ratio
                })
                
        elif action_type in [self.ACTION_SELL_25, self.ACTION_SELL_50, self.ACTION_SELL_100]:
            ratio = self.TRADE_RATIOS[action_type]
            success, shares, fee, profit = self._execute_sell(current_price, ratio, date)
            if success:
                trade_happened = True
                trade_fee = fee
                trade_profit = profit
                self.trades.append({
                    'date': date, 'type': 'sell', 
                    'price': current_price, 'shares': shares, 
                    'fee': fee, 'ratio': ratio, 'profit_per_share': profit
                })
        
        # 更新交易跟踪
        if trade_happened:
            days_since_last = self.current_step - self.last_trade_step
            if days_since_last <= self.trading_cooldown:
                self.consecutive_trades += 1
            else:
                self.consecutive_trades = 1
            self.last_trade_step = self.current_step
        else:
            # 如果不交易，逐渐减少连续交易计数
            if self.consecutive_trades > 0:
                days_since_last = self.current_step - self.last_trade_step
                if days_since_last > self.trading_cooldown:
                    self.consecutive_trades = max(0, self.consecutive_trades - 1)
        
        # Update Step
        self.current_step += 1
        
        # 计算新的净值
        new_net_worth = self.balance + self.shares_held * current_price
        
        # ==================== 改进的奖励函数 V2 ====================
        reward = self._calculate_reward(
            action_type, original_action, trade_happened, invalid_action,
            current_price, new_net_worth, trade_fee, trade_profit
        )
        
        self.net_worth = new_net_worth
        if self.net_worth > self.max_net_worth:
            self.max_net_worth = self.net_worth
            
        # 终止条件
        terminated = False
        if self.current_step >= len(self.df) - 1:
            terminated = True
            # Episode 结束时的总体奖励
            total_return = (self.net_worth - self.initial_balance) / self.initial_balance
            reward += total_return * 5
        
        if self.net_worth < self.initial_balance * 0.5:  # 亏损50%终止
            terminated = True
            reward = -10
            
        truncated = False
        
        info = {
            'net_worth': self.net_worth,
            'balance': self.balance,
            'shares_held': self.shares_held,
            'date': date,
            'trade_happened': trade_happened,
            'action': action_type,
            'original_action': original_action,
            'invalid_action': invalid_action,
            'action_mask': self.action_masks(),
            'total_fees': self.total_fees_paid,
            'consecutive_trades': self.consecutive_trades,
        }
        
        return self._next_observation(), reward, terminated, truncated, info

    def _calculate_reward(self, action_type, original_action, trade_happened, 
                          invalid_action, current_price, new_net_worth, 
                          trade_fee, trade_profit):
        """
        改进的奖励函数 V6 (平衡版)
        
        核心理念：
        - 奖励盈利交易，轻微惩罚亏损短线
        - 不惩罚所有交易，只惩罚频繁亏损的短线
        - 空仓时间过长要惩罚
        - 持仓盈利时鼓励继续持有
        """
        reward = 0.0
        
        # 获取技术指标
        frame = self.df.iloc[self.current_step - 1] if self.current_step > 0 else self.df.iloc[0]
        rsi = frame.get("rsi", 50)
        macd_diff = frame.get("macd_diff", 0)
        sma = frame.get("sma", current_price)
        
        # 计算持仓天数
        holding_days = 0
        if self.position_entry_step is not None:
            holding_days = self.current_step - self.position_entry_step
        
        # 计算空仓天数
        days_without_position = self.current_step - self.last_trade_step if self.shares_held == 0 else 0
        
        # === 1. 基础奖励：净值变化（这是最重要的！）===
        net_worth_change = (new_net_worth - self.net_worth) / self.initial_balance
        reward += net_worth_change * 50  # 强调净值变化
        
        # === 2. 卖出决策奖励 ===
        if trade_happened and action_type in [self.ACTION_SELL_25, self.ACTION_SELL_50, self.ACTION_SELL_100]:
            if self.cost_basis > 0:
                profit_rate = trade_profit / self.cost_basis
                
                if profit_rate > 0:
                    # 盈利卖出 - 一定要给奖励！
                    base_reward = min(profit_rate * 5, 0.6)
                    
                    if holding_days >= 10:
                        reward += base_reward * 1.3  # 长线额外奖励
                    elif holding_days >= 5:
                        reward += base_reward * 1.1
                    else:
                        reward += base_reward * 0.8  # 短线盈利也给奖励，只是少一点
                else:
                    # 亏损卖出
                    loss_rate = abs(profit_rate)
                    if loss_rate > self.stop_loss_pct:
                        # 及时止损是好的
                        reward += 0.02
                    elif holding_days < 3:
                        # 短线亏损，轻微惩罚
                        reward -= 0.05
                    else:
                        # 正常止损
                        reward -= 0.02
        
        # === 3. 买入决策 ===
        if trade_happened and action_type in [self.ACTION_BUY_25, self.ACTION_BUY_50, self.ACTION_BUY_100]:
            # 基础：买入本身不惩罚
            # RSI低位买入给奖励
            if rsi < 35:
                reward += 0.05
            elif rsi < 50:
                reward += 0.02
            
            # 价格低于均线买入给奖励
            if current_price < sma:
                reward += 0.03
            
            # MACD正向
            if macd_diff > 0:
                reward += 0.02
        
        # === 4. 交易成本（轻微惩罚）===
        if trade_happened:
            fee_penalty = trade_fee / self.initial_balance * 5
            reward -= min(fee_penalty, 0.03)
            
            # 只惩罚非常频繁的交易（连续5次以上）
            if self.consecutive_trades > 5:
                reward -= 0.02 * (self.consecutive_trades - 5)
        
        # === 5. Hold 决策 ===
        if action_type == self.ACTION_HOLD:
            if self.shares_held > 0:
                # 持仓中 Hold
                if self.cost_basis > 0:
                    unrealized_pnl = (current_price - self.cost_basis) / self.cost_basis
                    
                    # 盈利持仓继续Hold - 好！
                    if unrealized_pnl > 0.03:
                        reward += 0.01
                    
                    # 严重浮亏不止损 - 轻微惩罚
                    if unrealized_pnl < -self.stop_loss_pct:
                        reward -= 0.01
                    
                    # 大幅浮盈+RSI超买，应该考虑卖出
                    if unrealized_pnl > 0.15 and rsi > 75:
                        reward -= 0.01
            else:
                # 空仓 Hold
                # 长期空仓要惩罚！鼓励参与市场
                if days_without_position > 20:
                    reward -= 0.01
                
                # 明显的买入机会没买入
                if rsi < 30 and current_price < sma * 0.95:
                    reward -= 0.02
                
                # 错过大涨
                price_change = (current_price - frame.get("open", current_price)) / current_price
                if price_change > 0.02:  # 当天涨超2%
                    reward -= 0.01
        
        # === 6. 回撤控制 ===
        if self.max_net_worth > 0:
            current_drawdown = (self.max_net_worth - new_net_worth) / self.max_net_worth
            if current_drawdown > 0.12:
                excess = current_drawdown - 0.12
                reward -= min(excess * 2, 0.2)
        
        # === 7. 新高奖励 ===
        if new_net_worth > self.max_net_worth:
            reward += 0.05
        
        # === 8. 无效动作惩罚 ===
        if invalid_action:
            reward -= 0.1
        
        # === 最终裁剪 ===
        reward = np.clip(reward, -1.0, 1.0)
        
        return reward

    def render(self):
        if self.render_mode == 'human':
            print(f"Step: {self.current_step}, Net Worth: {self.net_worth:.2f}, "
                  f"Balance: {self.balance:.2f}, Shares: {self.shares_held}, "
                  f"Fees: {self.total_fees_paid:.2f}")

