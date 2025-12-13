"""
LSTM/GRU 多股票模型预测脚本

使用 LSTM/GRU 训练的模型对股票进行预测和回测。

使用示例：
    # 使用 LSTM 模型预测单只股票
    python predict_lstm_multi.py --stock_code 000001 --model_name lstm_multi_stock
    
    # 批量预测多只股票
    python predict_lstm_multi.py --stock_codes 000001,000002,600000 --model_name gru_multi_stock
"""
import os
import sys
import gymnasium as gym
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse

# 导入自定义策略（需要在加载模型前导入，以便注册自定义类）
from rl_trading.policies.lstm_policy import (
    LSTMFeatureExtractor, 
    GRUFeatureExtractor, 
    TransformerFeatureExtractor
)
from rl_trading.envs.stock_trading_env import StockTradingEnv

# 尝试导入 MaskablePPO
try:
    from sb3_contrib import MaskablePPO
    HAS_MASKABLE_PPO = True
except ImportError:
    from stable_baselines3 import PPO
    HAS_MASKABLE_PPO = False


def predict_single_stock(model, stock_code, args, data_dir, output_dir, model_name):
    """对单只股票进行预测"""
    print(f"\n{'='*60}")
    print(f"Predicting for stock: {stock_code}")
    print(f"Using model: {model_name}")
    print(f"{'='*60}")
    
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
    
    use_action_mask = HAS_MASKABLE_PPO
    
    obs, info = env.reset()
    done = False
    truncated = False
    
    net_worths = []
    dates = []
    actions_taken = []
    
    print(f"Initial Balance: {env.initial_balance}")
    print("-" * 60)
    
    step_counter = 0
    action_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    
    while not done and not truncated:
        if use_action_mask:
            action_mask = env.action_masks()
            action, _states = model.predict(obs, deterministic=args.deterministic, action_masks=action_mask)
        else:
            action, _states = model.predict(obs, deterministic=args.deterministic)
        
        original_action = int(action)
        obs, reward, done, truncated, info = env.step(action)
        
        step_counter += 1
        action_counts[original_action] = action_counts.get(original_action, 0) + 1
        
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
        
        if env.current_step % 200 == 0:
            action_str = ['Hold', 'Buy50%', 'Buy100%', 'Sell50%', 'Sell100%'][info.get('action', 0)]
            traded = "[OK]" if info.get('trade_happened', False) else ""
            print(f"Date: {info['date']}, Net Worth: {info['net_worth']:.2f}, "
                  f"Shares: {info['shares_held']}, Action: {action_str} {traded}")
    
    print("-" * 60)
    print(f"Prediction finished. Total Steps: {step_counter}")
    
    real_buys = len([t for t in env.trades if t['type'] == 'buy'])
    real_sells = len([t for t in env.trades if t['type'] == 'sell'])
    
    profit = info['net_worth'] - env.initial_balance
    profit_pct = profit / env.initial_balance * 100
    
    peak = net_worths[0]
    max_drawdown = 0
    for nw in net_worths:
        if nw > peak:
            peak = nw
        drawdown = (peak - nw) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    
    sharpe = 0
    if len(net_worths) > 1:
        returns = np.diff(net_worths) / net_worths[:-1]
        if np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
    
    result = {
        'stock_code': stock_code,
        'model_name': model_name,
        'initial_balance': env.initial_balance,
        'final_net_worth': info['net_worth'],
        'profit': profit,
        'profit_pct': profit_pct,
        'max_drawdown': max_drawdown * 100,
        'sharpe_ratio': sharpe,
        'total_trades': real_buys + real_sells,
        'buy_trades': real_buys,
        'sell_trades': real_sells,
        'action_counts': action_counts,
    }
    
    print(f"\n{'='*60}")
    print(f"Performance Summary - {stock_code} ({model_name})")
    print(f"{'='*60}")
    print(f"Initial Balance: {env.initial_balance:.2f}")
    print(f"Final Net Worth: {info['net_worth']:.2f}")
    print(f"Profit/Loss: {profit:.2f} ({profit_pct:+.2f}%)")
    print(f"Max Drawdown: {max_drawdown * 100:.2f}%")
    print(f"Sharpe Ratio: {sharpe:.2f}")
    print(f"Total Trades: Buy={real_buys}, Sell={real_sells}")
    print(f"Action Distribution: {action_counts}")
    print(f"{'='*60}")
    
    if args.save_plots:
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
                    idx = plot_dates.index(trade['date'])
                    ax1.scatter([trade['date']], [trade['price']], color='red', marker='^', s=80, zorder=5, label='_nolegend_')
                    ax1.annotate('B', (trade['date'], trade['price']), textcoords="offset points", 
                               xytext=(0, 10), ha='center', fontsize=8, color='red', fontweight='bold')
            
            for trade in sell_trades:
                if trade['date'] in plot_dates:
                    idx = plot_dates.index(trade['date'])
                    ax1.scatter([trade['date']], [trade['price']], color='green', marker='v', s=80, zorder=5, label='_nolegend_')
                    ax1.annotate('S', (trade['date'], trade['price']), textcoords="offset points", 
                               xytext=(0, -15), ha='center', fontsize=8, color='green', fontweight='bold')
            
            ax1.set_title(f'Stock Price & Trading Signals - {stock_code}', fontsize=12)
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
            ax2.set_title(f'LSTM/GRU Model Performance - {model_name}', fontsize=12)
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
            
            output_plot = os.path.join(output_dir, f"lstm_prediction_{stock_code}_{model_name}.png")
            plt.savefig(output_plot, dpi=150, bbox_inches='tight')
            print(f"Plot saved to {output_plot}")
            plt.close()
            
        except Exception as e:
            print(f"Could not save plot: {e}")
    
    if args.save_trades and len(env.trades) > 0:
        try:
            trades_df = pd.DataFrame(env.trades)
            trades_path = os.path.join(output_dir, f"lstm_trades_{stock_code}_{model_name}.csv")
            trades_df.to_csv(trades_path, index=False)
            print(f"Trades saved to {trades_path}")
        except Exception as e:
            print(f"Could not save trades: {e}")
    
    return result


