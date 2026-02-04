"""
Sample Strategies for Backtrader Demo
======================================
Contains various example strategies using backtrader's built-in indicators.
"""

import backtrader as bt


class BaseStrategy(bt.Strategy):
    """Base strategy class with common position sizing logic"""
    
    def get_size(self):
        """Calculate the number of shares to buy based on available cash and position percentage"""
        # Get position percentage from params, default to 90%
        position_pct = getattr(self.params, 'position_pct', 0.9)
        
        # Calculate available cash
        cash = self.broker.getcash()
        
        # Get current price
        price = self.data.close[0]
        
        if price <= 0:
            return 0
        
        # Calculate max shares we can buy with position_pct of cash
        # In China A-shares, must buy in lots of 100
        max_shares = int((cash * position_pct) / price)
        
        # Round down to nearest 100 (China A-share lot size)
        lot_size = 100
        size = (max_shares // lot_size) * lot_size
        
        return max(size, lot_size)  # At least buy 1 lot (100 shares)


class DoubleMACrossStrategy(BaseStrategy):
    """
    Double Moving Average Crossover Strategy
    -----------------------------------------
    Classic momentum strategy using two moving averages:
    - Buy when fast MA crosses above slow MA (golden cross)
    - Sell when fast MA crosses below slow MA (death cross)
    """
    
    params = (
        ("fast_period", 5),    # Fast moving average period
        ("slow_period", 20),   # Slow moving average period
        ("position_pct", 0.9), # Percentage of cash to use for each trade
        ("printlog", True),    # Print trade logs
    )
    
    def __init__(self):
        # Calculate moving averages
        self.fast_ma = bt.indicators.SMA(
            self.data.close, period=self.params.fast_period
        )
        self.slow_ma = bt.indicators.SMA(
            self.data.close, period=self.params.slow_period
        )
        
        # Create crossover signal
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
        
        # Track order status
        self.order = None
        self.buy_price = None
        self.buy_comm = None
    
    def log(self, txt, dt=None):
        """Logging function for this strategy"""
        if self.params.printlog:
            dt = dt or self.datas[0].datetime.date(0)
            print(f"[{dt.isoformat()}] {txt}")
    
    def notify_order(self, order):
        """Callback when order status changes"""
        if order.status in [order.Submitted, order.Accepted]:
            return
        
        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(
                    f"BUY EXECUTED, Price: {order.executed.price:.2f}, "
                    f"Cost: {order.executed.value:.2f}, "
                    f"Comm: {order.executed.comm:.2f}"
                )
                self.buy_price = order.executed.price
                self.buy_comm = order.executed.comm
            else:
                self.log(
                    f"SELL EXECUTED, Price: {order.executed.price:.2f}, "
                    f"Cost: {order.executed.value:.2f}, "
                    f"Comm: {order.executed.comm:.2f}"
                )
            self.bar_executed = len(self)
        
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log("Order Canceled/Margin/Rejected")
        
        self.order = None
    
    def notify_trade(self, trade):
        """Callback when trade is closed"""
        if not trade.isclosed:
            return
        
        self.log(f"TRADE PROFIT, Gross: {trade.pnl:.2f}, Net: {trade.pnlcomm:.2f}")
    
    def next(self):
        """Called on each bar"""
        # Check if there's a pending order
        if self.order:
            return
        
        # Check if we are in the market
        if not self.position:
            # Not in market, look for buy signal
            if self.crossover > 0:  # Golden cross
                size = self.get_size()
                self.log(f"BUY CREATE, {self.data.close[0]:.2f}, Size: {size}")
                self.order = self.buy(size=size)
        else:
            # In market, look for sell signal
            if self.crossover < 0:  # Death cross
                self.log(f"SELL CREATE, {self.data.close[0]:.2f}")
                self.order = self.close()


class RSIMeanReversionStrategy(BaseStrategy):
    """
    RSI Mean Reversion Strategy
    ----------------------------
    Buys when RSI indicates oversold, sells when RSI indicates overbought.
    - Buy when RSI < oversold_level
    - Sell when RSI > overbought_level
    """
    
    params = (
        ("rsi_period", 14),        # RSI calculation period
        ("oversold_level", 30),    # RSI level for buy signal
        ("overbought_level", 70),  # RSI level for sell signal
        ("position_pct", 0.9),     # Percentage of cash to use for each trade
        ("printlog", True),
    )
    
    def __init__(self):
        self.rsi = bt.indicators.RSI(
            self.data.close, period=self.params.rsi_period
        )
        self.order = None
    
    def log(self, txt, dt=None):
        if self.params.printlog:
            dt = dt or self.datas[0].datetime.date(0)
            print(f"[{dt.isoformat()}] {txt}")
    
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        
        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(
                    f"BUY EXECUTED, Price: {order.executed.price:.2f}, "
                    f"RSI: {self.rsi[0]:.2f}"
                )
            else:
                self.log(
                    f"SELL EXECUTED, Price: {order.executed.price:.2f}, "
                    f"RSI: {self.rsi[0]:.2f}"
                )
        
        self.order = None
    
    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.log(f"TRADE PROFIT, Gross: {trade.pnl:.2f}, Net: {trade.pnlcomm:.2f}")
    
    def next(self):
        if self.order:
            return
        
        if not self.position:
            if self.rsi[0] < self.params.oversold_level:
                size = self.get_size()
                self.log(f"BUY CREATE (RSI={self.rsi[0]:.2f}), Price: {self.data.close[0]:.2f}, Size: {size}")
                self.order = self.buy(size=size)
        else:
            if self.rsi[0] > self.params.overbought_level:
                self.log(f"SELL CREATE (RSI={self.rsi[0]:.2f}), Price: {self.data.close[0]:.2f}")
                self.order = self.close()


class MACDStrategy(BaseStrategy):
    """
    MACD Strategy
    -------------
    Uses MACD indicator for trading signals:
    - Buy when MACD line crosses above signal line
    - Sell when MACD line crosses below signal line
    """
    
    params = (
        ("fast_ema", 12),
        ("slow_ema", 26),
        ("signal_period", 9),
        ("position_pct", 0.9),  # Percentage of cash to use for each trade
        ("printlog", True),
    )
    
    def __init__(self):
        self.macd = bt.indicators.MACD(
            self.data.close,
            period_me1=self.params.fast_ema,
            period_me2=self.params.slow_ema,
            period_signal=self.params.signal_period,
        )
        
        # Crossover of MACD line and signal line
        self.crossover = bt.indicators.CrossOver(
            self.macd.macd, self.macd.signal
        )
        
        self.order = None
    
    def log(self, txt, dt=None):
        if self.params.printlog:
            dt = dt or self.datas[0].datetime.date(0)
            print(f"[{dt.isoformat()}] {txt}")
    
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        
        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(f"BUY EXECUTED, Price: {order.executed.price:.2f}")
            else:
                self.log(f"SELL EXECUTED, Price: {order.executed.price:.2f}")
        
        self.order = None
    
    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.log(f"TRADE PROFIT, Gross: {trade.pnl:.2f}, Net: {trade.pnlcomm:.2f}")
    
    def next(self):
        if self.order:
            return
        
        if not self.position:
            if self.crossover > 0:
                size = self.get_size()
                self.log(f"BUY CREATE, Price: {self.data.close[0]:.2f}, Size: {size}")
                self.order = self.buy(size=size)
        else:
            if self.crossover < 0:
                self.log(f"SELL CREATE, Price: {self.data.close[0]:.2f}")
                self.order = self.close()


class BollingerBandsStrategy(BaseStrategy):
    """
    Bollinger Bands Strategy
    -------------------------
    Uses Bollinger Bands for mean reversion trading:
    - Buy when price touches lower band
    - Sell when price touches upper band
    """
    
    params = (
        ("period", 20),       # Bollinger Bands period
        ("devfactor", 2.0),   # Standard deviation factor
        ("position_pct", 0.9),  # Percentage of cash to use for each trade
        ("printlog", True),
    )
    
    def __init__(self):
        self.bband = bt.indicators.BollingerBands(
            self.data.close,
            period=self.params.period,
            devfactor=self.params.devfactor,
        )
        self.order = None
    
    def log(self, txt, dt=None):
        if self.params.printlog:
            dt = dt or self.datas[0].datetime.date(0)
            print(f"[{dt.isoformat()}] {txt}")
    
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        
        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(f"BUY EXECUTED, Price: {order.executed.price:.2f}")
            else:
                self.log(f"SELL EXECUTED, Price: {order.executed.price:.2f}")
        
        self.order = None
    
    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.log(f"TRADE PROFIT, Gross: {trade.pnl:.2f}, Net: {trade.pnlcomm:.2f}")
    
    def next(self):
        if self.order:
            return
        
        if not self.position:
            # Buy when price touches lower band
            if self.data.close[0] <= self.bband.lines.bot[0]:
                size = self.get_size()
                self.log(f"BUY CREATE (touch lower band), Price: {self.data.close[0]:.2f}, Size: {size}")
                self.order = self.buy(size=size)
        else:
            # Sell when price touches upper band
            if self.data.close[0] >= self.bband.lines.top[0]:
                self.log(f"SELL CREATE (touch upper band), Price: {self.data.close[0]:.2f}")
                self.order = self.close()


# Dictionary mapping strategy names to classes for easy selection
STRATEGIES = {
    "double_ma": DoubleMACrossStrategy,
    "rsi": RSIMeanReversionStrategy,
    "macd": MACDStrategy,
    "bollinger": BollingerBandsStrategy,
}
