"""
多股票模型预测脚本

使用多股票训练的模型对单只股票或多只股票进行预测和回测。

使用示例：
    # 使用多股票模型预测单只股票
    python predict_ppo_multi.py --stock_code 000001
    
    # 批量预测多只股票
    python predict_ppo_multi.py --stock_codes 000001,000002,600000
    
    # 使用指定的模型
    python predict_ppo_multi.py --model_name ppo_multi_stock --stock_code 000001
"""
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


def predict_single_stock(model, stock_code, args, data_dir, output_dir):
    """对单只股票进行预测"""
    print(f"\n{'='*60}")
    print(f"Predicting for stock: {stock_code}")
    print(f"{'='*60}")
    
    # 创建环境
    try:
        env = StockTradingEnv(
            stock_code=stock_code, 
            data_dir=data_dir, 
            render_mode='human',
            buy_rate=args.buy_rate,
            buy_min=args.buy_min,
            sell_rate=args.sell_rate,
            sell_min=args.sell_min,
            stamp_duty=args.stamp_duty,
            trade_amount_percent=args.trade_percent
        )
    except Exception as e:
        print(f"[ERROR] Could not load data for {stock_code}: {e}")
        return None
    
    # 确定是否使用 action mask
    use_action_mask = HAS_MASKABLE_PPO and hasattr(model, 'predict')
    
    # 运行预测
    obs, info = env.reset()
    done = False
    truncated = False
    
    net_worths = []
    dates = []
    actions_taken = []
    
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
        if env.current_step % 200 == 0:
            action_str = ['Hold', 'Buy50%', 'Buy100%', 'Sell50%', 'Sell100%'][info.get('action', 0)]
            traded = "[OK]" if info.get('trade_happened', False) else ""
            print(f"Date: {info['date']}, Net Worth: {info['net_worth']:.2f}, "
                  f"Shares: {info['shares_held']}, Action: {action_str} {traded}")
    
    print("-" * 60)
    print(f"Prediction finished. Total Steps: {step_counter}")
    
    # 统计实际交易
    real_buys = len([t for t in env.trades if t['type'] == 'buy'])
    real_sells = len([t for t in env.trades if t['type'] == 'sell'])
    
    # 计算性能指标
    profit = info['net_worth'] - env.initial_balance
    profit_pct = profit / env.initial_balance * 100
    
    # 最大回撤
    peak = net_worths[0]
    max_drawdown = 0
    for nw in net_worths:
        if nw > peak:
            peak = nw
        drawdown = (peak - nw) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    
    # Sharpe Ratio
    sharpe = 0
    if len(net_worths) > 1:
        returns = np.diff(net_worths) / net_worths[:-1]
        if np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
    
    result = {
        'stock_code': stock_code,
        'initial_balance': env.initial_balance,
        'final_net_worth': info['net_worth'],
        'profit': profit,
        'profit_pct': profit_pct,
        'max_drawdown': max_drawdown * 100,
        'sharpe_ratio': sharpe,
        'total_trades': real_buys + real_sells,
        'buy_trades': real_buys,
        'sell_trades': real_sells,
        'hold_actions': hold_actions,
        'buy50_actions': buy50_actions,
        'buy100_actions': buy100_actions,
        'sell50_actions': sell50_actions,
        'sell100_actions': sell100_actions,
    }
    
    print(f"\n{'='*60}")
    print(f"Performance Summary - {stock_code}")
    print(f"{'='*60}")
    print(f"Initial Balance: {env.initial_balance:.2f}")
    print(f"Final Net Worth: {info['net_worth']:.2f}")
    print(f"Profit/Loss: {profit:.2f} ({profit_pct:+.2f}%)")
    print(f"Max Drawdown: {max_drawdown * 100:.2f}%")
    print(f"Sharpe Ratio: {sharpe:.2f}")
    print(f"Total Trades: Buy={real_buys}, Sell={real_sells}")
    print(f"{'='*60}")
    
    # 保存图表
    if args.save_plots:
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
            ax1.set_title(f'Multi-Stock Model Performance - {stock_code}')
            ax1.set_xlabel('Date')
            ax1.set_ylabel('Net Worth')
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            
            # 标记买卖点
            buy_dates = [t['date'] for t in env.trades if t['type'] == 'buy']
            sell_dates = [t['date'] for t in env.trades if t['type'] == 'sell']
            
            for bd in buy_dates[:50]:
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
            
            output_plot = os.path.join(output_dir, f"multi_model_prediction_{stock_code}.png")
            plt.savefig(output_plot, dpi=150)
            print(f"Plot saved to {output_plot}")
            plt.close()
            
        except Exception as e:
            print(f"Could not save plot: {e}")
    
    # 保存交易记录
    if args.save_trades and len(env.trades) > 0:
        try:
            trades_df = pd.DataFrame(env.trades)
            trades_path = os.path.join(output_dir, f"multi_model_trades_{stock_code}.csv")
            trades_df.to_csv(trades_path, index=False)
            print(f"Trades saved to {trades_path}")
        except Exception as e:
            print(f"Could not save trades: {e}")
    
    return result


