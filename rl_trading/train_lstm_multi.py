"""
LSTM/GRU 多股票训练脚本

使用自定义 LSTM/GRU 特征提取器训练交易策略。

特点：
1. 支持 LSTM、GRU、Transformer 三种序列模型
2. 自注意力机制增强时序特征提取
3. 支持多股票并行训练
4. 支持继续训练

使用示例：
    # 使用 LSTM 训练
    python train_lstm_multi.py --rnn_type lstm --timesteps 1000000
    
    # 使用 GRU 训练（更快）
    python train_lstm_multi.py --rnn_type gru --timesteps 1000000
    
    # 继续训练
    python train_lstm_multi.py --resume lstm_multi_stock --timesteps 500000
"""
import os
import sys
import multiprocessing
import gymnasium as gym
import numpy as np
import torch
import random

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import pandas as pd

# 全局配置变量（用于子进程）
_ENV_CONFIG = {}


def _make_multi_env_subprocess(env_id):
    """子进程中创建多股票环境"""
    from rl_trading.envs.multi_stock_trading_env import MultiStockTradingEnv
    from stable_baselines3.common.monitor import Monitor
    
    try:
        from sb3_contrib.common.wrappers import ActionMasker
        HAS_ACTION_MASKER = True
    except ImportError:
        HAS_ACTION_MASKER = False
    
    def get_action_masks(env):
        return env.action_masks()
    
    config = _ENV_CONFIG
    
    env = MultiStockTradingEnv(
        stock_codes=config['stock_codes'],
        data_dir=config['data_dir'],
        buy_rate=config['buy_rate'],
        buy_min=config['buy_min'],
        sell_rate=config['sell_rate'],
        sell_min=config['sell_min'],
        stamp_duty=config['stamp_duty'],
        trade_amount_percent=config['trade_percent'],
        random_stock_per_episode=True,
    )
    
    if config.get('use_maskable_ppo', False) and HAS_ACTION_MASKER:
        env = ActionMasker(env, get_action_masks)
    
    env = Monitor(env)
    env.reset(seed=env_id * 42 + random.randint(0, 1000))
    
    return env


def make_multi_env_fn(env_id, config):
    """创建多股票环境的工厂函数"""
    def _init():
        global _ENV_CONFIG
        _ENV_CONFIG = config
        return _make_multi_env_subprocess(env_id)
    return _init


class RewardLoggingCallback:
    """训练日志回调"""
    def __init__(self, verbose=0, num_envs=1):
        self.verbose = verbose
        self.num_envs = num_envs
        self.episode_rewards = []
        self.episode_lengths = []
        self.episode_net_worths = []
        self.stock_rewards = {}
        self.last_print_step = 0
        self.print_freq = 8192


class ParallelTrainingCallback:
    """用于多环境并行训练的回调"""
    def __init__(self, stats_logger, verbose=0):
        from stable_baselines3.common.callbacks import BaseCallback
        
        _stats_logger = stats_logger
        
        class _Callback(BaseCallback):
            def __init__(self, verbose=0):
                super().__init__(verbose)
                self.stats_logger = _stats_logger
                
            def _on_step(self) -> bool:
                if self.locals.get("dones") is not None:
                    for i, done in enumerate(self.locals["dones"]):
                        if done:
                            if "infos" in self.locals and len(self.locals["infos"]) > i:
                                info = self.locals["infos"][i]
                                if "episode" in info:
                                    self.stats_logger.episode_rewards.append(info["episode"]["r"])
                                    self.stats_logger.episode_lengths.append(info["episode"]["l"])
                                if "net_worth" in info:
                                    self.stats_logger.episode_net_worths.append(info["net_worth"])
                                    stock_code = info.get("stock_code", "unknown")
                                    if stock_code not in self.stats_logger.stock_rewards:
                                        self.stats_logger.stock_rewards[stock_code] = []
                                    self.stats_logger.stock_rewards[stock_code].append(info["net_worth"])
                
                if self.num_timesteps - self.stats_logger.last_print_step >= self.stats_logger.print_freq:
                    if len(self.stats_logger.episode_rewards) > 0:
                        mean_reward = np.mean(self.stats_logger.episode_rewards[-20:])
                        mean_length = np.mean(self.stats_logger.episode_lengths[-20:])
                        
                        print(f"\n{'='*70}")
                        print(f"[LSTM Multi-Stock] Timestep: {self.num_timesteps} | Envs: {self.stats_logger.num_envs}")
                        print(f"Episodes completed: {len(self.stats_logger.episode_rewards)}")
                        print(f"Mean episode reward (last 20): {mean_reward:.2f}")
                        print(f"Mean episode length (last 20): {mean_length:.0f}")
                        
                        if len(self.stats_logger.episode_rewards) >= 2:
                            print(f"Min/Max reward: {min(self.stats_logger.episode_rewards[-20:]):.2f} / {max(self.stats_logger.episode_rewards[-20:]):.2f}")
                        
                        if len(self.stats_logger.episode_net_worths) > 0:
                            mean_nw = np.mean(self.stats_logger.episode_net_worths[-20:])
                            print(f"Mean final net worth (last 20): {mean_nw:.2f}")
                        
                        if len(self.stats_logger.stock_rewards) > 0:
                            print(f"\nStock-wise performance (recent):")
                            for code, nws in sorted(self.stats_logger.stock_rewards.items())[:10]:
                                if len(nws) > 0:
                                    recent_nw = np.mean(nws[-5:]) if len(nws) >= 5 else np.mean(nws)
                                    print(f"  {code}: {recent_nw:.0f} (episodes: {len(nws)})")
                            if len(self.stats_logger.stock_rewards) > 10:
                                print(f"  ... and {len(self.stats_logger.stock_rewards) - 10} more stocks")
                        
                        print(f"{'='*70}\n")
                        
                    self.stats_logger.last_print_step = self.num_timesteps
                
                return True
        
        self.callback = _Callback(verbose)
    
    def get_callback(self):
        return self.callback


