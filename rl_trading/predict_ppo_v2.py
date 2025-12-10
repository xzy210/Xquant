"""
PPO 预测脚本 V2 - 优化版本

配合 StockTradingEnvV2 使用
"""
import os
import sys
import gymnasium as gym
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl_trading.envs.stock_trading_env_v2 import StockTradingEnvV2

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


# 动作名称映射
ACTION_NAMES = {
    0: "Hold",
    1: "Buy 25%",
    2: "Buy 50%",
    3: "Buy 100%",
    4: "Sell 25%",
    5: "Sell 50%",
    6: "Sell 100%",
}


def predict():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock_code", type=str, default="000001", help="Stock code to predict")
    parser.add_argument("--buy_rate", type=float, default=0.0001, help="Buy commission rate")
    parser.add_argument("--buy_min", type=float, default=5.0, help="Buy commission minimum")
    parser.add_argument("--sell_rate", type=float, default=0.0001, help="Sell commission rate")
    parser.add_argument("--sell_min", type=float, default=5.0, help="Sell commission minimum")
    parser.add_argument("--stamp_duty", type=float, default=0.0005, help="Stamp duty rate")
    parser.add_argument("--deterministic", action="store_true", default=True, help="Use deterministic actions")
    parser.add_argument("--trading_cooldown", type=int, default=5, help="Min days between trades")
    args = parser.parse_args()

    # Configuration
    STOCK_CODE = args.stock_code
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    model_path = os.path.join(MODELS_DIR, f"ppo_stock_{STOCK_CODE}_v2.zip")
    
    # Create output directory if not exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    if not os.path.exists(model_path):
        print(f"Model not found at {model_path}. Please train first with train_ppo_v2.py")
        return

    print(f"Loading model from {model_path}...")
    
    # 创建环境
    env = StockTradingEnvV2(
        stock_code=STOCK_CODE, 
        data_dir=DATA_DIR, 
        render_mode='human',
        buy_rate=args.buy_rate,
        buy_min=args.buy_min,
        sell_rate=args.sell_rate,
        sell_min=args.sell_min,
        stamp_duty=args.stamp_duty,
        trading_cooldown=args.trading_cooldown,
    )
    
    # 加载模型
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
    
    print(f"\n{'='*70}")
    print("Starting prediction V2...")
    print(f"Action Space: {len(ACTION_NAMES)} actions")
    for k, v in ACTION_NAMES.items():
        print(f"  {k}: {v}")
    print(f"{'='*70}")
    print(f"Initial Balance: {env.initial_balance}")
    print("-" * 70)
    
    step_counter = 0
    action_counts = {i: 0 for i in range(7)}
    invalid_actions = 0
    
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
        action_counts[original_action] += 1
        if info.get("invalid_action", False):
            invalid_actions += 1
        
        net_worths.append(info['net_worth'])
        dates.append(info['date'])
        actions_taken.append({
            'date': info['date'],
            'action': original_action,
            'action_name': ACTION_NAMES[original_action],
            'executed': info.get('trade_happened', False),
            'net_worth': info['net_worth'],
            'shares': info['shares_held'],
            'balance': info['balance']
        })
        
        # 打印进度
        if env.current_step % 100 == 0:
            action_str = ACTION_NAMES.get(info.get('action', 0), 'Unknown')
            traded = "[OK]" if info.get('trade_happened', False) else ""
            print(f"Date: {info['date']}, Net Worth: {info['net_worth']:.2f}, "
                  f"Shares: {info['shares_held']}, Balance: {info['balance']:.2f}, "
                  f"Action: {action_str} {traded}")
    
    print("-" * 70)
    print(f"Prediction finished. Total Steps: {step_counter}")
    
    # 统计实际交易
    real_buys = len([t for t in env.trades if t['type'] == 'buy'])
    real_sells = len([t for t in env.trades if t['type'] == 'sell'])
    
    print(f"\n{'='*70}")
    print("Trade Statistics V2")
    print(f"{'='*70}")
    print(f"Action Distribution:")
    for action_id, count in action_counts.items():
        pct = count / step_counter * 100 if step_counter > 0 else 0
        print(f"  {ACTION_NAMES[action_id]:12s}: {count:5d} ({pct:5.1f}%)")
    
    print(f"\nExecuted Trades: Buy: {real_buys}, Sell: {real_sells}")
    print(f"Total Fees Paid: {env.total_fees_paid:.2f}")
    
    if not use_action_mask:
        invalid_rate = invalid_actions / step_counter * 100 if step_counter > 0 else 0
        print(f"Invalid Action Rate: {invalid_rate:.1f}%")
    else:
        print("[OK] Action Masking enabled, all actions are valid")
    
    # 计算交易频率
    trade_frequency = (real_buys + real_sells) / step_counter * 100 if step_counter > 0 else 0
    print(f"\nTrading Frequency: {trade_frequency:.2f}% of days")
    hold_rate = action_counts[0] / step_counter * 100 if step_counter > 0 else 0
    print(f"Hold Rate: {hold_rate:.1f}%")
    
    print(f"\n{'='*70}")
    print("Performance Summary")
    print(f"{'='*70}")
    print(f"Initial Balance: {env.initial_balance:.2f}")
    print(f"Final Net Worth: {info['net_worth']:.2f}")
    profit = info['net_worth'] - env.initial_balance
    profit_pct = profit / env.initial_balance * 100
    print(f"Profit/Loss: {profit:.2f} ({profit_pct:+.2f}%)")
    
    # Calculate max drawdown
    peak = net_worths[0]
    max_drawdown = 0
    max_drawdown_date = None
    for i, nw in enumerate(net_worths):
        if nw > peak:
            peak = nw
        drawdown = (peak - nw) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_drawdown_date = dates[i]
    print(f"Max Drawdown: {max_drawdown * 100:.2f}%")
    if max_drawdown_date:
        print(f"Max Drawdown Date: {max_drawdown_date}")
    
    # Calculate Sharpe Ratio (simplified)
    if len(net_worths) > 1:
        returns = np.diff(net_worths) / net_worths[:-1]
        if np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
            print(f"Sharpe Ratio (Annualized): {sharpe:.2f}")
    
    # 计算胜率
    if len(env.trades) > 0:
        sell_trades = [t for t in env.trades if t['type'] == 'sell']
        if sell_trades:
            winning_trades = [t for t in sell_trades if t.get('profit_per_share', 0) > 0]
            win_rate = len(winning_trades) / len(sell_trades) * 100
            print(f"Win Rate (Sell trades): {win_rate:.1f}%")
    
    print(f"{'='*70}\n")
    
    # Print trade details
    if len(env.trades) > 0:
        print("Trade Details (first 30):")
        print("-" * 90)
        for i, trade in enumerate(env.trades[:30]):
            ratio = trade.get('ratio', 0) * 100
            profit_info = ""
            if trade['type'] == 'sell' and 'profit_per_share' in trade:
                profit_info = f" | P/L: {trade['profit_per_share']:.2f}/share"
            print(f"{i+1:3d}. {trade['date']} | {trade['type'].upper():4s} {ratio:3.0f}% | "
                  f"Price: {trade['price']:.2f} | Shares: {trade['shares']:5d} | Fee: {trade['fee']:.2f}{profit_info}")
        if len(env.trades) > 30:
            print(f"... Total {len(env.trades)} trades")
        print("-" * 90)
    
    # 绘图
    try:
        fig, axes = plt.subplots(3, 1, figsize=(14, 12))
        
        # 净值曲线
        ax1 = axes[0]
        ax1.plot(dates, net_worths, label='Net Worth', color='blue', linewidth=1.5)
        ax1.axhline(y=env.initial_balance, color='gray', linestyle='--', label='Initial Balance')
        ax1.fill_between(dates, env.initial_balance, net_worths, 
                         where=[nw >= env.initial_balance for nw in net_worths],
                         color='green', alpha=0.3)
        ax1.fill_between(dates, env.initial_balance, net_worths,
                         where=[nw < env.initial_balance for nw in net_worths],
                         color='red', alpha=0.3)
        ax1.set_title(f'AI Trading Performance V2 - {STOCK_CODE}')
        ax1.set_xlabel('Date')
        ax1.set_ylabel('Net Worth')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # 标记买卖点
        buy_dates = [t['date'] for t in env.trades if t['type'] == 'buy']
        sell_dates = [t['date'] for t in env.trades if t['type'] == 'sell']
        
        for bd in buy_dates[:100]:
            if bd in dates:
                idx = dates.index(bd)
                ax1.scatter([bd], [net_worths[idx]], color='green', marker='^', s=30, zorder=5, alpha=0.7)
        for sd in sell_dates[:100]:
            if sd in dates:
                idx = dates.index(sd)
                ax1.scatter([sd], [net_worths[idx]], color='red', marker='v', s=30, zorder=5, alpha=0.7)
        
        # 回撤曲线
        ax2 = axes[1]
        peaks = np.maximum.accumulate(net_worths)
        drawdowns = (peaks - net_worths) / peaks * 100
        ax2.fill_between(dates, 0, drawdowns, color='red', alpha=0.5)
        ax2.set_title('Drawdown')
        ax2.set_xlabel('Date')
        ax2.set_ylabel('Drawdown (%)')
        ax2.grid(True, alpha=0.3)
        
        # 动作分布图
        ax3 = axes[2]
        action_labels = [ACTION_NAMES[i] for i in range(7)]
        action_values = [action_counts[i] for i in range(7)]
        colors = ['gray', 'lightgreen', 'green', 'darkgreen', 'lightsalmon', 'salmon', 'darkred']
        bars = ax3.bar(action_labels, action_values, color=colors)
        ax3.set_title('Action Distribution')
        ax3.set_xlabel('Action')
        ax3.set_ylabel('Count')
        ax3.grid(True, alpha=0.3, axis='y')
        
        # 在柱状图上显示数值
        for bar, val in zip(bars, action_values):
            ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 5, 
                    str(val), ha='center', va='bottom', fontsize=9)
        
        plt.tight_layout()
        
        output_plot = os.path.join(OUTPUT_DIR, f"prediction_plot_{STOCK_CODE}_v2.png")
        plt.savefig(output_plot, dpi=150)
        print(f"Performance plot saved to {output_plot}")
        plt.close()
        
    except Exception as e:
        print(f"Could not save plot: {e}")
    
    # 保存交易记录
    try:
        trades_df = pd.DataFrame(env.trades)
        trades_path = os.path.join(OUTPUT_DIR, f"trades_{STOCK_CODE}_v2.csv")
        trades_df.to_csv(trades_path, index=False)
        print(f"Trades saved to {trades_path}")
        
        # 保存详细动作记录
        actions_df = pd.DataFrame(actions_taken)
        actions_path = os.path.join(OUTPUT_DIR, f"actions_{STOCK_CODE}_v2.csv")
        actions_df.to_csv(actions_path, index=False)
        print(f"Actions saved to {actions_path}")
        
    except Exception as e:
        print(f"Could not save data: {e}")


if __name__ == "__main__":
    predict()

