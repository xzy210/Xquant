# etf_grid_strategy.py - ETF Grid Trading Strategy
"""
ETF Grid Trading Strategy Module

Features:
- Intelligent grid generation based on ATR (Average True Range)
- Support for both arithmetic and geometric grid spacing
- Dynamic grid adjustment based on market volatility
- Position management with pyramid buying/selling
- Risk control with max position limits
- Comprehensive backtesting support
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import json


class GridType(Enum):
    """Grid spacing type"""
    ARITHMETIC = "arithmetic"  # Equal price interval
    GEOMETRIC = "geometric"    # Equal percentage interval


class SignalType(Enum):
    """Trading signal type"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class GridLevel:
    """Single grid level"""
    price: float           # Grid price
    level: int             # Grid level number (0 = base, positive = above, negative = below)
    quantity: int = 0      # Shares held at this level
    avg_cost: float = 0.0  # Average cost at this level
    is_triggered: bool = False  # Whether this level has been triggered


@dataclass
class TradeSignal:
    """Trading signal"""
    signal_type: SignalType
    price: float
    quantity: int
    grid_level: int
    reason: str
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass 
class GridConfig:
    """Grid strategy configuration"""
    # Basic parameters
    base_price: float = 0.0          # Grid center price (0 = auto calculate)
    grid_count: int = 10             # Number of grids on each side
    grid_spacing: float = 0.02       # Grid spacing (2% for geometric, or fixed price for arithmetic)
    grid_type: GridType = GridType.GEOMETRIC
    
    # Position parameters
    initial_capital: float = 100000  # Initial capital
    position_per_grid: float = 0.1   # Position ratio per grid (10% of capital)
    max_position_ratio: float = 0.8  # Maximum total position ratio
    min_trade_amount: int = 100      # Minimum trade unit (shares)
    
    # ATR adaptive parameters
    use_atr_adaptive: bool = True    # Use ATR for adaptive grid spacing
    atr_period: int = 14             # ATR calculation period
    atr_multiplier: float = 1.5      # ATR multiplier for grid spacing
    
    # Risk control
    stop_loss_ratio: float = 0.15    # Stop loss ratio (15%)
    take_profit_ratio: float = 0.30  # Take profit ratio (30%)
    rebalance_threshold: float = 0.1 # Rebalance when price deviates 10% from base
    
    # Trading parameters
    commission_rate: float = 0.0003  # Commission rate (0.03%)
    min_commission: float = 5.0      # Minimum commission
    slippage: float = 0.001          # Slippage (0.1%)

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            'base_price': self.base_price,
            'grid_count': self.grid_count,
            'grid_spacing': self.grid_spacing,
            'grid_type': self.grid_type.value,
            'initial_capital': self.initial_capital,
            'position_per_grid': self.position_per_grid,
            'max_position_ratio': self.max_position_ratio,
            'min_trade_amount': self.min_trade_amount,
            'use_atr_adaptive': self.use_atr_adaptive,
            'atr_period': self.atr_period,
            'atr_multiplier': self.atr_multiplier,
            'stop_loss_ratio': self.stop_loss_ratio,
            'take_profit_ratio': self.take_profit_ratio,
            'rebalance_threshold': self.rebalance_threshold,
            'commission_rate': self.commission_rate,
            'min_commission': self.min_commission,
            'slippage': self.slippage,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'GridConfig':
        """Create from dictionary"""
        if 'grid_type' in data and isinstance(data['grid_type'], str):
            data['grid_type'] = GridType(data['grid_type'])
        return cls(**data)


@dataclass
class GridState:
    """Grid strategy state"""
    grids: List[GridLevel] = field(default_factory=list)
    base_price: float = 0.0
    current_position: int = 0          # Total shares held
    total_cost: float = 0.0            # Total cost
    available_cash: float = 0.0        # Available cash
    realized_profit: float = 0.0       # Realized profit
    unrealized_profit: float = 0.0     # Unrealized profit
    trade_count: int = 0               # Number of trades
    last_trade_price: float = 0.0      # Last trade price
    last_rebalance_date: str = ""      # Last rebalance date
    
    def get_avg_cost(self) -> float:
        """Get average cost per share"""
        if self.current_position > 0:
            return self.total_cost / self.current_position
        return 0.0
    
    def get_total_value(self, current_price: float) -> float:
        """Get total portfolio value"""
        return self.available_cash + self.current_position * current_price
    
    def get_position_ratio(self, current_price: float) -> float:
        """Get current position ratio"""
        total_value = self.get_total_value(current_price)
        if total_value > 0:
            return (self.current_position * current_price) / total_value
        return 0.0