def get_device_info():
    """获取设备信息"""
    if torch.cuda.is_available():
        device_name = torch.cuda.get_device_name(0)
        device = "cuda"
        print(f"[OK] CUDA available: {device_name}")
    else:
        device_name = "CPU"
        device = "cpu"
        print(f"[INFO] CUDA not available, using CPU")
    return device, device_name


def train():
    # 尝试导入 MaskablePPO
    try:
        from sb3_contrib import MaskablePPO
        from sb3_contrib.common.wrappers import ActionMasker
        USE_MASKABLE_PPO = True
        print("[OK] Using MaskablePPO (Action Masking enabled)")
    except ImportError:
        from stable_baselines3 import PPO
        USE_MASKABLE_PPO = False
        print("[WARN] sb3_contrib not installed, using standard PPO (no Action Masking)")
    
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
    from rl_trading.envs.multi_stock_trading_env import MultiStockTradingEnv, get_available_stock_codes
    from rl_trading.policies.lstm_policy import get_policy_kwargs

    parser = argparse.ArgumentParser(description="LSTM/GRU Multi-Stock PPO Training")
    
    # RNN 配置
    parser.add_argument("--rnn_type", type=str, default="lstm", choices=["lstm", "gru", "transformer"],
                        help="RNN type: lstm, gru, or transformer")
    parser.add_argument("--hidden_size", type=int, default=128, help="RNN hidden size")
    parser.add_argument("--rnn_layers", type=int, default=2, help="Number of RNN layers")
    parser.add_argument("--features_dim", type=int, default=128, help="Feature extractor output dimension")
    parser.add_argument("--dropout", type=float, default=0.1, help="Dropout rate")
    parser.add_argument("--bidirectional", action="store_true", help="Use bidirectional RNN")
    
    # 基本训练参数
    parser.add_argument("--timesteps", type=int, default=1000000, help="Total training timesteps")
    parser.add_argument("--learning_rate", type=float, default=0.0003, help="Learning rate")
    parser.add_argument("--ent_coef", type=float, default=0.01, help="Entropy coefficient")
    parser.add_argument("--model_name", type=str, default="", help="Model name for saving (auto-generated if empty)")
    parser.add_argument("--resume", type=str, default="", help="Resume training from existing model")
    
    # 股票筛选参数
    parser.add_argument("--stock_codes", type=str, default="", help="Comma-separated stock codes")
    parser.add_argument("--include_cyb", action="store_true", help="Include 创业板")
    parser.add_argument("--include_kcb", action="store_true", help="Include 科创板")
    parser.add_argument("--include_bse", action="store_true", help="Include 北交所")
    parser.add_argument("--include_st", action="store_true", help="Include ST stocks")
    parser.add_argument("--max_stocks", type=int, default=0, help="Max number of stocks (0 = all)")
    parser.add_argument("--min_data_days", type=int, default=500, help="Minimum data days")
    
    # 交易参数
    parser.add_argument("--buy_rate", type=float, default=0.0001, help="Buy commission rate")
    parser.add_argument("--buy_min", type=float, default=5.0, help="Buy commission minimum")
    parser.add_argument("--sell_rate", type=float, default=0.0001, help="Sell commission rate")
    parser.add_argument("--sell_min", type=float, default=5.0, help="Sell commission minimum")
    parser.add_argument("--stamp_duty", type=float, default=0.0005, help="Stamp duty rate")
    parser.add_argument("--trade_percent", type=float, default=0.5, help="Trade amount percent")
    
    # 并行参数
    parser.add_argument("--num_envs", type=int, default=8, help="Number of parallel envs")
    parser.add_argument("--device", type=str, default="auto", help="Device: auto/cuda/cpu")
    
    args = parser.parse_args()

    # 自动生成模型名称
    if not args.model_name:
        args.model_name = f"{args.rnn_type}_multi_stock"
    
    # 设备检测
    if args.device == "auto":
        device, device_name = get_device_info()
    else:
        device = args.device
        device_name = args.device.upper()
    
    num_envs = args.num_envs
    print(f"[INFO] Using {num_envs} parallel environments")
    print(f"[INFO] RNN Type: {args.rnn_type.upper()}")

    # 目录配置
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    STOCKLIST_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stocklist.csv")
    
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    # 获取股票列表
    if args.stock_codes:
        stock_codes = [code.strip() for code in args.stock_codes.split(",")]
        print(f"[INFO] Using specified stock codes: {len(stock_codes)} stocks")
    else:
        print(f"\n[INFO] Auto-selecting stocks from {DATA_DIR}")
        stock_codes = get_available_stock_codes(
            data_dir=DATA_DIR,
            stocklist_path=STOCKLIST_PATH,
            exclude_cyb=not args.include_cyb,
            exclude_kcb=not args.include_kcb,
            exclude_bse=not args.include_bse,
            exclude_st=not args.include_st,
            min_data_days=args.min_data_days
        )
    
    if not stock_codes:
        print("[ERROR] No valid stock codes found!")
        return
    
    if args.max_stocks > 0 and len(stock_codes) > args.max_stocks:
        random.shuffle(stock_codes)
        stock_codes = stock_codes[:args.max_stocks]
        print(f"[INFO] Limited to {args.max_stocks} randomly selected stocks")
    
    print(f"\n[OK] Found {len(stock_codes)} valid stocks for training")

    print(f"\n{'='*70}")
    print(f"{args.rnn_type.upper()} Multi-Stock Training Configuration")
    print(f"{'='*70}")
    print(f"RNN Type: {args.rnn_type.upper()}")
    print(f"Hidden Size: {args.hidden_size}")
    print(f"RNN Layers: {args.rnn_layers}")
    print(f"Features Dim: {args.features_dim}")
    print(f"Bidirectional: {args.bidirectional}")
    print(f"Total Stocks: {len(stock_codes)}")
    print(f"Device: {device_name}")
    print(f"Parallel Environments: {num_envs}")
    print(f"Total Timesteps: {args.timesteps}")
    print(f"Learning Rate: {args.learning_rate}")
    print(f"Model Name: {args.model_name}")
    if args.resume:
        print(f"Resume From: {args.resume}")
    print(f"{'='*70}\n")

    # 环境配置
    env_config = {
        'stock_codes': stock_codes,
        'data_dir': DATA_DIR,
        'buy_rate': args.buy_rate,
        'buy_min': args.buy_min,
        'sell_rate': args.sell_rate,
        'sell_min': args.sell_min,
        'stamp_duty': args.stamp_duty,
        'trade_percent': args.trade_percent,
        'use_maskable_ppo': USE_MASKABLE_PPO,
    }
    
    global _ENV_CONFIG
    _ENV_CONFIG = env_config

    # 创建并行环境
    if num_envs > 1:
        print(f"Creating {num_envs} parallel environments...")
        try:
            env = SubprocVecEnv([make_multi_env_fn(i, env_config) for i in range(num_envs)])
            print(f"[OK] SubprocVecEnv created successfully")
        except Exception as e:
            print(f"[WARN] SubprocVecEnv failed ({e}), falling back to DummyVecEnv")
            env = DummyVecEnv([make_multi_env_fn(i, env_config) for i in range(num_envs)])
    else:
        env = DummyVecEnv([make_multi_env_fn(0, env_config)])

    # Tensorboard
    try:
        import tensorboard
        tb_log = LOG_DIR
    except ImportError:
        tb_log = None
    
    # 获取策略配置（使用自定义 LSTM/GRU 特征提取器）
    policy_kwargs = get_policy_kwargs(
        rnn_type=args.rnn_type,
        features_dim=args.features_dim,
        hidden_size=args.hidden_size,
        num_layers=args.rnn_layers,
        dropout=args.dropout,
        bidirectional=args.bidirectional,
        net_arch=[64, 64],  # 策略和价值网络架构
    )
    
    print(f"[INFO] Policy kwargs: {policy_kwargs}")
    
    # 创建或加载模型
    is_resume = bool(args.resume)
    
    if is_resume:
        resume_path = os.path.join(MODELS_DIR, f"{args.resume}.zip")
        if not os.path.exists(resume_path):
            print(f"[ERROR] Resume model not found: {resume_path}")
            env.close()
            return
        
        print(f"\n[RESUME] Loading model from: {resume_path}")
        
        if USE_MASKABLE_PPO:
            model = MaskablePPO.load(resume_path, env=env, device=device, tensorboard_log=tb_log)
            model.learning_rate = args.learning_rate
            model.ent_coef = args.ent_coef
        else:
            from stable_baselines3 import PPO
            model = PPO.load(resume_path, env=env, device=device, tensorboard_log=tb_log)
            model.learning_rate = args.learning_rate
            model.ent_coef = args.ent_coef
        
        print(f"[OK] Model loaded, resuming training...")
    else:
        print(f"Creating new {args.rnn_type.upper()} model on device: {device}")
        
        if USE_MASKABLE_PPO:
            model = MaskablePPO(
                "MlpPolicy", 
                env, 
                policy_kwargs=policy_kwargs,
                verbose=1, 
                tensorboard_log=tb_log,
                learning_rate=args.learning_rate,
                ent_coef=args.ent_coef,
                n_steps=2048,
                batch_size=128,
                n_epochs=10,
                gamma=0.99,
                gae_lambda=0.95,
                clip_range=0.2,
                vf_coef=0.5,
                device=device,
            )
        else:
            from stable_baselines3 import PPO
            model = PPO(
                "MlpPolicy", 
                env, 
                policy_kwargs=policy_kwargs,
                verbose=1, 
                tensorboard_log=tb_log,
                learning_rate=args.learning_rate,
                ent_coef=args.ent_coef,
                n_steps=2048,
                batch_size=128,
                n_epochs=10,
                gamma=0.99,
                gae_lambda=0.95,
                clip_range=0.2,
                vf_coef=0.5,
                device=device,
            )

    # 训练
    print(f"\nStarting {args.rnn_type.upper()} training for {args.timesteps} timesteps...")
    
    logger = RewardLoggingCallback(verbose=1, num_envs=num_envs)
    callback_wrapper = ParallelTrainingCallback(logger, verbose=1)
    
    try:
        import time
        start_time = time.time()
        
        model.learn(total_timesteps=args.timesteps, callback=callback_wrapper.get_callback())
        
        elapsed = time.time() - start_time
        fps = args.timesteps / elapsed
        
        print("\n" + "="*70)
        print(f"{args.rnn_type.upper()} Multi-Stock Training Completed!")
        print(f"Time elapsed: {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        print(f"Average FPS: {fps:.0f}")
        print(f"Total episodes: {len(logger.episode_rewards)}")
        
        if len(logger.episode_rewards) > 0:
            print(f"Final mean reward (last 20): {np.mean(logger.episode_rewards[-20:]):.2f}")
            print(f"Overall mean reward: {np.mean(logger.episode_rewards):.2f}")
            print(f"Best episode reward: {max(logger.episode_rewards):.2f}")
            if len(logger.episode_net_worths) > 0:
                print(f"Best final net worth: {max(logger.episode_net_worths):.2f}")
        
        print("="*70 + "\n")
        
        # 保存模型
        save_path = os.path.join(MODELS_DIR, args.model_name)
        model.save(save_path)
        print(f"Model saved to {save_path}.zip")
        
        # 保存训练配置
        config_path = os.path.join(MODELS_DIR, f"{args.model_name}_config.txt")
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(f"# {args.rnn_type.upper()} Multi-Stock Training Config\n")
            f.write(f"model_name={args.model_name}\n")
            f.write(f"rnn_type={args.rnn_type}\n")
            f.write(f"hidden_size={args.hidden_size}\n")
            f.write(f"rnn_layers={args.rnn_layers}\n")
            f.write(f"features_dim={args.features_dim}\n")
            f.write(f"bidirectional={args.bidirectional}\n")
            f.write(f"dropout={args.dropout}\n")
            f.write(f"num_stocks={len(stock_codes)}\n")
            f.write(f"timesteps={args.timesteps}\n")
            f.write(f"num_envs={num_envs}\n")
            f.write(f"device={device}\n")
            f.write(f"learning_rate={args.learning_rate}\n")
            f.write(f"ent_coef={args.ent_coef}\n")
            f.write(f"use_maskable_ppo={USE_MASKABLE_PPO}\n")
            f.write(f"resumed_from={args.resume if is_resume else 'None'}\n")
            f.write(f"training_time_seconds={elapsed:.1f}\n")
            f.write(f"average_fps={fps:.0f}\n")
            f.write(f"\n# Stock codes:\n")
            f.write(f"stock_codes={','.join(stock_codes[:100])}\n")
            if len(stock_codes) > 100:
                f.write(f"# ... and {len(stock_codes) - 100} more\n")
        print(f"Config saved to {config_path}")
        
    except Exception as e:
        print(f"An error occurred during training: {e}")
        import traceback
        traceback.print_exc()
    finally:
        env.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    train()

