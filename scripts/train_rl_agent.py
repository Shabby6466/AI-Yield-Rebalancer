import logging
import os
import numpy as np
from src.ml.rl_agent import PPORebalancer, DefiRebalanceEnv
from stable_baselines3.common.callbacks import CheckpointCallback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generate_synthetic_training_data(n_steps=10000, n_pools=5):
    """
    Generates realistic synthetic DeFi data for training.
    Includes:
    - Yield cycles (sine waves)
    - Risk spikes
    - TVL shifts
    """
    logger.info(f"Generating {n_steps} steps of synthetic training data...")
    
    data = []
    for t in range(n_steps):
        # 32 features per pool
        obs = np.random.normal(0, 0.1, (n_pools, 32)).astype(np.float32)
        
        for p in range(n_pools):
            # 1. Base APY ( Sine wave + Noise)
            base_apy = 5 + 5 * np.sin(t / 100 + p) + np.random.normal(0, 1)
            obs[p, 0] = max(0, base_apy)
            
            # 2. TVL (Log scale)
            obs[p, 4] = 15 + np.sin(t / 500) # $3M - $30M proxy
            
            # 3. Risk Score (Sudden spikes)
            risk = 10 + 10 * np.sin(t / 1000)
            if np.random.random() < 0.01: # 1% chance of risk spike
                risk = 80
            obs[p, 11] = risk
            
            # 4. Gas Prices (Market-wide)
            obs[p, 16] = 50 + 40 * np.sin(t / 50)
            
        data.append(obs)
        
    return data

def train():
    # 1. Prepare Environment
    training_data = generate_synthetic_training_data(n_steps=20000)
    env = DefiRebalanceEnv(pool_data=training_data)
    
    # 2. Initialize Agent
    agent = PPORebalancer(env)
    
    # 3. Setup Callbacks
    checkpoint_callback = CheckpointCallback(
        save_freq=5000,
        save_path="./models/checkpoints/",
        name_prefix="ppo_defi_model"
    )
    
    # 4. Train
    logger.info("Starting Training Session...")
    agent.train(total_timesteps=100000) # Increased for better convergence
    
    # 5. Save Final Model
    os.makedirs("models", exist_ok=True)
    agent.save("models/ppo_rebalancer_v1.zip")
    logger.info("✅ Model trained and saved to models/ppo_rebalancer_v1.zip")

if __name__ == "__main__":
    train()
