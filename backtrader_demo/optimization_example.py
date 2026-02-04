"""
Backtrader Demo - Parameter Optimization Example
=================================================
Demonstrates backtrader's built-in optimization capabilities.
"""

import sys
from pathlib import Path
from datetime import datetime

import backtrader as bt
import pandas as pd

from data_loader import create_bt_data_feed, get_available_stocks
from strategies import DoubleMACrossStrategy


def run_optimization(
    stock_code: str = "000001.SZ",
    start_date: str = "2020-01-01",
    end_date: str = None,
    initial_cash: float = 100000.0,
):
    """
    Run parameter optimization for Double MA strategy.
    
    Args:
        stock_code: Stock code to optimize on
        start_date: Backtest start date
        end_date: Backtest end date
        initial_cash: Initial portfolio cash
    """
    print("=" * 60)
    print("Backtrader Demo - Parameter Optimization")
    print("=" * 60)
    print(f"Stock: {stock_code}")
    print(f"Date Range: {start_date} to {end_date or 'now'}")
    print()
    
    # Create Cerebro
    cerebro = bt.Cerebro(optreturn=False)
    
    # Add strategy with parameter ranges to optimize
    # This will test all combinations of fast_period and slow_period
    cerebro.optstrategy(
        DoubleMACrossStrategy,
        fast_period=range(3, 15, 2),     # 3, 5, 7, 9, 11, 13
        slow_period=range(15, 35, 5),    # 15, 20, 25, 30
        printlog=False,  # Disable logging during optimization
    )
    
    # Load data
    print(f"Loading data for {stock_code}...")
    data = create_bt_data_feed(
        stock_code,
        start_date=start_date,
        end_date=end_date,
    )
    
    if data is None:
        print(f"Error: Failed to load data for {stock_code}")
        return None
    
    cerebro.adddata(data, name=stock_code)
    
    # Set broker
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=0.001)
    
    # Add analyzers
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    
    print("Running optimization...")
    print("Testing parameter combinations:")
    print("  fast_period: [3, 5, 7, 9, 11, 13]")
    print("  slow_period: [15, 20, 25, 30]")
    print()
    
    # Run optimization
    results = cerebro.run()
    
    # Collect results
    optimization_results = []
    
    for strategy_result in results:
        # Get strategy parameters
        params = strategy_result[0].params
        fast = params.fast_period
        slow = params.slow_period
        
        # Get final value
        final_value = strategy_result[0].broker.getvalue()
        ret = (final_value / initial_cash - 1) * 100
        
        # Get Sharpe ratio
        try:
            sharpe = strategy_result[0].analyzers.sharpe.get_analysis()
            sharpe_ratio = sharpe.get("sharperatio", None)
        except:
            sharpe_ratio = None
        
        optimization_results.append({
            "fast_period": fast,
            "slow_period": slow,
            "final_value": final_value,
            "return_pct": ret,
            "sharpe_ratio": sharpe_ratio,
        })
    
    # Convert to DataFrame and sort by return
    df = pd.DataFrame(optimization_results)
    df = df.sort_values("return_pct", ascending=False)
    
    print("=" * 60)
    print("Optimization Results (Top 10 by Return)")
    print("=" * 60)
    print(df.head(10).to_string(index=False))
    
    print("\n")
    print("=" * 60)
    print("Best Parameters:")
    print("=" * 60)
    best = df.iloc[0]
    print(f"  Fast Period: {int(best['fast_period'])}")
    print(f"  Slow Period: {int(best['slow_period'])}")
    print(f"  Return: {best['return_pct']:.2f}%")
    if best['sharpe_ratio'] is not None:
        print(f"  Sharpe Ratio: {best['sharpe_ratio']:.3f}")
    print("=" * 60)
    
    return df


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run parameter optimization")
    parser.add_argument("-s", "--stock", default="000001.SZ", help="Stock code")
    parser.add_argument("--start", default="2020-01-01", help="Start date")
    parser.add_argument("--end", default=None, help="End date")
    parser.add_argument("--cash", type=float, default=100000, help="Initial cash")
    
    args = parser.parse_args()
    
    run_optimization(
        stock_code=args.stock,
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.cash,
    )


if __name__ == "__main__":
    main()
