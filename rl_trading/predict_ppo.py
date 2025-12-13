import os
import sys
import gymnasium as gym
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl_trading.envs.stock_trading_env import StockTradingEnv

import argparse

# 尝试导入 MaskablePPO
try:
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.wrappers import ActionMasker
    HAS_MASKABLE_PPO = True
except ImportError:
    from stable_baselines3 import PPO
    HAS_MASKABLE_PPO = False


def get_action_masks(env):
    """获取环境的 action masks"""
    return env.action_masks()


def predict():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock_code", type=str, default="000001", help="Stock code to predict")
    parser.add_argument("--buy_rate", type=float, default=0.0001, help="Buy commission rate")
    parser.add_argument("--buy_min", type=float, default=5.0, help="Buy commission minimum")
    parser.add_argument("--sell_rate", type=float, default=0.0001, help="Sell commission rate")
    parser.add_argument("--sell_min", type=float, default=5.0, help="Sell commission minimum")
    parser.add_argument("--stamp_duty", type=float, default=0.0005, help="Stamp duty rate")
    parser.add_argument("--trade_percent", type=float, default=0.5, help="Trade amount percent")
    parser.add_argument("--deterministic", action="store_true", default=True, help="Use deterministic actions")
    args = parser.parse_args()

    # Configuration
    STOCK_CODE = args.stock_code
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    model_path = os.path.join(MODELS_DIR, f"ppo_stock_{STOCK_CODE}.zip")
    
    # Create output directory if not exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    if not os.path.exists(model_path):
        print(f"Model not found at {model_path}. Please train first.")
        return

    print(f"Loading model from {model_path}...")
    
    # 创建环境
    env = StockTradingEnv(
        stock_code=STOCK_CODE, 
        data_dir=DATA_DIR, 
        render_mode='human',
        buy_rate=args.buy_rate,
        buy_min=args.buy_min,
        sell_rate=args.sell_rate,
        sell_min=args.sell_min,
        stamp_duty=args.stamp_duty,
        trade_amount_percent=args.trade_percent
    )
    
    # 加载模型 - 自动检测模型类型
    try:
        if HAS_MASKABLE_PPO:
            model = MaskablePPO.load(model_path)
            use_action_mask = True
            print("[OK] Using MaskablePPO for prediction")
        else:
            raise ImportError()
    except:
        from stable_baselines3 import PPO
        model = PPO.load(model_path)
        use_action_mask = False
        print("[WARN] Using standard PPO for prediction (no Action Masking)")
    
    # 运行预测
    obs, info = env.reset()
    done = False
    truncated = False
    
    net_worths = []
    dates = []
    actions_taken = []
    
    print("Starting prediction...")
    print(f"Initial Balance: {env.initial_balance}")
    print("-" * 60)
    
    step_counter = 0
    buy50_actions = 0
    buy100_actions = 0
    sell50_actions = 0
    sell100_actions = 0
    hold_actions = 0
    invalid_buys = 0
    invalid_sells = 0
    
    while not done and not truncated:
        # 获取动作
        if use_action_mask:
            action_mask = env.action_masks()
            action, _states = model.predict(obs, deterministic=args.deterministic, action_masks=action_mask)
        else:
            action, _states = model.predict(obs, deterministic=args.deterministic)
        
        original_action = int(action)
        obs, reward, done, truncated, info = env.step(action)
        
        step_counter += 1
        
        # 统计动作
        if original_action == 0:
            hold_actions += 1
        elif original_action == 1:
            buy50_actions += 1
            if info.get("invalid_action", False) and info.get("original_action") == 1:
                invalid_buys += 1
        elif original_action == 2:
            buy100_actions += 1
            if info.get("invalid_action", False) and info.get("original_action") == 2:
                invalid_buys += 1
        elif original_action == 3:
            sell50_actions += 1
            if info.get("invalid_action", False) and info.get("original_action") == 3:
                invalid_sells += 1
        elif original_action == 4:
            sell100_actions += 1
            if info.get("invalid_action", False) and info.get("original_action") == 4:
                invalid_sells += 1
        
        net_worths.append(info['net_worth'])
        dates.append(info['date'])
        actions_taken.append({
            'date': info['date'],
            'action': original_action,
            'executed': info.get('trade_happened', False),
            'net_worth': info['net_worth'],
            'shares': info['shares_held'],
            'balance': info['balance']
        })
        
        # 打印进度
        if env.current_step % 100 == 0:
            action_str = ['Hold', 'Buy50%', 'Buy100%', 'Sell50%', 'Sell100%'][info.get('action', 0)]
            traded = "[OK]" if info.get('trade_happened', False) else ""
            print(f"Date: {info['date']}, Net Worth: {info['net_worth']:.2f}, "
                  f"Shares: {info['shares_held']}, Balance: {info['balance']:.2f}, "
                  f"Action: {action_str} {traded}")
    
    print("-" * 60)
    print(f"Prediction finished. Total Steps: {step_counter}")
    
    # 统计实际交易
    real_buys = len([t for t in env.trades if t['type'] == 'buy'])
    real_sells = len([t for t in env.trades if t['type'] == 'sell'])
    
    print(f"\n{'='*60}")
    print("Trade Statistics")
    print(f"{'='*60}")
    print(f"Model Actions: Buy50%: {buy50_actions}, Buy100%: {buy100_actions}, "
          f"Sell50%: {sell50_actions}, Sell100%: {sell100_actions}, Hold: {hold_actions}")
    print(f"Executed Trades: Buy: {real_buys}, Sell: {real_sells}")
    
    total_buy_actions = buy50_actions + buy100_actions
    total_sell_actions = sell50_actions + sell100_actions
    if not use_action_mask:
        print(f"Invalid 'Buy' Actions: {invalid_buys} (insufficient funds)")
        print(f"Invalid 'Sell' Actions: {invalid_sells} (no position)")
        invalid_rate = (invalid_buys + invalid_sells) / step_counter * 100 if step_counter > 0 else 0
        print(f"Invalid Action Rate: {invalid_rate:.1f}%")
    else:
        print("[OK] Action Masking enabled, all actions are valid")
    
    print(f"\n{'='*60}")
    print("Performance Summary")
    print(f"{'='*60}")
    print(f"Initial Balance: {env.initial_balance:.2f}")
    print(f"Final Net Worth: {info['net_worth']:.2f}")
    profit = info['net_worth'] - env.initial_balance
    profit_pct = profit / env.initial_balance * 100
    print(f"Profit/Loss: {profit:.2f} ({profit_pct:+.2f}%)")
    
    # Calculate max drawdown
    peak = net_worths[0]
    max_drawdown = 0
    for nw in net_worths:
        if nw > peak:
            peak = nw
        drawdown = (peak - nw) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    print(f"Max Drawdown: {max_drawdown * 100:.2f}%")
    
    # Calculate Sharpe Ratio (simplified)
    if len(net_worths) > 1:
        returns = np.diff(net_worths) / net_worths[:-1]
        if np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)  # Annualized
            print(f"Sharpe Ratio (Annualized): {sharpe:.2f}")
    
    print(f"{'='*60}\n")
    
    # Print trade details
    if len(env.trades) > 0:
        print("Trade Details:")
        print("-" * 80)
        for i, trade in enumerate(env.trades[:20]):  # Show first 20 trades
            print(f"{i+1}. {trade['date']} | {trade['type'].upper():4s} | "
                  f"Price: {trade['price']:.2f} | Shares: {trade['shares']} | Fee: {trade['fee']:.2f}")
        if len(env.trades) > 20:
            print(f"... Total {len(env.trades)} trades")
        print("-" * 80)
    
    # 绘图
    try:
        # 获取股票价格数据用于绘制K线
        price_data = env.df.copy()
        # 只取预测期间的数据
        price_dates = price_data['date'].tolist()
        start_idx = price_dates.index(dates[0]) if dates[0] in price_dates else 0
        end_idx = price_dates.index(dates[-1]) + 1 if dates[-1] in price_dates else len(price_dates)
        price_data = price_data.iloc[start_idx:end_idx]
        
        fig, axes = plt.subplots(3, 1, figsize=(14, 14), gridspec_kw={'height_ratios': [2, 2, 1]})
        
        # ===== 图1: K线图 + 买卖点 =====
        ax1 = axes[0]
        plot_dates = price_data['date'].tolist()
        closes = price_data['close'].tolist()
        opens = price_data['open'].tolist()
        highs = price_data['high'].tolist()
        lows = price_data['low'].tolist()
        
        # 绘制K线（简化版：用收盘价线 + 涨跌颜色）
        ax1.plot(plot_dates, closes, color='#1976D2', linewidth=1, label='Close Price', alpha=0.8)
        
        # 填充涨跌区域
        for i in range(1, len(closes)):
            if closes[i] >= closes[i-1]:
                ax1.fill_between(plot_dates[i-1:i+1], lows[i-1:i+1], highs[i-1:i+1], 
                                color='red', alpha=0.15)
            else:
                ax1.fill_between(plot_dates[i-1:i+1], lows[i-1:i+1], highs[i-1:i+1], 
                                color='green', alpha=0.15)
        
        # 标记买卖点
        buy_trades = [t for t in env.trades if t['type'] == 'buy']
        sell_trades = [t for t in env.trades if t['type'] == 'sell']
        
        for trade in buy_trades:
            if trade['date'] in plot_dates:
                ax1.scatter([trade['date']], [trade['price']], color='red', marker='^', s=80, zorder=5, label='_nolegend_')
                ax1.annotate('B', (trade['date'], trade['price']), textcoords="offset points", 
                           xytext=(0, 10), ha='center', fontsize=8, color='red', fontweight='bold')
        
        for trade in sell_trades:
            if trade['date'] in plot_dates:
                ax1.scatter([trade['date']], [trade['price']], color='green', marker='v', s=80, zorder=5, label='_nolegend_')
                ax1.annotate('S', (trade['date'], trade['price']), textcoords="offset points", 
                           xytext=(0, -15), ha='center', fontsize=8, color='green', fontweight='bold')
        
        ax1.set_title(f'Stock Price & Trading Signals - {STOCK_CODE}', fontsize=12)
        ax1.set_ylabel('Price')
        ax1.legend(loc='upper left')
        ax1.grid(True, alpha=0.3)
        
        # 添加成交量副图（如果有）
        if 'volume' in price_data.columns:
            ax1_vol = ax1.twinx()
            volumes = price_data['volume'].tolist()
            colors = ['red' if closes[i] >= opens[i] else 'green' for i in range(len(closes))]
            ax1_vol.bar(plot_dates, volumes, color=colors, alpha=0.3, width=0.8)
            ax1_vol.set_ylabel('Volume', color='gray')
            ax1_vol.tick_params(axis='y', labelcolor='gray')
            ax1_vol.set_ylim(0, max(volumes) * 3)  # 压缩成交量显示高度
        
        # ===== 图2: 净值曲线 =====
        ax2 = axes[1]
        ax2.plot(dates, net_worths, label='Net Worth', color='#1565C0', linewidth=1.5)
        ax2.axhline(y=env.initial_balance, color='gray', linestyle='--', label='Initial Balance', alpha=0.7)
        ax2.fill_between(dates, env.initial_balance, net_worths, 
                         where=[nw >= env.initial_balance for nw in net_worths],
                         color='green', alpha=0.3)
        ax2.fill_between(dates, env.initial_balance, net_worths,
                         where=[nw < env.initial_balance for nw in net_worths],
                         color='red', alpha=0.3)
        ax2.set_title(f'AI Trading Performance - {STOCK_CODE}', fontsize=12)
        ax2.set_ylabel('Net Worth')
        ax2.legend(loc='upper left')
        ax2.grid(True, alpha=0.3)
        
        # 添加收益率标注
        final_return = (net_worths[-1] - env.initial_balance) / env.initial_balance * 100
        ax2.text(0.98, 0.95, f'Return: {final_return:+.1f}%\nMax DD: {max_drawdown*100:.1f}%', 
                transform=ax2.transAxes, fontsize=10, verticalalignment='top', horizontalalignment='right',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        
        # ===== 图3: 回撤图 =====
        ax3 = axes[2]
        peaks = np.maximum.accumulate(net_worths)
        drawdowns = (peaks - net_worths) / peaks * 100
        ax3.fill_between(dates, 0, drawdowns, color='#E53935', alpha=0.6)
        ax3.set_title('Drawdown (回撤)', fontsize=12)
        ax3.set_xlabel('Date')
        ax3.set_ylabel('Drawdown (%)')
        ax3.grid(True, alpha=0.3)
        ax3.set_ylim(0, max(drawdowns) * 1.1 if max(drawdowns) > 0 else 10)
        
        plt.tight_layout()
        
        output_plot = os.path.join(OUTPUT_DIR, f"prediction_plot_{STOCK_CODE}.png")
        plt.savefig(output_plot, dpi=150, bbox_inches='tight')
        print(f"Performance plot saved to {output_plot}")
        plt.close()
        
    except Exception as e:
        print(f"Could not save plot: {e}")
    
    # 保存交易记录
    try:
        trades_df = pd.DataFrame(env.trades)
        trades_path = os.path.join(OUTPUT_DIR, f"trades_{STOCK_CODE}.csv")
        trades_df.to_csv(trades_path, index=False)
        print(f"Trades saved to {trades_path}")
    except Exception as e:
        print(f"Could not save trades: {e}")


if __name__ == "__main__":
    predict()
