"""
PPO 训练脚本 V2 - 多环境并行版本

主要改进：
1. 支持多环境并行训练（SubprocVecEnv）
2. 支持 GPU 加速
3. 自动检测最优并行数
4. 训练速度提升 2-4 倍
"""
import os
import sys
import multiprocessing
import gymnasium as gym
import numpy as np
import torch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse

# 全局配置变量（用于子进程）
_ENV_CONFIG = {}


def _make_env_subprocess(env_id):
    """
    子进程中创建环境的函数
    注意：这个函数需要在模块级别定义，以便 pickle 序列化
    """
    from rl_trading.envs.stock_trading_env_v2 import StockTradingEnvV2
    from stable_baselines3.common.monitor import Monitor
    
    # 尝试导入 ActionMasker
    try:
        from sb3_contrib.common.wrappers import ActionMasker
        HAS_ACTION_MASKER = True
    except ImportError:
        HAS_ACTION_MASKER = False
    
    def get_action_masks(env):
        return env.action_masks()
    
    config = _ENV_CONFIG
    
    env = StockTradingEnvV2(
        stock_code=config['stock_code'],
        data_dir=config['data_dir'],
        buy_rate=config['buy_rate'],
        buy_min=config['buy_min'],
        sell_rate=config['sell_rate'],
        sell_min=config['sell_min'],
        stamp_duty=config['stamp_duty'],
        trading_cooldown=config['trading_cooldown'],
        drawdown_penalty_threshold=config['drawdown_threshold'],
    )
    
    if config.get('use_maskable_ppo', False) and HAS_ACTION_MASKER:
        env = ActionMasker(env, get_action_masks)
    
    env = Monitor(env)
    env.reset(seed=env_id * 42)  # 不同环境使用不同种子
    
    return env


def make_env_fn(env_id, config):
    """
    创建环境的工厂函数
    """
    def _init():
        global _ENV_CONFIG
        _ENV_CONFIG = config
        return _make_env_subprocess(env_id)
    return _init


class DetailedLoggingCallback:
    """
    详细日志回调，监控训练过程中的关键指标
    """
    def __init__(self, verbose=0, num_envs=1):
        from stable_baselines3.common.callbacks import BaseCallback
        self.base_callback = BaseCallback
        self.verbose = verbose
        self.num_envs = num_envs
        self.episode_rewards = []
        self.episode_lengths = []
        self.episode_net_worths = []
        self.episode_fees = []
        self.last_print_step = 0
        self.print_freq = 8192  # 增加打印频率以适应多环境
        self.num_timesteps = 0
        
    def _on_step(self, locals_dict) -> bool:
        self.num_timesteps = locals_dict.get("self", {}).num_timesteps if hasattr(locals_dict.get("self", {}), 'num_timesteps') else 0
        
        if locals_dict.get("dones") is not None:
            for i, done in enumerate(locals_dict["dones"]):
                if done:
                    if "infos" in locals_dict and len(locals_dict["infos"]) > i:
                        info = locals_dict["infos"][i]
                        if "episode" in info:
                            ep_rew = info["episode"]["r"]
                            ep_len = info["episode"]["l"]
                            self.episode_rewards.append(ep_rew)
                            self.episode_lengths.append(ep_len)
                        
                        if "net_worth" in info:
                            self.episode_net_worths.append(info["net_worth"])
                        if "total_fees" in info:
                            self.episode_fees.append(info["total_fees"])
        
        return True
    
    def print_stats(self, timesteps):
        if timesteps - self.last_print_step >= self.print_freq and len(self.episode_rewards) > 0:
            mean_reward = np.mean(self.episode_rewards[-10:])
            mean_length = np.mean(self.episode_lengths[-10:])
            
            print(f"\n{'='*70}")
            print(f"Timestep: {timesteps} | Parallel Envs: {self.num_envs}")
            print(f"Episodes completed: {len(self.episode_rewards)}")
            print(f"Mean episode reward (last 10): {mean_reward:.2f}")
            print(f"Mean episode length (last 10): {mean_length:.0f}")
            
            if len(self.episode_rewards) >= 2:
                print(f"Min/Max episode reward: {min(self.episode_rewards[-10:]):.2f} / {max(self.episode_rewards[-10:]):.2f}")
            
            if len(self.episode_net_worths) > 0:
                mean_net_worth = np.mean(self.episode_net_worths[-10:])
                max_net_worth = max(self.episode_net_worths[-10:])
                print(f"Mean final net worth (last 10): {mean_net_worth:.2f}")
                print(f"Best net worth (last 10): {max_net_worth:.2f}")
            
            if len(self.episode_fees) > 0:
                mean_fees = np.mean(self.episode_fees[-10:])
                print(f"Mean total fees (last 10): {mean_fees:.2f}")
            
            print(f"{'='*70}\n")
            
            self.last_print_step = timesteps


