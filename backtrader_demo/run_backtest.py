"""
Backtrader Demo - Main Runner
==============================
Main script to run backtrader backtests with project data.

Usage:
    python run_backtest.py                          # Run with defaults
    python run_backtest.py -s 000001.SZ             # Specify stock
    python run_backtest.py -s 000001.SZ -t rsi      # Specify strategy
    python run_backtest.py --list-strategies        # List available strategies
    python run_backtest.py --list-stocks            # List available stocks
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

import backtrader as bt

# Import local modules
from data_loader import (
    create_bt_data_feed,
    get_available_stocks,
    load_stock_list,
    PROJECT_ROOT,
)
from strategies import STRATEGIES


def run_backtest(
    stock_code: str,
    strategy_name: str = "double_ma",
    start_date: str = None,
    end_date: str = None,
    initial_cash: float = 100000.0,
    commission: float = 0.001,
    data_dir: str = None,
    plot: bool = True,
    strategy_params: dict = None,
):
    """
    Run a backtest for a given stock and strategy.
    
    Args:
        stock_code: Stock code (e.g., '000001.SZ')
        strategy_name: Name of strategy ('double_ma', 'rsi', 'macd', 'bollinger')
        start_date: Backtest start date (YYYY-MM-DD)
        end_date: Backtest end date (YYYY-MM-DD)
        initial_cash: Initial portfolio cash
        commission: Trading commission rate
        data_dir: Path to data directory
        plot: Whether to show chart
        strategy_params: Additional strategy parameters
    
    Returns:
        dict: Backtest results
    """
    print("=" * 60)
    print("Backtrader Demo - Backtest Runner")
    print("=" * 60)
    
    # Get strategy class
    if strategy_name not in STRATEGIES:
        print(f"[ERROR] Unknown strategy: {strategy_name}")
        print(f"Available strategies: {list(STRATEGIES.keys())}")
        return None
    
    strategy_class = STRATEGIES[strategy_name]
    
    # Create Cerebro engine
    cerebro = bt.Cerebro()
    
    # Add strategy
    if strategy_params:
        cerebro.addstrategy(strategy_class, **strategy_params)
    else:
        cerebro.addstrategy(strategy_class)
    
    # Load data
    print(f"\n[INFO] Loading data for {stock_code}...")
    data = create_bt_data_feed(
        stock_code,
        data_dir=data_dir,
        start_date=start_date,
        end_date=end_date,
    )
    
    if data is None:
        print(f"[ERROR] Failed to load data for {stock_code}")
        return None
    
    cerebro.adddata(data, name=stock_code)
    
    # Set initial cash
    cerebro.broker.setcash(initial_cash)
    
    # Set commission - Chinese A-share market style
    # Commission: 0.1% (can be negotiated), minimum 5 yuan
    # Stamp duty: 0.1% (sell only) - not implemented in this simple demo
    cerebro.broker.setcommission(commission=commission)
    
    # Add analyzers for performance metrics
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.03)
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    
    # Print initial portfolio value
    print(f"\n[INFO] Initial Portfolio Value: {cerebro.broker.getvalue():,.2f}")
    print(f"[INFO] Strategy: {strategy_name} ({strategy_class.__name__})")
    print(f"[INFO] Commission: {commission * 100:.2f}%")
    
    # Run backtest
    print("\n[INFO] Running backtest...")
    print("-" * 60)
    
    results = cerebro.run()
    strategy_result = results[0]
    
    # Print final results
    print("-" * 60)
    final_value = cerebro.broker.getvalue()
    print(f"\n[RESULT] Final Portfolio Value: {final_value:,.2f}")
    print(f"[RESULT] Total Return: {((final_value / initial_cash) - 1) * 100:.2f}%")
    
    # Get analyzer results
    try:
        sharpe = strategy_result.analyzers.sharpe.get_analysis()
        sharpe_ratio = sharpe.get("sharperatio", None)
        if sharpe_ratio is not None:
            print(f"[RESULT] Sharpe Ratio: {sharpe_ratio:.3f}")
    except:
        pass
    
    try:
        drawdown = strategy_result.analyzers.drawdown.get_analysis()
        max_dd = drawdown.get("max", {}).get("drawdown", 0)
        print(f"[RESULT] Max Drawdown: {max_dd:.2f}%")
    except:
        pass
    
    try:
        trades = strategy_result.analyzers.trades.get_analysis()
        total_trades = trades.get("total", {}).get("total", 0)
        won = trades.get("won", {}).get("total", 0)
        lost = trades.get("lost", {}).get("total", 0)
        print(f"[RESULT] Total Trades: {total_trades} (Won: {won}, Lost: {lost})")
        if total_trades > 0:
            win_rate = won / total_trades * 100
            print(f"[RESULT] Win Rate: {win_rate:.1f}%")
    except:
        pass
    
    print("=" * 60)
    
    # Plot if requested
    if plot:
        try:
            cerebro.plot(
                style="candlestick",
                barup="red",     # Chinese stock market convention
                bardown="green", # Green for down in China
                volup="red",
                voldown="green",
            )
        except Exception as e:
            print(f"[WARNING] Could not generate plot: {e}")
    
    return {
        "final_value": final_value,
        "return": (final_value / initial_cash) - 1,
        "sharpe_ratio": sharpe_ratio if "sharpe_ratio" in dir() else None,
        "max_drawdown": max_dd if "max_dd" in dir() else None,
    }


def main():
    """Main entry point with argument parsing"""
    parser = argparse.ArgumentParser(
        description="Backtrader Demo - Run backtests using project data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -s 000001.SZ                    # Backtest 平安银行 with default strategy
  %(prog)s -s 600519.SH -t macd            # Backtest 贵州茅台 with MACD strategy
  %(prog)s -s 000858.SZ -t rsi --no-plot   # No chart output
  %(prog)s --list-strategies               # Show available strategies
  %(prog)s --list-stocks                   # Show available stocks
        """
    )
    
    parser.add_argument(
        "-s", "--stock",
        type=str,
        default="000001.SZ",
        help="Stock code (default: 000001.SZ)"
    )
    
    parser.add_argument(
        "-t", "--strategy",
        type=str,
        default="double_ma",
        choices=list(STRATEGIES.keys()),
        help="Strategy name (default: double_ma)"
    )
    
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Start date (YYYY-MM-DD)"
    )
    
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date (YYYY-MM-DD)"
    )
    
    parser.add_argument(
        "--cash",
        type=float,
        default=100000.0,
        help="Initial cash (default: 100000)"
    )
    
    parser.add_argument(
        "--commission",
        type=float,
        default=0.001,
        help="Commission rate (default: 0.001 = 0.1%%)"
    )
    
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Data directory path"
    )
    
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Disable chart plotting"
    )
    
    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="List available strategies and exit"
    )
    
    parser.add_argument(
        "--list-stocks",
        action="store_true",
        help="List available stocks and exit"
    )
    
    args = parser.parse_args()
    
    # List strategies
    if args.list_strategies:
        print("\nAvailable Strategies:")
        print("-" * 40)
        for name, cls in STRATEGIES.items():
            print(f"  {name:15} - {cls.__doc__.split(chr(10))[1].strip()}")
        return
    
    # List stocks
    if args.list_stocks:
        stocks = get_available_stocks(args.data_dir)
        print(f"\nAvailable Stocks ({len(stocks)} total):")
        print("-" * 40)
        for i, code in enumerate(stocks[:50]):  # Show first 50
            print(f"  {code}", end="")
            if (i + 1) % 5 == 0:
                print()
        if len(stocks) > 50:
            print(f"\n  ... and {len(stocks) - 50} more")
        return
    
    # Run backtest
    run_backtest(
        stock_code=args.stock,
        strategy_name=args.strategy,
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.cash,
        commission=args.commission,
        data_dir=args.data_dir,
        plot=not args.no_plot,
    )


if __name__ == "__main__":
    main()
