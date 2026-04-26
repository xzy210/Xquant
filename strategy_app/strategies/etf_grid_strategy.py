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
        self._backtest_symbol: str = "ETF_GRID"
        self._backtest_time_col: str = "date"
        self._initial_position_done = False
        self._last_processed_index = -1
        
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

    def initialize_backtest(self, context, prepared_data):
        """Initialize ETF grid state from normalized data prepared by UnifiedBacktestEngine."""
        if not prepared_data.data:
            raise ValueError("ETF grid backtest requires non-empty market data")
        self._backtest_symbol = prepared_data.primary_symbol or next(iter(prepared_data.data.keys()))
        data = prepared_data.data[self._backtest_symbol]
        self._backtest_time_col = "time" if "time" in data.columns else "date"
        self._initial_position_done = False
        self._last_processed_index = -1
        self.initialize(data, initial_cash=context.initial_cash)

    def on_bar(self, context, bars: Dict[str, Any], history: Dict[str, pd.DataFrame] = None):
        """Run one ETF grid step inside the unified backtest event loop."""
        symbol = self._backtest_symbol if self._backtest_symbol in bars else next(iter(bars.keys()), None)
        if not symbol or history is None or symbol not in history:
            return

        hist_data = history[symbol]
        if hist_data is None or hist_data.empty:
            return

        current_index = len(hist_data) - 1
        if current_index <= self._last_processed_index:
            return

        row = hist_data.iloc[-1]
        current_price = float(row["close"])
        current_time = self._format_bar_time(row, self._backtest_time_col)

        if not self._initial_position_done:
            initial_quantity = int(
                self.config.initial_capital * 0.5 / current_price / self.config.min_trade_amount
            ) * self.config.min_trade_amount
            if initial_quantity > 0:
                self._setup_initial_position(current_price, initial_quantity, current_time)
                context.order(symbol, initial_quantity, price=current_price, reason="Initial position setup")
            self._record_bar_stats(current_price, current_time)
            self._initial_position_done = True
            self._last_processed_index = current_index
            return

        if len(hist_data) < 2:
            self._last_processed_index = current_index
            return

        prev_price = float(hist_data.iloc[-2]["close"])
        self.check_rebalance(current_price, current_time)

        signal = self.check_signal(current_price, prev_price, current_time)
        if signal and self.execute_signal(signal, current_time):
            signed_quantity = signal.quantity if signal.signal_type == SignalType.BUY else -signal.quantity
            context.order(symbol, signed_quantity, price=current_price, reason=signal.reason)

        self._record_bar_stats(current_price, current_time)
        self._last_processed_index = current_index

    def finalize_backtest_result(self, result: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Attach ETF-grid specific report fields to the unified result."""
        final_price = 0.0
        equity_curve = result.get("equity_curve")
        if equity_curve is not None and not equity_curve.empty and "close" in equity_curve.columns:
            final_price = float(equity_curve.iloc[-1]["close"] or 0.0)
        summary = self._build_backtest_summary(final_price)
        result.update({
            "summary": summary,
            "trade_history": self.trade_history,
            "daily_stats": self.daily_stats,
            "config": self.config.to_dict(),
        })
        return result

    @staticmethod
    def _format_bar_time(row, time_col: str) -> str:
        time_val = row[time_col] if time_col in row else row.get("date")
        if hasattr(time_val, "strftime"):
            return time_val.strftime("%Y-%m-%d %H:%M") if time_col == "time" else time_val.strftime("%Y-%m-%d")
        return str(time_val)

    def _record_bar_stats(self, current_price: float, current_time: str) -> None:
        self.update_unrealized_profit(current_price)
        stats = self.get_stats(current_price)
        stats["date"] = current_time
        stats["price"] = round(current_price, 3)
        self.daily_stats.append(stats)

    def _build_backtest_summary(self, final_price: float) -> Dict[str, Any]:
        final_stats = self.get_stats(final_price) if final_price > 0 else self.get_stats(self.state.last_trade_price or self.state.base_price)
        if self.daily_stats:
            returns = [s["total_return"] for s in self.daily_stats]
            max_return = max(returns)
            min_return = min(returns)

            peak = self.config.initial_capital
            max_drawdown = 0
            for stat in self.daily_stats:
                value = stat["total_value"]
                if value > peak:
                    peak = value
                drawdown = (peak - value) / peak if peak > 0 else 0
                max_drawdown = max(max_drawdown, drawdown)

            winning_trades = sum(
                1 for t in self.trade_history
                if t["type"] == "sell" and t.get("realized_profit", 0) > 0
            )
            total_sells = sum(1 for t in self.trade_history if t["type"] == "sell")
            win_rate = winning_trades / total_sells if total_sells > 0 else 0

            final_stats.update({
                "max_return": round(max_return, 2),
                "min_return": round(min_return, 2),
                "max_drawdown": round(max_drawdown * 100, 2),
                "win_rate": round(win_rate * 100, 2),
                "total_trades": len([t for t in self.trade_history if t["type"] in ["buy", "sell"]]),
                "buy_trades": len([t for t in self.trade_history if t["type"] == "buy"]),
                "sell_trades": len([t for t in self.trade_history if t["type"] == "sell"]),
                "rebalance_count": len([t for t in self.trade_history if t["type"] == "rebalance"]),
            })
        return final_stats

    def _calculate_base_price(self, data: pd.DataFrame) -> float:
        """Calculate base price from historical data."""
        if data.empty:
            return 0.0
        recent_data = data.tail(20)
        return recent_data["close"].mean()

    def _calculate_atr(self, data: pd.DataFrame, period: int = None) -> float:
        """Calculate Average True Range."""
        if period is None:
            period = self.config.atr_period

        if len(data) < period + 1:
            return 0.0

        high = data["high"].values
        low = data["low"].values
        close = data["close"].values

        tr_list = []
        for i in range(1, len(data)):
            tr = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
            tr_list.append(tr)

        if len(tr_list) >= period:
            return np.mean(tr_list[-period:])
        return np.mean(tr_list) if tr_list else 0.0

    def _generate_grids(self):
        """Generate grid levels based on configuration."""
        base_price = self.state.base_price
        grid_count = self.config.grid_count
        spacing = self.config.grid_spacing

        grids = []
        for i in range(-grid_count, grid_count + 1):
            if self.config.grid_type == GridType.GEOMETRIC:
                price = base_price * (1 + spacing) ** i
            else:
                price = base_price + i * spacing

            grids.append(GridLevel(
                price=round(price, 3),
                level=i,
                quantity=0,
                avg_cost=0.0,
                is_triggered=False,
            ))

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
        # Sort grids by price descending
        sorted_grids = sorted(self.state.grids, key=lambda x: x.price, reverse=True)
        
        # Check for buy signals (price crosses down)
        # We iterate from highest price to lowest to find the first grid crossed
        for grid in sorted_grids:
            # Condition: prev_price >= grid.price > current_price
            # Using >= for prev_price handles the case where price was exactly at grid level
            if prev_price >= grid.price > current_price:
                if not grid.is_triggered:
                    quantity = self.calculate_trade_quantity(grid.price, is_buy=True)
                    if quantity > 0:
                        return TradeSignal(
                            signal_type=SignalType.BUY,
                            price=current_price,
                            quantity=quantity,
                            grid_level=grid.level,
                            reason=f"Price crossed below grid level {grid.level} at {grid.price:.3f}"
                        )

        # Check for sell signals (price crosses up)
        # We iterate from lowest price to highest
        for grid in sorted(sorted_grids, key=lambda x: x.price):
            # Condition: prev_price <= grid.price < current_price
            if prev_price <= grid.price < current_price:
                # Sell logic: Price crosses up through Level N, sell position from Level N-1
                target_level = grid.level - 1
                target_grid = next((g for g in self.state.grids if g.level == target_level), None)
                
                if target_grid and target_grid.is_triggered and target_grid.quantity > 0:
                    return TradeSignal(
                        signal_type=SignalType.SELL,
                        price=current_price,
                        quantity=target_grid.quantity, # Sell the exact amount bought at that level
                        grid_level=grid.level, # Signal triggered at this level
                        reason=f"Price crossed above grid level {grid.level} at {grid.price:.3f}, selling level {target_level}"
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
            if signal.reason.startswith("Stop loss") or signal.reason.startswith("Take profit"):
                # For stop loss/take profit, reduce quantity from grids starting from lowest price
                remaining_qty = signal.quantity
                sorted_grids = sorted(self.state.grids, key=lambda x: x.price)
                for grid in sorted_grids:
                    if remaining_qty <= 0:
                        break
                    if grid.quantity > 0:
                        sell_qty = min(grid.quantity, remaining_qty)
                        grid.quantity -= sell_qty
                        remaining_qty -= sell_qty
                        if grid.quantity == 0:
                            grid.is_triggered = False
            else:
                # Normal grid sell: release the position from Level N-1
                # signal.grid_level is the level crossed (N), we sell N-1
                target_level = signal.grid_level - 1
                for grid in self.state.grids:
                    if grid.level == target_level:
                        sell_qty = min(grid.quantity, signal.quantity)
                        grid.quantity -= sell_qty
                        if grid.quantity == 0:
                            grid.is_triggered = False
                        break
        
        # Record trade with current grid state
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
            'realized_profit': round(self.state.realized_profit, 2),
            'base_price': round(self.state.base_price, 3),
            'grid_prices': [round(g.price, 3) for g in self.state.grids],
            'grids_snapshot': [
                {'level': g.level, 'price': round(g.price, 3), 'quantity': g.quantity}
                for g in self.state.grids
            ]
        }
        self.trade_history.append(trade_record)
        
        return True
    
    def _setup_initial_position(self, price: float, total_quantity: int, time_str: str):
        """
        Setup initial position by distributing quantity across multiple grid levels
        starting from the level below current price.
        """
        # Find nearest grid level below current price
        _, grid_below = self.find_nearest_grids(price)
        
        if grid_below:
            start_level = grid_below.level
        else:
            # If price is below all grids, use the lowest level
            if self.state.grids:
                start_level = min(g.level for g in self.state.grids)
            else:
                start_level = 0
        
        # Calculate quantity per grid based on config
        trade_value = self.config.initial_capital * self.config.position_per_grid
        qty_per_grid = int(trade_value / price / self.config.min_trade_amount) * self.config.min_trade_amount
        if qty_per_grid <= 0:
            qty_per_grid = self.config.min_trade_amount
            
        # Distribute quantity
        remaining_qty = total_quantity
        distributions = []
        
        # Sort grids by level descending (highest price first)
        sorted_grids = sorted(self.state.grids, key=lambda x: x.level, reverse=True)
        
        # Find start index
        start_idx = 0
        found = False
        for i, grid in enumerate(sorted_grids):
            if grid.level == start_level:
                start_idx = i
                found = True
                break
        
        if not found and sorted_grids:
            # Fallback: if start_level not found, find the first grid below price
            for i, grid in enumerate(sorted_grids):
                if grid.price <= price:
                    start_idx = i
                    break
            else:
                # If all grids are above price, use the last (lowest) one
                start_idx = len(sorted_grids) - 1

        # Distribute downwards from start_level
        for i in range(start_idx, len(sorted_grids)):
            if remaining_qty <= 0:
                break
                
            grid = sorted_grids[i]
            
            # Determine allocation for this grid
            if i == len(sorted_grids) - 1:
                # Last grid gets the rest
                alloc_qty = remaining_qty
            else:
                alloc_qty = min(remaining_qty, qty_per_grid)
            
            distributions.append((grid, alloc_qty))
            remaining_qty -= alloc_qty
            
        # If still remaining (e.g. ran out of grids), add to the last grid used
        if remaining_qty > 0 and distributions:
            last_grid, last_qty = distributions[-1]
            distributions[-1] = (last_grid, last_qty + remaining_qty)
            
        # Execute trade (update state)
        amount = price * total_quantity
        commission = self.calculate_commission(amount)
        
        if self.state.available_cash < amount + commission:
            # Adjust quantity if not enough cash (should have been handled before, but safety check)
            # For initial setup, we assume the quantity passed is valid or we force it?
            # Let's just proceed, assuming caller calculated correctly.
            pass
        
        self.state.available_cash -= (amount + commission)
        self.state.current_position += total_quantity
        self.state.total_cost += amount
        self.state.trade_count += 1
        self.state.last_trade_price = price
        
        # Update grids
        for grid, qty in distributions:
            grid.quantity += qty
            grid.is_triggered = True
            
        # Record trade
        trade_record = {
            'date': time_str,
            'type': 'buy',
            'price': round(price, 3),
            'quantity': total_quantity,
            'amount': round(amount, 2),
            'commission': round(commission, 2),
            'grid_level': start_level,
            'reason': f"Initial position setup (Distributed from Lv{start_level} down)",
            'position_after': self.state.current_position,
            'cash_after': round(self.state.available_cash, 2),
            'realized_profit': round(self.state.realized_profit, 2),
            'base_price': round(self.state.base_price, 3),
            'grid_prices': [round(g.price, 3) for g in self.state.grids],
            'grids_snapshot': [
                {'level': g.level, 'price': round(g.price, 3), 'quantity': g.quantity}
                for g in self.state.grids
            ]
        }
        self.trade_history.append(trade_record)

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
            old_position = self.state.current_position
            
            self.state.base_price = current_price
            self._generate_grids()
            self.state.last_rebalance_date = current_date
            
            # Map existing position to the new base grid (Level 0)
            # This ensures that existing positions can be sold when price rises above Level 1
            if old_position > 0:
                base_grid = next((g for g in self.state.grids if g.level == 0), None)
                if base_grid:
                    base_grid.quantity = old_position
                    base_grid.is_triggered = True
            
            # Record rebalance event with new grid state
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
                'realized_profit': self.state.realized_profit,
                'base_price': round(self.state.base_price, 3),
                'grid_prices': [round(g.price, 3) for g in self.state.grids],
                'grids_snapshot': [
                    {'level': g.level, 'price': round(g.price, 3), 'quantity': g.quantity}
                    for g in self.state.grids
                ]
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
        Run backtest through UnifiedBacktestEngine and return the legacy ETF grid report shape.
        """
        if data.empty or len(data) < 2:
            return {'error': 'Insufficient data for backtest'}

        if progress_callback:
            progress_callback(0, len(data))

        from strategy_app.backtest import (
            BacktestConfig,
            FeeModelConfig,
            OrderMatcher,
            OrderMatcherConfig,
            SimulationBroker,
            UnifiedBacktestEngine,
        )

        broker = SimulationBroker(
            matcher=OrderMatcher(OrderMatcherConfig(
                slippage_pct=self.config.slippage,
                min_lot=self.config.min_trade_amount,
                enforce_volume_limit=False,
                enforce_bar_price_range=False,
                enforce_price_limit=False,
            )),
            fee_config=FeeModelConfig(
                buy_commission_rate=self.config.commission_rate,
                sell_commission_rate=self.config.commission_rate,
                min_commission=self.config.min_commission,
            ),
        )
        engine = UnifiedBacktestEngine(
            BacktestConfig(initial_cash=self.config.initial_capital, mode="bar"),
            broker=broker,
        )
        result = engine.run(self, data, code="ETF_GRID", mode="bar")

        if progress_callback:
            progress_callback(len(data), len(data))

        return {
            "summary": result.get("summary", {}),
            "trade_history": result.get("trade_history", []),
            "daily_stats": result.get("daily_stats", []),
            "config": result.get("config", self.config.to_dict()),
            "unified_result": result,
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
