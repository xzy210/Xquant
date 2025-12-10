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
        fig, axes = plt.subplots(2, 1, figsize=(14, 10))
        
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
        ax1.set_title(f'AI Trading Performance - {STOCK_CODE}')
        ax1.set_xlabel('Date')
        ax1.set_ylabel('Net Worth')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # 标记买卖点
        buy_dates = [t['date'] for t in env.trades if t['type'] == 'buy']
        sell_dates = [t['date'] for t in env.trades if t['type'] == 'sell']
        
        # 在净值曲线上标记
        for bd in buy_dates[:50]:  # 限制标记数量
            if bd in dates:
                idx = dates.index(bd)
                ax1.scatter([bd], [net_worths[idx]], color='green', marker='^', s=50, zorder=5)
        for sd in sell_dates[:50]:
            if sd in dates:
                idx = dates.index(sd)
                ax1.scatter([sd], [net_worths[idx]], color='red', marker='v', s=50, zorder=5)
        
        # 回撤曲线
        ax2 = axes[1]
        peaks = np.maximum.accumulate(net_worths)
        drawdowns = (peaks - net_worths) / peaks * 100
        ax2.fill_between(dates, 0, drawdowns, color='red', alpha=0.5)
        ax2.set_title('Drawdown')
        ax2.set_xlabel('Date')
        ax2.set_ylabel('Drawdown (%)')
        ax2.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        output_plot = os.path.join(OUTPUT_DIR, f"prediction_plot_{STOCK_CODE}.png")
        plt.savefig(output_plot, dpi=150)
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
