"""
PPO 训练脚本 V1 - 多环境并行版本

支持：
1. 多环境并行训练（SubprocVecEnv）
2. GPU 加速
3. 可配置的并行环境数
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
    """
    from rl_trading.envs.stock_trading_env import StockTradingEnv
    from stable_baselines3.common.monitor import Monitor
    
    try:
        from sb3_contrib.common.wrappers import ActionMasker
        HAS_ACTION_MASKER = True
    except ImportError:
        HAS_ACTION_MASKER = False
    
    def get_action_masks(env):
        return env.action_masks()
    
    config = _ENV_CONFIG
    
    env = StockTradingEnv(
        stock_code=config['stock_code'],
        data_dir=config['data_dir'],
        buy_rate=config['buy_rate'],
        buy_min=config['buy_min'],
        sell_rate=config['sell_rate'],
        sell_min=config['sell_min'],
        stamp_duty=config['stamp_duty'],
        trade_amount_percent=config['trade_percent'],
    )
    
    if config.get('use_maskable_ppo', False) and HAS_ACTION_MASKER:
        env = ActionMasker(env, get_action_masks)
    
    env = Monitor(env)
    env.reset(seed=env_id * 42)
    
    return env


def make_env_fn(env_id, config):
    """创建环境的工厂函数"""
    def _init():
        global _ENV_CONFIG
        _ENV_CONFIG = config
        return _make_env_subprocess(env_id)
    return _init