def predict():
    parser = argparse.ArgumentParser(description="LSTM/GRU Model Prediction")
    
    parser.add_argument("--model_name", type=str, default="lstm_multi_stock", help="Model name to load")
    parser.add_argument("--stock_code", type=str, default="", help="Single stock code to predict")
    parser.add_argument("--stock_codes", type=str, default="", help="Comma-separated stock codes")
    
    parser.add_argument("--buy_rate", type=float, default=0.0001, help="Buy commission rate")
    parser.add_argument("--buy_min", type=float, default=5.0, help="Buy commission minimum")
    parser.add_argument("--sell_rate", type=float, default=0.0001, help="Sell commission rate")
    parser.add_argument("--sell_min", type=float, default=5.0, help="Sell commission minimum")
    parser.add_argument("--stamp_duty", type=float, default=0.0005, help="Stamp duty rate")
    parser.add_argument("--trade_percent", type=float, default=0.5, help="Trade amount percent")
    
    parser.add_argument("--deterministic", action="store_true", default=True, help="Use deterministic actions")
    parser.add_argument("--save_plots", action="store_true", default=True, help="Save performance plots")
    parser.add_argument("--save_trades", action="store_true", default=True, help="Save trade records")
    
    args = parser.parse_args()

    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    model_path = os.path.join(MODELS_DIR, f"{args.model_name}.zip")
    
    if not os.path.exists(model_path):
        print(f"[ERROR] Model not found at {model_path}")
        print(f"Available models in {MODELS_DIR}:")
        for f in os.listdir(MODELS_DIR):
            if f.endswith('.zip'):
                print(f"  - {f}")
        return

    print(f"Loading model from {model_path}...")
    print("[INFO] Loading with custom LSTM/GRU feature extractor support")
    
    # 加载模型（自定义类已在文件顶部导入）
    try:
        if HAS_MASKABLE_PPO:
            # 需要传入 custom_objects 以识别自定义类
            custom_objects = {
                "LSTMFeatureExtractor": LSTMFeatureExtractor,
                "GRUFeatureExtractor": GRUFeatureExtractor,
                "TransformerFeatureExtractor": TransformerFeatureExtractor,
            }
            model = MaskablePPO.load(model_path, custom_objects=custom_objects)
            print("[OK] Using MaskablePPO with custom feature extractor")
        else:
            raise ImportError()
    except Exception as e:
        print(f"[WARN] MaskablePPO load failed: {e}")
        try:
            from stable_baselines3 import PPO
            custom_objects = {
                "LSTMFeatureExtractor": LSTMFeatureExtractor,
                "GRUFeatureExtractor": GRUFeatureExtractor,
                "TransformerFeatureExtractor": TransformerFeatureExtractor,
            }
            model = PPO.load(model_path, custom_objects=custom_objects)
            print("[OK] Using standard PPO with custom feature extractor")
        except Exception as e2:
            print(f"[ERROR] Could not load model: {e2}")
            return
    
    stock_codes = []
    if args.stock_code:
        stock_codes = [args.stock_code]
    elif args.stock_codes:
        stock_codes = [code.strip() for code in args.stock_codes.split(",")]
    else:
        print("[ERROR] Please specify --stock_code or --stock_codes")
        return
    
    print(f"\nPredicting for {len(stock_codes)} stock(s): {stock_codes}")
    
    results = []
    for stock_code in stock_codes:
        result = predict_single_stock(model, stock_code, args, DATA_DIR, OUTPUT_DIR, args.model_name)
        if result:
            results.append(result)
    
    if len(results) > 1:
        print(f"\n{'='*80}")
        print("Batch Prediction Summary")
        print(f"{'='*80}")
        
        df = pd.DataFrame(results)
        print(df[['stock_code', 'profit_pct', 'max_drawdown', 'sharpe_ratio', 'total_trades']].to_string(index=False))
        
        print(f"\n{'='*80}")
        print(f"Average Return: {df['profit_pct'].mean():.2f}%")
        print(f"Win Rate: {(df['profit_pct'] > 0).sum() / len(df) * 100:.1f}%")
        print(f"Best: {df.loc[df['profit_pct'].idxmax(), 'stock_code']} ({df['profit_pct'].max():.2f}%)")
        print(f"Worst: {df.loc[df['profit_pct'].idxmin(), 'stock_code']} ({df['profit_pct'].min():.2f}%)")
        print(f"{'='*80}")
        
        summary_path = os.path.join(OUTPUT_DIR, f"lstm_batch_results_{args.model_name}.csv")
        df.to_csv(summary_path, index=False)
        print(f"\nBatch results saved to {summary_path}")


if __name__ == "__main__":
    predict()