class ParallelTrainingCallback:
    """
    用于多环境并行训练的回调
    """
    def __init__(self, stats_logger, verbose=0):
        from stable_baselines3.common.callbacks import BaseCallback
        
        # 保存对 stats_logger 的引用
        _stats_logger = stats_logger
        
        class _Callback(BaseCallback):
            def __init__(self, verbose=0):
                super().__init__(verbose)
                self.stats_logger = _stats_logger  # 使用闭包引用
                
            def _on_step(self) -> bool:
                self.stats_logger._on_step(self.locals)
                self.stats_logger.print_stats(self.num_timesteps)
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


def get_optimal_num_envs():
    """获取最优并行环境数量"""
    cpu_count = multiprocessing.cpu_count()
    # 建议使用 CPU 核心数的一半到全部
    optimal = max(2, min(cpu_count - 1, 8))
    return optimal


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
        print("       Install: pip install sb3-contrib")
    
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
    from stable_baselines3.common.monitor import Monitor
    from rl_trading.envs.stock_trading_env_v2 import StockTradingEnvV2

    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=1000000, help="Total training timesteps")
    parser.add_argument("--stock_code", type=str, default="000001", help="Stock code to train on")
    parser.add_argument("--buy_rate", type=float, default=0.0001, help="Buy commission rate")
    parser.add_argument("--buy_min", type=float, default=5.0, help="Buy commission minimum")
    parser.add_argument("--sell_rate", type=float, default=0.0001, help="Sell commission rate")
    parser.add_argument("--sell_min", type=float, default=5.0, help="Sell commission minimum")
    parser.add_argument("--stamp_duty", type=float, default=0.0005, help="Stamp duty rate")
    
    # === 训练参数 ===
    parser.add_argument("--learning_rate", type=float, default=0.0003, help="Learning rate")
    parser.add_argument("--ent_coef", type=float, default=0.05, help="Entropy coefficient (higher = more exploration)")
    parser.add_argument("--n_steps", type=int, default=2048, help="Steps per update per env")
    parser.add_argument("--batch_size", type=int, default=64, help="Batch size")
    parser.add_argument("--gamma", type=float, default=0.99, help="Discount factor")
    parser.add_argument("--gae_lambda", type=float, default=0.95, help="GAE lambda")
    
    # === 并行参数 ===
    parser.add_argument("--num_envs", type=int, default=4, help="Number of parallel envs")
    parser.add_argument("--device", type=str, default="auto", help="Device: auto/cuda/cpu")
    
    # === 风控参数 ===
    parser.add_argument("--trading_cooldown", type=int, default=5, help="Min days between trades")
    parser.add_argument("--drawdown_threshold", type=float, default=0.1, help="Drawdown penalty threshold")
    
    args = parser.parse_args()

    # 设备检测
    if args.device == "auto":
        device, device_name = get_device_info()
    else:
        device = args.device
        device_name = args.device.upper()
    
    # 并行环境数量
    if args.num_envs <= 0:
        num_envs = get_optimal_num_envs()
        print(f"[INFO] Auto-detected optimal num_envs: {num_envs}")
    else:
        num_envs = args.num_envs
        print(f"[INFO] Using {num_envs} parallel environments")
    
    # Configuration
    STOCK_CODE = args.stock_code
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"Training Configuration V2 (Parallel)")
    print(f"{'='*70}")
    print(f"Stock Code: {STOCK_CODE}")
    print(f"Device: {device_name}")
    print(f"Parallel Environments: {num_envs}")
    print(f"Total Timesteps: {args.timesteps}")
    print(f"Effective Steps/Update: {args.n_steps * num_envs}")
    print(f"Learning Rate: {args.learning_rate}")
    print(f"Entropy Coefficient: {args.ent_coef}")
    print(f"N Steps (per env): {args.n_steps}")
    print(f"Batch Size: {args.batch_size}")
    print(f"Gamma (Discount): {args.gamma}")
    print(f"GAE Lambda: {args.gae_lambda}")
    print(f"Trading Cooldown: {args.trading_cooldown} days")
    print(f"Drawdown Threshold: {args.drawdown_threshold * 100}%")
    print(f"Commission: Buy={args.buy_rate}(Min {args.buy_min}), Sell={args.sell_rate}(Min {args.sell_min})")
    print(f"{'='*70}\n")

    # 环境配置
    env_config = {
        'stock_code': STOCK_CODE,
        'data_dir': DATA_DIR,
        'buy_rate': args.buy_rate,
        'buy_min': args.buy_min,
        'sell_rate': args.sell_rate,
        'sell_min': args.sell_min,
        'stamp_duty': args.stamp_duty,
        'trading_cooldown': args.trading_cooldown,
        'drawdown_threshold': args.drawdown_threshold,
        'use_maskable_ppo': USE_MASKABLE_PPO,
    }
    
    # 更新全局配置
    global _ENV_CONFIG
    _ENV_CONFIG = env_config

    # 创建并行环境
    if num_envs > 1:
        print(f"Creating {num_envs} parallel environments (SubprocVecEnv)...")
        try:
            env = SubprocVecEnv([make_env_fn(i, env_config) for i in range(num_envs)])
            print(f"[OK] SubprocVecEnv created successfully")
        except Exception as e:
            print(f"[WARN] SubprocVecEnv failed ({e}), falling back to DummyVecEnv")
            env = DummyVecEnv([make_env_fn(i, env_config) for i in range(num_envs)])
    else:
        print("Creating single environment (DummyVecEnv)...")
        env = DummyVecEnv([make_env_fn(0, env_config)])

    # Tensorboard
    try:
        import tensorboard
        tb_log = LOG_DIR
    except ImportError:
        print("Tensorboard not installed, disabling tensorboard logging")
        tb_log = None
    
    # 网络结构
    policy_kwargs = dict(
        net_arch=dict(
            pi=[256, 256, 128],
            vf=[256, 256, 128]
        )
    )
    
    # 创建模型
    print(f"Creating model on device: {device}")
    
    if USE_MASKABLE_PPO:
        model = MaskablePPO(
            "MlpPolicy", 
            env, 
            verbose=1, 
            tensorboard_log=tb_log,
            learning_rate=args.learning_rate,
            ent_coef=args.ent_coef,
            n_steps=args.n_steps,
            batch_size=args.batch_size,
            n_epochs=10,
            gamma=args.gamma,
            gae_lambda=args.gae_lambda,
            clip_range=0.2,
            vf_coef=0.5,
            max_grad_norm=0.5,
            policy_kwargs=policy_kwargs,
            device=device,
        )
    else:
        from stable_baselines3 import PPO
        model = PPO(
            "MlpPolicy", 
            env, 
            verbose=1, 
            tensorboard_log=tb_log,
            learning_rate=args.learning_rate,
            ent_coef=args.ent_coef,
            n_steps=args.n_steps,
            batch_size=args.batch_size,
            n_epochs=10,
            gamma=args.gamma,
            gae_lambda=args.gae_lambda,
            clip_range=0.2,
            vf_coef=0.5,
            max_grad_norm=0.5,
            policy_kwargs=policy_kwargs,
            device=device,
        )

    # 训练
    print(f"\nStarting training for {args.timesteps} timesteps...")
    print(f"Action Space: 7 actions (Hold, Buy 25/50/100%, Sell 25/50/100%)")
    print(f"Estimated speedup: ~{num_envs}x compared to single env\n")
    
    logger = DetailedLoggingCallback(verbose=1, num_envs=num_envs)
    callback_wrapper = ParallelTrainingCallback(logger, verbose=1)
    
    try:
        import time
        start_time = time.time()
        
        model.learn(total_timesteps=args.timesteps, callback=callback_wrapper.get_callback())
        
        elapsed = time.time() - start_time
        fps = args.timesteps / elapsed
        
        print("\n" + "="*70)
        print("Training completed!")
        print(f"Time elapsed: {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        print(f"Average FPS: {fps:.0f}")
        print(f"Episodes completed: {len(logger.episode_rewards)}")
        if len(logger.episode_rewards) > 0:
            print(f"Final mean reward (last 10 episodes): {np.mean(logger.episode_rewards[-10:]):.2f}")
            print(f"Overall mean reward: {np.mean(logger.episode_rewards):.2f}")
            print(f"Best episode reward: {max(logger.episode_rewards):.2f}")
            if len(logger.episode_net_worths) > 0:
                print(f"Best final net worth: {max(logger.episode_net_worths):.2f}")
        print("="*70 + "\n")
        
        # 保存模型
        save_path = os.path.join(MODELS_DIR, f"ppo_stock_{STOCK_CODE}_v2")
        model.save(save_path)
        print(f"Model saved to {save_path}.zip")
        
        # 保存训练配置
        config_path = os.path.join(MODELS_DIR, f"ppo_stock_{STOCK_CODE}_v2_config.txt")
        with open(config_path, 'w') as f:
            f.write(f"# PPO V2 Training Config (Parallel)\n")
            f.write(f"stock_code={STOCK_CODE}\n")
            f.write(f"timesteps={args.timesteps}\n")
            f.write(f"num_envs={num_envs}\n")
            f.write(f"device={device}\n")
            f.write(f"learning_rate={args.learning_rate}\n")
            f.write(f"ent_coef={args.ent_coef}\n")
            f.write(f"n_steps={args.n_steps}\n")
            f.write(f"batch_size={args.batch_size}\n")
            f.write(f"gamma={args.gamma}\n")
            f.write(f"gae_lambda={args.gae_lambda}\n")
            f.write(f"trading_cooldown={args.trading_cooldown}\n")
            f.write(f"drawdown_threshold={args.drawdown_threshold}\n")
            f.write(f"use_maskable_ppo={USE_MASKABLE_PPO}\n")
            f.write(f"action_space=7\n")
            f.write(f"training_time_seconds={elapsed:.1f}\n")
            f.write(f"average_fps={fps:.0f}\n")
        print(f"Config saved to {config_path}")
        
    except Exception as e:
        print(f"An error occurred during training: {e}")
        import traceback
        traceback.print_exc()
    finally:
        env.close()


if __name__ == "__main__":
    # Windows 多进程需要这个保护
    multiprocessing.freeze_support()
    train()