class ETFGridStrategy:
    """
    ETF Grid Trading Strategy
    
    A professional grid trading strategy designed for ETFs with features:
    - ATR-based adaptive grid spacing
    - Intelligent position management  
    - Risk control mechanisms
    - Comprehensive backtesting
    """
    
    def __init__(self, config: GridConfig = None):
        self.config = config or GridConfig()
        self.state = GridState()
        self.trade_history: List[Dict] = []
        self.daily_stats: List[Dict] = []
        
    def initialize(self, data: pd.DataFrame, initial_cash: float = None):
        """
        Initialize the strategy with historical data
        
        Args:
            data: DataFrame with columns [date, open, high, low, close, volume]
            initial_cash: Initial capital (overrides config if provided)
        """
        if initial_cash is not None:
            self.config.initial_capital = initial_cash
            
        self.state.available_cash = self.config.initial_capital
        self.state.current_position = 0
        self.state.total_cost = 0.0
        self.state.realized_profit = 0.0
        self.trade_history = []
        self.daily_stats = []
        
        # Calculate base price if not set
        if self.config.base_price <= 0:
            self.config.base_price = self._calculate_base_price(data)
        
        self.state.base_price = self.config.base_price
        
        # Calculate ATR if adaptive mode enabled
        if self.config.use_atr_adaptive:
            atr = self._calculate_atr(data)
            if atr > 0:
                # Adjust grid spacing based on ATR
                self.config.grid_spacing = (atr / self.config.base_price) * self.config.atr_multiplier
                self.config.grid_spacing = max(0.01, min(0.05, self.config.grid_spacing))  # Clamp to 1%-5%
        
        # Generate grid levels
        self._generate_grids()
        
    def _calculate_base_price(self, data: pd.DataFrame) -> float:
        """Calculate base price from historical data"""
        if data.empty:
            return 0.0
        
        # Use recent 20-day moving average as base price
        recent_data = data.tail(20)
        return recent_data['close'].mean()
    
    def _calculate_atr(self, data: pd.DataFrame, period: int = None) -> float:
        """Calculate Average True Range"""
        if period is None:
            period = self.config.atr_period
            
        if len(data) < period + 1:
            return 0.0
        
        high = data['high'].values
        low = data['low'].values
        close = data['close'].values
        
        tr_list = []
        for i in range(1, len(data)):
            tr = max(
                high[i] - low[i],
                abs(high[i] - close[i-1]),
                abs(low[i] - close[i-1])
            )
            tr_list.append(tr)
        
        if len(tr_list) >= period:
            return np.mean(tr_list[-period:])
        return np.mean(tr_list) if tr_list else 0.0
    
    def _generate_grids(self):
        """Generate grid levels based on configuration"""
        base_price = self.state.base_price
        grid_count = self.config.grid_count
        spacing = self.config.grid_spacing
        
        grids = []
        
        # Generate grids above and below base price
        for i in range(-grid_count, grid_count + 1):
            if self.config.grid_type == GridType.GEOMETRIC:
                # Geometric: equal percentage intervals
                price = base_price * (1 + spacing) ** i
            else:
                # Arithmetic: equal price intervals
                price = base_price + i * spacing
            
            grid = GridLevel(
                price=round(price, 3),
                level=i,
                quantity=0,
                avg_cost=0.0,
                is_triggered=False
            )
            grids.append(grid)
        
        # Sort by price descending (highest first)
        grids.sort(key=lambda x: x.price, reverse=True)
        self.state.grids = grids
    
    def get_grids(self) -> List[GridLevel]:
        """Get all grid levels"""
        return self.state.grids
    
    def get_grid_prices(self) -> List[float]:
        """Get all grid prices sorted ascending"""
        prices = [g.price for g in self.state.grids]
        return sorted(prices)
    
    def find_nearest_grids(self, price: float) -> Tuple[Optional[GridLevel], Optional[GridLevel]]:
        """
        Find the nearest grid levels above and below current price
        
        Returns:
            (grid_above, grid_below)
        """
        grid_above = None
        grid_below = None
        
        sorted_grids = sorted(self.state.grids, key=lambda x: x.price)
        
        for grid in sorted_grids:
            if grid.price <= price:
                grid_below = grid
            elif grid.price > price and grid_above is None:
                grid_above = grid
                break
        
        return grid_above, grid_below
    
    def calculate_trade_quantity(self, price: float, is_buy: bool) -> int:
        """
        Calculate the number of shares to trade
        
        Args:
            price: Current price
            is_buy: True for buy, False for sell
        
        Returns:
            Number of shares (rounded to min_trade_amount)
        """
        if is_buy:
            # Calculate based on position per grid ratio
            trade_value = self.config.initial_capital * self.config.position_per_grid
            quantity = int(trade_value / price / self.config.min_trade_amount) * self.config.min_trade_amount
            
            # Check if enough cash
            cost = quantity * price * (1 + self.config.commission_rate + self.config.slippage)
            if cost > self.state.available_cash:
                quantity = int(self.state.available_cash / price / (1 + self.config.commission_rate + self.config.slippage))
                quantity = (quantity // self.config.min_trade_amount) * self.config.min_trade_amount
            
            # Check max position limit
            current_ratio = self.state.get_position_ratio(price)
            if current_ratio >= self.config.max_position_ratio:
                return 0
                
            return max(0, quantity)
        else:
            # For sell, use the quantity held at the grid level or a portion of total
            quantity = int(self.state.current_position * self.config.position_per_grid)
            quantity = (quantity // self.config.min_trade_amount) * self.config.min_trade_amount
            return min(quantity, self.state.current_position)
    
    def calculate_commission(self, amount: float) -> float:
        """Calculate trading commission"""
        commission = amount * self.config.commission_rate
        return max(commission, self.config.min_commission)
    
    def check_signal(self, current_price: float, prev_price: float, 
                     current_date: str = None) -> Optional[TradeSignal]:
        """
        Check if a trading signal is triggered
        
        Args:
            current_price: Current price
            prev_price: Previous price
            current_date: Current date string
        
        Returns:
            TradeSignal if triggered, None otherwise
        """
        grid_above, grid_below = self.find_nearest_grids(current_price)
        
        # Check for buy signal (price crosses down through a grid)
        if grid_below and prev_price > grid_below.price >= current_price:
            if not grid_below.is_triggered or grid_below.quantity == 0:
                quantity = self.calculate_trade_quantity(current_price, is_buy=True)
                if quantity > 0:
                    return TradeSignal(
                        signal_type=SignalType.BUY,
                        price=current_price,
                        quantity=quantity,
                        grid_level=grid_below.level,
                        reason=f"Price crossed below grid level {grid_below.level} at {grid_below.price:.3f}"
                    )
        
        # Check for sell signal (price crosses up through a grid)
        if grid_above and prev_price < grid_above.price <= current_price:
            if grid_above.is_triggered or self.state.current_position > 0:
                quantity = self.calculate_trade_quantity(current_price, is_buy=False)
                if quantity > 0:
                    return TradeSignal(
                        signal_type=SignalType.SELL,
                        price=current_price,
                        quantity=quantity,
                        grid_level=grid_above.level,
                        reason=f"Price crossed above grid level {grid_above.level} at {grid_above.price:.3f}"
                    )
        
        # Check stop loss
        if self.state.current_position > 0:
            avg_cost = self.state.get_avg_cost()
            if avg_cost > 0:
                loss_ratio = (avg_cost - current_price) / avg_cost
                if loss_ratio >= self.config.stop_loss_ratio:
                    return TradeSignal(
                        signal_type=SignalType.SELL,
                        price=current_price,
                        quantity=self.state.current_position,
                        grid_level=0,
                        reason=f"Stop loss triggered at {loss_ratio*100:.1f}% loss"
                    )
        
        # Check take profit
        if self.state.current_position > 0:
            avg_cost = self.state.get_avg_cost()
            if avg_cost > 0:
                profit_ratio = (current_price - avg_cost) / avg_cost
                if profit_ratio >= self.config.take_profit_ratio:
                    # Sell partial position for profit taking
                    quantity = self.state.current_position // 2
                    quantity = (quantity // self.config.min_trade_amount) * self.config.min_trade_amount
                    if quantity > 0:
                        return TradeSignal(
                            signal_type=SignalType.SELL,
                            price=current_price,
                            quantity=quantity,
                            grid_level=0,
                            reason=f"Take profit triggered at {profit_ratio*100:.1f}% gain"
                        )
        
        return None
    
    def execute_signal(self, signal: TradeSignal, date: str = None) -> bool:
        """
        Execute a trading signal
        
        Args:
            signal: TradeSignal to execute
            date: Trade date
        
        Returns:
            True if successful, False otherwise
        """
        if signal is None:
            return False
        
        price = signal.price * (1 + self.config.slippage if signal.signal_type == SignalType.BUY else 1 - self.config.slippage)
        amount = price * signal.quantity
        commission = self.calculate_commission(amount)
        
        if signal.signal_type == SignalType.BUY:
            total_cost = amount + commission
            if total_cost > self.state.available_cash:
                return False
            
            self.state.available_cash -= total_cost
            self.state.current_position += signal.quantity
            self.state.total_cost += amount
            self.state.trade_count += 1
            self.state.last_trade_price = price
            
            # Update grid state
            for grid in self.state.grids:
                if grid.level == signal.grid_level:
                    grid.is_triggered = True
                    grid.quantity += signal.quantity
                    break
        
        elif signal.signal_type == SignalType.SELL:
            if signal.quantity > self.state.current_position:
                signal.quantity = self.state.current_position
            
            proceeds = amount - commission
            
            # Calculate realized profit
            avg_cost = self.state.get_avg_cost()
            profit = (price - avg_cost) * signal.quantity - commission
            self.state.realized_profit += profit
            
            self.state.available_cash += proceeds
            self.state.current_position -= signal.quantity
            self.state.total_cost = self.state.current_position * avg_cost if self.state.current_position > 0 else 0
            self.state.trade_count += 1
            self.state.last_trade_price = price
            
            # Update grid state
            for grid in self.state.grids:
                if grid.level == signal.grid_level:
                    grid.quantity = max(0, grid.quantity - signal.quantity)
                    if grid.quantity == 0:
                        grid.is_triggered = False
                    break
        
        # Record trade
        trade_record = {
            'date': date or datetime.now().strftime('%Y-%m-%d'),
            'type': signal.signal_type.value,
            'price': round(price, 3),
            'quantity': signal.quantity,
            'amount': round(amount, 2),
            'commission': round(commission, 2),
            'grid_level': signal.grid_level,
            'reason': signal.reason,
            'position_after': self.state.current_position,
            'cash_after': round(self.state.available_cash, 2),
            'realized_profit': round(self.state.realized_profit, 2)
        }
        self.trade_history.append(trade_record)
        
        return True
    
    def check_rebalance(self, current_price: float, current_date: str) -> bool:
        """
        Check if grid rebalancing is needed
        
        Args:
            current_price: Current price
            current_date: Current date
        
        Returns:
            True if rebalanced, False otherwise
        """
        if self.state.base_price <= 0:
            return False
        
        deviation = abs(current_price - self.state.base_price) / self.state.base_price
        
        if deviation >= self.config.rebalance_threshold:
            # Rebalance: adjust base price and regenerate grids
            old_base = self.state.base_price
            self.state.base_price = current_price
            self._generate_grids()
            self.state.last_rebalance_date = current_date
            
            # Record rebalance event
            self.trade_history.append({
                'date': current_date,
                'type': 'rebalance',
                'price': current_price,
                'quantity': 0,
                'amount': 0,
                'commission': 0,
                'grid_level': 0,
                'reason': f'Grid rebalanced from {old_base:.3f} to {current_price:.3f} ({deviation*100:.1f}% deviation)',
                'position_after': self.state.current_position,
                'cash_after': self.state.available_cash,
                'realized_profit': self.state.realized_profit
            })
            return True
        
        return False
    
    def update_unrealized_profit(self, current_price: float):
        """Update unrealized profit based on current price"""
        if self.state.current_position > 0:
            market_value = self.state.current_position * current_price
            self.state.unrealized_profit = market_value - self.state.total_cost
        else:
            self.state.unrealized_profit = 0.0
    
    def get_stats(self, current_price: float) -> Dict[str, Any]:
        """Get current strategy statistics"""
        self.update_unrealized_profit(current_price)
        
        total_value = self.state.get_total_value(current_price)
        total_return = (total_value - self.config.initial_capital) / self.config.initial_capital
        
        return {
            'initial_capital': self.config.initial_capital,
            'current_cash': round(self.state.available_cash, 2),
            'current_position': self.state.current_position,
            'position_value': round(self.state.current_position * current_price, 2),
            'total_value': round(total_value, 2),
            'total_return': round(total_return * 100, 2),
            'realized_profit': round(self.state.realized_profit, 2),
            'unrealized_profit': round(self.state.unrealized_profit, 2),
            'total_profit': round(self.state.realized_profit + self.state.unrealized_profit, 2),
            'avg_cost': round(self.state.get_avg_cost(), 3) if self.state.current_position > 0 else 0,
            'position_ratio': round(self.state.get_position_ratio(current_price) * 100, 2),
            'trade_count': self.state.trade_count,
            'base_price': round(self.state.base_price, 3),
            'grid_count': self.config.grid_count * 2 + 1,
            'grid_spacing': round(self.config.grid_spacing * 100, 2),
        }
    
    def backtest(self, data: pd.DataFrame, progress_callback=None) -> Dict[str, Any]:
        """
        Run backtest on historical data
        
        Args:
            data: DataFrame with columns [date, open, high, low, close, volume]
            progress_callback: Optional callback function(current, total)
        
        Returns:
            Backtest results dictionary
        """
        if data.empty or len(data) < 2:
            return {'error': 'Insufficient data for backtest'}
        
        # Initialize strategy
        self.initialize(data)
        
        # Initial buy at half position
        initial_price = data.iloc[0]['close']
        initial_quantity = int(self.config.initial_capital * 0.5 / initial_price / self.config.min_trade_amount) * self.config.min_trade_amount
        
        if initial_quantity > 0:
            initial_signal = TradeSignal(
                signal_type=SignalType.BUY,
                price=initial_price,
                quantity=initial_quantity,
                grid_level=0,
                reason="Initial position setup"
            )
            self.execute_signal(initial_signal, data.iloc[0]['date'].strftime('%Y-%m-%d') if hasattr(data.iloc[0]['date'], 'strftime') else str(data.iloc[0]['date']))
        
        total_rows = len(data)
        
        # Run through each day
        for i in range(1, total_rows):
            row = data.iloc[i]
            prev_row = data.iloc[i-1]
            
            current_price = row['close']
            prev_price = prev_row['close']
            current_date = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])
            
            # Check for rebalance
            self.check_rebalance(current_price, current_date)
            
            # Check and execute signals
            signal = self.check_signal(current_price, prev_price, current_date)
            if signal:
                self.execute_signal(signal, current_date)
            
            # Update unrealized profit
            self.update_unrealized_profit(current_price)
            
            # Record daily stats
            stats = self.get_stats(current_price)
            stats['date'] = current_date
            stats['price'] = round(current_price, 3)
            self.daily_stats.append(stats)
            
            if progress_callback:
                progress_callback(i, total_rows)
        
        # Calculate final results
        final_price = data.iloc[-1]['close']
        final_stats = self.get_stats(final_price)
        
        # Calculate additional metrics
        if self.daily_stats:
            returns = [s['total_return'] for s in self.daily_stats]
            max_return = max(returns)
            min_return = min(returns)
            
            # Calculate max drawdown
            peak = self.config.initial_capital
            max_drawdown = 0
            for stat in self.daily_stats:
                value = stat['total_value']
                if value > peak:
                    peak = value
                drawdown = (peak - value) / peak
                max_drawdown = max(max_drawdown, drawdown)
            
            # Calculate win rate
            winning_trades = sum(1 for t in self.trade_history 
                               if t['type'] == 'sell' and t.get('realized_profit', 0) > 0)
            total_sells = sum(1 for t in self.trade_history if t['type'] == 'sell')
            win_rate = winning_trades / total_sells if total_sells > 0 else 0
            
            final_stats.update({
                'max_return': round(max_return, 2),
                'min_return': round(min_return, 2),
                'max_drawdown': round(max_drawdown * 100, 2),
                'win_rate': round(win_rate * 100, 2),
                'total_trades': len([t for t in self.trade_history if t['type'] in ['buy', 'sell']]),
                'buy_trades': len([t for t in self.trade_history if t['type'] == 'buy']),
                'sell_trades': len([t for t in self.trade_history if t['type'] == 'sell']),
                'rebalance_count': len([t for t in self.trade_history if t['type'] == 'rebalance']),
            })
        
        return {
            'summary': final_stats,
            'trade_history': self.trade_history,
            'daily_stats': self.daily_stats,
            'config': self.config.to_dict()
        }
    
    def save_state(self, filepath: str):
        """Save strategy state to file"""
        state_data = {
            'config': self.config.to_dict(),
            'state': {
                'base_price': self.state.base_price,
                'current_position': self.state.current_position,
                'total_cost': self.state.total_cost,
                'available_cash': self.state.available_cash,
                'realized_profit': self.state.realized_profit,
                'trade_count': self.state.trade_count,
                'last_trade_price': self.state.last_trade_price,
                'last_rebalance_date': self.state.last_rebalance_date,
                'grids': [
                    {
                        'price': g.price,
                        'level': g.level,
                        'quantity': g.quantity,
                        'avg_cost': g.avg_cost,
                        'is_triggered': g.is_triggered
                    }
                    for g in self.state.grids
                ]
            },
            'trade_history': self.trade_history
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(state_data, f, indent=2, ensure_ascii=False)
    
    def load_state(self, filepath: str):
        """Load strategy state from file"""
        with open(filepath, 'r', encoding='utf-8') as f:
            state_data = json.load(f)
        
        self.config = GridConfig.from_dict(state_data['config'])
        
        state = state_data['state']
        self.state.base_price = state['base_price']
        self.state.current_position = state['current_position']
        self.state.total_cost = state['total_cost']
        self.state.available_cash = state['available_cash']
        self.state.realized_profit = state['realized_profit']
        self.state.trade_count = state['trade_count']
        self.state.last_trade_price = state['last_trade_price']
        self.state.last_rebalance_date = state.get('last_rebalance_date', '')
        
        self.state.grids = [
            GridLevel(
                price=g['price'],
                level=g['level'],
                quantity=g['quantity'],
                avg_cost=g['avg_cost'],
                is_triggered=g['is_triggered']
            )
            for g in state['grids']
        ]
        
        self.trade_history = state_data.get('trade_history', [])


def create_default_etf_config(etf_type: str = 'broad_market') -> GridConfig:
    """
    Create default configuration based on ETF type
    
    Args:
        etf_type: Type of ETF ('broad_market', 'sector', 'commodity', 'bond')
    
    Returns:
        GridConfig with appropriate parameters
    """
    configs = {
        'broad_market': GridConfig(
            grid_count=10,
            grid_spacing=0.02,  # 2% spacing for broad market ETFs
            grid_type=GridType.GEOMETRIC,
            position_per_grid=0.1,
            max_position_ratio=0.8,
            use_atr_adaptive=True,
            atr_multiplier=1.5,
            stop_loss_ratio=0.15,
            take_profit_ratio=0.25,
        ),
        'sector': GridConfig(
            grid_count=8,
            grid_spacing=0.03,  # 3% spacing for sector ETFs (more volatile)
            grid_type=GridType.GEOMETRIC,
            position_per_grid=0.08,
            max_position_ratio=0.7,
            use_atr_adaptive=True,
            atr_multiplier=1.8,
            stop_loss_ratio=0.12,
            take_profit_ratio=0.20,
        ),
        'commodity': GridConfig(
            grid_count=12,
            grid_spacing=0.025,
            grid_type=GridType.GEOMETRIC,
            position_per_grid=0.06,
            max_position_ratio=0.6,
            use_atr_adaptive=True,
            atr_multiplier=2.0,
            stop_loss_ratio=0.10,
            take_profit_ratio=0.18,
        ),
        'bond': GridConfig(
            grid_count=15,
            grid_spacing=0.005,  # 0.5% spacing for bond ETFs (low volatility)
            grid_type=GridType.ARITHMETIC,
            position_per_grid=0.15,
            max_position_ratio=0.9,
            use_atr_adaptive=False,
            stop_loss_ratio=0.05,
            take_profit_ratio=0.08,
        ),
    }
    
    return configs.get(etf_type, configs['broad_market'])