class RewardLoggingCallback:
    """训练日志回调"""
    def __init__(self, verbose=0, num_envs=1):
        self.verbose = verbose
        self.num_envs = num_envs
        self.episode_rewards = []
        self.episode_lengths = []
        self.episode_net_worths = []
        self.last_print_step = 0
        self.print_freq = 4096


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
                
                # 打印统计
                if self.num_timesteps - self.stats_logger.last_print_step >= self.stats_logger.print_freq:
                    if len(self.stats_logger.episode_rewards) > 0:
                        mean_reward = np.mean(self.stats_logger.episode_rewards[-10:])
                        mean_length = np.mean(self.stats_logger.episode_lengths[-10:])
                        
                        print(f"\n{'='*60}")
                        print(f"Timestep: {self.num_timesteps} | Parallel Envs: {self.stats_logger.num_envs}")
                        print(f"Episodes completed: {len(self.stats_logger.episode_rewards)}")
                        print(f"Mean episode reward (last 10): {mean_reward:.2f}")
                        print(f"Mean episode length (last 10): {mean_length:.0f}")
                        
                        if len(self.stats_logger.episode_rewards) >= 2:
                            print(f"Min/Max reward: {min(self.stats_logger.episode_rewards[-10:]):.2f} / {max(self.stats_logger.episode_rewards[-10:]):.2f}")
                        
                        if len(self.stats_logger.episode_net_worths) > 0:
                            mean_nw = np.mean(self.stats_logger.episode_net_worths[-10:])
                            print(f"Mean final net worth (last 10): {mean_nw:.2f}")
                        
                        print(f"{'='*60}\n")
                        
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
        print("       Install: pip install sb3-contrib")
    
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
    from stable_baselines3.common.monitor import Monitor
    from rl_trading.envs.stock_trading_env import StockTradingEnv

    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=500000, help="Total training timesteps")
    parser.add_argument("--stock_code", type=str, default="000001", help="Stock code to train on")
    parser.add_argument("--buy_rate", type=float, default=0.0001, help="Buy commission rate")
    parser.add_argument("--buy_min", type=float, default=5.0, help="Buy commission minimum")
    parser.add_argument("--sell_rate", type=float, default=0.0001, help="Sell commission rate")
    parser.add_argument("--sell_min", type=float, default=5.0, help="Sell commission minimum")
    parser.add_argument("--stamp_duty", type=float, default=0.0005, help="Stamp duty rate")
    parser.add_argument("--trade_percent", type=float, default=0.5, help="Percent of balance/shares to trade each time")
    parser.add_argument("--learning_rate", type=float, default=0.0003, help="Learning rate")
    parser.add_argument("--ent_coef", type=float, default=0.01, help="Entropy coefficient")
    parser.add_argument("--resume", action="store_true", help="Resume training from existing model for this stock")
    parser.add_argument("--reset_params", action="store_true", help="Reset model parameters (lr, ent_coef) when resuming")
    
    # 并行参数
    parser.add_argument("--num_envs", type=int, default=4, help="Number of parallel envs")
    parser.add_argument("--device", type=str, default="auto", help="Device: auto/cuda/cpu")
    
    args = parser.parse_args()

    # 设备检测
    if args.device == "auto":
        device, device_name = get_device_info()
    else:
        device = args.device
        device_name = args.device.upper()
    
    num_envs = args.num_envs
    print(f"[INFO] Using {num_envs} parallel environments")

    # Configuration
    STOCK_CODE = args.stock_code
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Training Configuration V1 (Parallel)")
    print(f"{'='*60}")
    print(f"Stock Code: {STOCK_CODE}")
    print(f"Device: {device_name}")
    print(f"Parallel Environments: {num_envs}")
    print(f"Total Timesteps: {args.timesteps}")
    print(f"Trade Percent: {args.trade_percent * 100}%")
    print(f"Learning Rate: {args.learning_rate}")
    print(f"Entropy Coefficient: {args.ent_coef}")
    print(f"Commission: Buy={args.buy_rate}(Min {args.buy_min}), Sell={args.sell_rate}(Min {args.sell_min})")
    print(f"Stamp Duty: {args.stamp_duty}")
    print(f"{'='*60}\n")

    # 环境配置
    env_config = {
        'stock_code': STOCK_CODE,
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
    
    # 创建或加载模型
    model_path = os.path.join(MODELS_DIR, f"ppo_stock_{STOCK_CODE}.zip")
    is_resume = args.resume and os.path.exists(model_path)
    
    if is_resume:
        # 从已有模型继续训练
        print(f"\n{'='*60}")
        print(f"[RESUME] Loading model from: {model_path}")
        print(f"[RESUME] Will continue training for {args.timesteps} more timesteps")
        print(f"{'='*60}\n")
        
        if USE_MASKABLE_PPO:
            model = MaskablePPO.load(
                model_path, 
                env=env, 
                device=device,
                tensorboard_log=tb_log,
            )
            if args.reset_params:
                model.learning_rate = args.learning_rate
                model.ent_coef = args.ent_coef
                print(f"[INFO] Resumed model params RESET - LR: {model.learning_rate}, Ent Coef: {model.ent_coef}")
            else:
                print(f"[INFO] Resumed model params (Keep original) - LR: {model.learning_rate}, Ent Coef: {model.ent_coef}")
        else:
            from stable_baselines3 import PPO
            model = PPO.load(
                model_path, 
                env=env, 
                device=device,
                tensorboard_log=tb_log,
            )
            if args.reset_params:
                model.learning_rate = args.learning_rate
                model.ent_coef = args.ent_coef
                print(f"[INFO] Resumed model params RESET - LR: {model.learning_rate}, Ent Coef: {model.ent_coef}")
            else:
                print(f"[INFO] Resumed model params (Keep original) - LR: {model.learning_rate}, Ent Coef: {model.ent_coef}")
        
        print(f"[OK] Model loaded successfully, resuming training...")
    else:
        # 创建新模型
        print(f"Creating new model on device: {device}")
        
        if USE_MASKABLE_PPO:
            model = MaskablePPO(
                "MlpPolicy", 
                env, 
                verbose=1, 
                tensorboard_log=tb_log,
                learning_rate=args.learning_rate,
                ent_coef=args.ent_coef,
                n_steps=2048,
                batch_size=64,
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
                verbose=1, 
                tensorboard_log=tb_log,
                learning_rate=args.learning_rate,
                ent_coef=args.ent_coef,
                n_steps=2048,
                batch_size=64,
                n_epochs=10,
                gamma=0.99,
                gae_lambda=0.95,
                clip_range=0.2,
                vf_coef=0.5,
                device=device,
            )

    # 训练
    if is_resume:
        print(f"\nResuming training for {args.timesteps} more timesteps...")
    else:
        print(f"\nStarting training for {args.timesteps} timesteps...")
    print(f"Estimated speedup: ~{num_envs}x compared to single env\n")
    
    logger = RewardLoggingCallback(verbose=1, num_envs=num_envs)
    callback_wrapper = ParallelTrainingCallback(logger, verbose=1)
    
    try:
        import time
        start_time = time.time()
        
        model.learn(total_timesteps=args.timesteps, callback=callback_wrapper.get_callback())
        
        elapsed = time.time() - start_time
        fps = args.timesteps / elapsed
        
        print("\n" + "="*60)
        print("Training completed!")
        print(f"Time elapsed: {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        print(f"Average FPS: {fps:.0f}")
        print(f"Total episodes: {len(logger.episode_rewards)}")
        if len(logger.episode_rewards) > 0:
            print(f"Final mean reward (last 10): {np.mean(logger.episode_rewards[-10:]):.2f}")
            print(f"Overall mean reward: {np.mean(logger.episode_rewards):.2f}")
            print(f"Best episode reward: {max(logger.episode_rewards):.2f}")
            if len(logger.episode_net_worths) > 0:
                print(f"Best final net worth: {max(logger.episode_net_worths):.2f}")
        print("="*60 + "\n")
        
        # 保存模型
        save_path = os.path.join(MODELS_DIR, f"ppo_stock_{STOCK_CODE}")
        model.save(save_path)
        print(f"Model saved to {save_path}.zip")
        
        # 保存训练配置
        config_path = os.path.join(MODELS_DIR, f"ppo_stock_{STOCK_CODE}_config.txt")
        with open(config_path, 'w') as f:
            f.write(f"# PPO V1 Training Config (Parallel)\n")
            f.write(f"stock_code={STOCK_CODE}\n")
            f.write(f"timesteps={args.timesteps}\n")
            f.write(f"num_envs={num_envs}\n")
            f.write(f"device={device}\n")
            f.write(f"trade_percent={args.trade_percent}\n")
            f.write(f"learning_rate={args.learning_rate}\n")
            f.write(f"ent_coef={args.ent_coef}\n")
            f.write(f"use_maskable_ppo={USE_MASKABLE_PPO}\n")
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
    multiprocessing.freeze_support()
    train()
