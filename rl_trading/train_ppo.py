import os
import sys
import gymnasium as gym
import numpy as np

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rl_trading.envs.stock_trading_env import StockTradingEnv

import argparse

# 尝试导入 MaskablePPO，如果不可用则回退到普通 PPO
try:
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.wrappers import ActionMasker
    from sb3_contrib.common.maskable.callbacks import MaskableEvalCallback
    USE_MASKABLE_PPO = True
    print("[OK] Using MaskablePPO (Action Masking enabled)")
except ImportError:
    from stable_baselines3 import PPO
    USE_MASKABLE_PPO = False
    print("[WARN] sb3_contrib not installed, using standard PPO (no Action Masking)")
    print("       Install: pip install sb3-contrib")

from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback


def get_action_masks(env):
    """获取环境的 action masks（用于 ActionMasker wrapper）"""
    return env.action_masks()


class RewardLoggingCallback(BaseCallback):
    """
    自定义回调，记录训练过程中的 episode 奖励和其他有用指标
    """
    def __init__(self, verbose=0):
        super(RewardLoggingCallback, self).__init__(verbose)
        self.episode_rewards = []
        self.episode_lengths = []
        self.episode_net_worths = []
        self.last_print_step = 0
        self.print_freq = 2048
        
    def _on_step(self) -> bool:
        if self.locals.get("dones") is not None:
            for i, done in enumerate(self.locals["dones"]):
                if done:
                    if "infos" in self.locals and len(self.locals["infos"]) > i:
                        info = self.locals["infos"][i]
                        if "episode" in info:
                            ep_rew = info["episode"]["r"]
                            ep_len = info["episode"]["l"]
                            self.episode_rewards.append(ep_rew)
                            self.episode_lengths.append(ep_len)
                        # 记录最终净值
                        if "net_worth" in info:
                            self.episode_net_worths.append(info["net_worth"])
        
        if self.num_timesteps - self.last_print_step >= self.print_freq and len(self.episode_rewards) > 0:
            mean_reward = np.mean(self.episode_rewards[-10:])
            mean_length = np.mean(self.episode_lengths[-10:])
            
            print(f"\n{'='*60}")
            print(f"Timestep: {self.num_timesteps}")
            print(f"Episodes completed: {len(self.episode_rewards)}")
            print(f"Mean episode reward (last 10): {mean_reward:.2f}")
            print(f"Mean episode length (last 10): {mean_length:.0f}")
            if len(self.episode_rewards) >= 2:
                print(f"Min/Max episode reward: {min(self.episode_rewards[-10:]):.2f} / {max(self.episode_rewards[-10:]):.2f}")
            if len(self.episode_net_worths) > 0:
                mean_net_worth = np.mean(self.episode_net_worths[-10:])
                print(f"Mean final net worth (last 10): {mean_net_worth:.2f}")
            print(f"{'='*60}\n")
            
            self.last_print_step = self.num_timesteps
            
        return True


def train():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=500000, help="Total training timesteps (increased default)")
    parser.add_argument("--stock_code", type=str, default="000001", help="Stock code to train on")
    parser.add_argument("--buy_rate", type=float, default=0.0001, help="Buy commission rate")
    parser.add_argument("--buy_min", type=float, default=5.0, help="Buy commission minimum")
    parser.add_argument("--sell_rate", type=float, default=0.0001, help="Sell commission rate")
    parser.add_argument("--sell_min", type=float, default=5.0, help="Sell commission minimum")
    parser.add_argument("--stamp_duty", type=float, default=0.0005, help="Stamp duty rate")
    parser.add_argument("--trade_percent", type=float, default=0.5, help="Percent of balance/shares to trade each time")
    parser.add_argument("--learning_rate", type=float, default=0.0003, help="Learning rate")
    parser.add_argument("--ent_coef", type=float, default=0.01, help="Entropy coefficient (lower = less exploration)")
    args = parser.parse_args()

    # Configuration
    STOCK_CODE = args.stock_code
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Training Configuration")
    print(f"{'='*60}")
    print(f"Stock Code: {STOCK_CODE}")
    print(f"Total Timesteps: {args.timesteps}")
    print(f"Trade Percent: {args.trade_percent * 100}%")
    print(f"Learning Rate: {args.learning_rate}")
    print(f"Entropy Coefficient: {args.ent_coef}")
    print(f"Commission: Buy={args.buy_rate}(Min {args.buy_min}), Sell={args.sell_rate}(Min {args.sell_min})")
    print(f"Stamp Duty: {args.stamp_duty}")
    print(f"Data Directory: {DATA_DIR}")
    print(f"{'='*60}\n")

    # 创建环境
    def make_env():
        env = StockTradingEnv(
            stock_code=STOCK_CODE, 
            data_dir=DATA_DIR,
            buy_rate=args.buy_rate,
            buy_min=args.buy_min,
            sell_rate=args.sell_rate,
            sell_min=args.sell_min,
            stamp_duty=args.stamp_duty,
            trade_amount_percent=args.trade_percent
        )
        
        if USE_MASKABLE_PPO:
            # 使用 ActionMasker 包装环境
            env = ActionMasker(env, get_action_masks)
        
        env = Monitor(env)
        return env
    
    # 向量化环境
    env = DummyVecEnv([make_env])

    # 初始化模型
    try:
        import tensorboard
        tb_log = LOG_DIR
    except ImportError:
        print("Tensorboard not installed, disabling tensorboard logging")
        tb_log = None
    
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
        )

    # 训练
    print(f"Starting training for {args.timesteps} timesteps...")
    print(f"Training progress will be printed every ~2048 steps.\n")
    
    reward_callback = RewardLoggingCallback(verbose=1)
    
    try:
        model.learn(total_timesteps=args.timesteps, callback=reward_callback)
        
        print("\n" + "="*60)
        print("Training completed!")
        print(f"Total episodes: {len(reward_callback.episode_rewards)}")
        if len(reward_callback.episode_rewards) > 0:
            print(f"Final mean reward (last 10 episodes): {np.mean(reward_callback.episode_rewards[-10:]):.2f}")
            print(f"Overall mean reward: {np.mean(reward_callback.episode_rewards):.2f}")
            print(f"Best episode reward: {max(reward_callback.episode_rewards):.2f}")
            if len(reward_callback.episode_net_worths) > 0:
                print(f"Best final net worth: {max(reward_callback.episode_net_worths):.2f}")
        print("="*60 + "\n")
        
        # 保存模型
        save_path = os.path.join(MODELS_DIR, f"ppo_stock_{STOCK_CODE}")
        model.save(save_path)
        print(f"Model saved to {save_path}.zip")
        
        # 保存训练配置
        config_path = os.path.join(MODELS_DIR, f"ppo_stock_{STOCK_CODE}_config.txt")
        with open(config_path, 'w') as f:
            f.write(f"stock_code={STOCK_CODE}\n")
            f.write(f"timesteps={args.timesteps}\n")
            f.write(f"trade_percent={args.trade_percent}\n")
            f.write(f"learning_rate={args.learning_rate}\n")
            f.write(f"ent_coef={args.ent_coef}\n")
            f.write(f"use_maskable_ppo={USE_MASKABLE_PPO}\n")
        print(f"Config saved to {config_path}")
        
    except Exception as e:
        print(f"An error occurred during training: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    train()