def predict():
    parser = argparse.ArgumentParser(description="Multi-Stock Model Prediction")
    
    # 模型参数
    parser.add_argument("--model_name", type=str, default="ppo_multi_stock", help="Model name to load")
    
    # 股票选择
    parser.add_argument("--stock_code", type=str, default="", help="Single stock code to predict")
    parser.add_argument("--stock_codes", type=str, default="", 
                        help="Comma-separated stock codes for batch prediction")
    
    # 交易参数
    parser.add_argument("--buy_rate", type=float, default=0.0001, help="Buy commission rate")
    parser.add_argument("--buy_min", type=float, default=5.0, help="Buy commission minimum")
    parser.add_argument("--sell_rate", type=float, default=0.0001, help="Sell commission rate")
    parser.add_argument("--sell_min", type=float, default=5.0, help="Sell commission minimum")
    parser.add_argument("--stamp_duty", type=float, default=0.0005, help="Stamp duty rate")
    parser.add_argument("--trade_percent", type=float, default=0.5, help="Trade amount percent")
    
    # 预测参数
    parser.add_argument("--deterministic", action="store_true", default=True, help="Use deterministic actions")
    parser.add_argument("--save_plots", action="store_true", default=True, help="Save performance plots")
    parser.add_argument("--save_trades", action="store_true", default=True, help="Save trade records")
    
    args = parser.parse_args()

    # 目录配置
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 加载模型
    model_path = os.path.join(MODELS_DIR, f"{args.model_name}.zip")
    
    if not os.path.exists(model_path):
        print(f"[ERROR] Model not found at {model_path}")
        print(f"Available models in {MODELS_DIR}:")
        for f in os.listdir(MODELS_DIR):
            if f.endswith('.zip'):
                print(f"  - {f}")
        return

    print(f"Loading model from {model_path}...")
    
    # 加载模型 - 自动检测模型类型
    try:
        if HAS_MASKABLE_PPO:
            model = MaskablePPO.load(model_path)
            print("[OK] Using MaskablePPO for prediction")
        else:
            raise ImportError()
    except:
        from stable_baselines3 import PPO
        model = PPO.load(model_path)
        print("[WARN] Using standard PPO for prediction (no Action Masking)")
    
    # 确定要预测的股票
    stock_codes = []
    if args.stock_code:
        stock_codes = [args.stock_code]
    elif args.stock_codes:
        stock_codes = [code.strip() for code in args.stock_codes.split(",")]
    else:
        print("[ERROR] Please specify --stock_code or --stock_codes")
        return
    
    print(f"\nPredicting for {len(stock_codes)} stock(s): {stock_codes}")
    
    # 批量预测
    results = []
    for stock_code in stock_codes:
        result = predict_single_stock(model, stock_code, args, DATA_DIR, OUTPUT_DIR)
        if result:
            results.append(result)
    
    # 汇总结果
    if len(results) > 1:
        print(f"\n{'='*80}")
        print("Batch Prediction Summary")
        print(f"{'='*80}")
        
        df = pd.DataFrame(results)
        
        # 打印汇总表格
        print(df[['stock_code', 'profit_pct', 'max_drawdown', 'sharpe_ratio', 'total_trades']].to_string(index=False))
        
        print(f"\n{'='*80}")
        print(f"Average Return: {df['profit_pct'].mean():.2f}%")
        print(f"Win Rate: {(df['profit_pct'] > 0).sum() / len(df) * 100:.1f}%")
        print(f"Best: {df.loc[df['profit_pct'].idxmax(), 'stock_code']} ({df['profit_pct'].max():.2f}%)")
        print(f"Worst: {df.loc[df['profit_pct'].idxmin(), 'stock_code']} ({df['profit_pct'].min():.2f}%)")
        print(f"{'='*80}")
        
        # 保存汇总结果
        summary_path = os.path.join(OUTPUT_DIR, f"multi_model_batch_results.csv")
        df.to_csv(summary_path, index=False)
        print(f"\nBatch results saved to {summary_path}")


if __name__ == "__main__":
    predict()

