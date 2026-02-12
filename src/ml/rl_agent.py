import gymnasium as gym
from gymnasium import spaces
import numpy as np
import torch as th
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
import logging
from typing import Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

class DefiRebalanceEnv(gym.Env):
    """
    Gymnasium Environment for DeFi Yield Rebalancing.
    
    Observation Space: 32-dimensional feature vector per pool (N pools)
    Action Space: N-dimensional vector (allocation weights, sums to 1)
    """
    def __init__(self, 
                 pool_data: List[Dict], 
                 initial_capital: float = 100000,
                 max_pools: int = 5,
                 risk_tolerance: float = 1.0): # 1.0 = Balanced, 0.1 = Aggressive, 10.0 = Conservative
        super(DefiRebalanceEnv, self).__init__()
        
        self.max_pools = max_pools
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.risk_tolerance = risk_tolerance
        
        # Action space: Allocation weights for each pool (0-1)
        # Using Box space for continuous allocation weights
        self.action_space = spaces.Box(
            low=0, high=1, 
            shape=(self.max_pools,), 
            dtype=np.float32
        )
        
        # Observation space: 32 features per pool
        # This includes APY, TVL, Volatility, Risk Score, etc.
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, 
            shape=(self.max_pools, 32), 
            dtype=np.float32
        )
        
        self.state = None
        self.current_step = 0
        self.pool_data = pool_data # This would ideally be loaded from TimescaleDB
        
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        self.current_capital = self.initial_capital
        
        # Initialize state with features from the first timestamp
        self.state = self._get_observation(self.current_step)
        
        return self.state, {}

    def step(self, action):
        """
        Execute one rebalancing action.
        
        Action: Vector of weights [w1, w2, ..., wN]
        Reward: Yield Gain - (Gas + Slippage) - (Risk Penalty)
        """
        # Normalize weights to sum to 1
        weights = action / (np.sum(action) + 1e-8)
        
        # Get next state data
        self.current_step += 1
        next_obs = self._get_observation(self.current_step)
        
        # Calculate Reward
        reward = self._calculate_reward(weights, next_obs)
        
        # Update capital based on weighted APYs
        current_apy_vector = next_obs[:, 0] # First feature is current_apy
        profit = np.sum(weights * current_apy_vector / 36500 * self.current_capital)
        self.current_capital += profit
        
        # Check if done
        terminated = self.current_step >= len(self.pool_data) - 1
        truncated = False
        
        self.state = next_obs
        
        return self.state, float(reward), terminated, truncated, {"capital": self.current_capital}

    def _get_observation(self, step_idx: int) -> np.ndarray:
        """Fetch features for the current step."""
        # In a real scenario, this fetches 32 features for each of the top N pools
        # Placeholder: Generate random observations if pool_data is empty
        if not self.pool_data:
            return np.random.randn(self.max_pools, 32).astype(np.float32)
        
        # Real implementation would map the feature engineering output here
        return self.pool_data[step_idx]

    def _calculate_reward(self, weights: np.ndarray, obs: np.ndarray) -> float:
        """
        Reward Function:
        Profit - (Gas Usage * Gas Price) - (Slippage) - (Risk Penalty)
        """
        apys = obs[:, 0]
        risk_scores = obs[:, 11] # Assuming feature index 11 is Risk Score (0-100)
        
        # 1. Total Portfolio APY
        predicted_yield = np.sum(weights * apys)
        
        # 2. Risk Penalty (Scaled by risk_tolerance)
        # Using squared penalty to avoid "chasing yield" in dangerous pools
        # self.risk_tolerance: higher = more penalty (Conservative)
        risk_penalty = np.sum(weights * (risk_scores / 100) ** 2) * 10.0 * self.risk_tolerance
        
        # 3. Churn Penalty (Gas cost approximation)
        # If weights changed significantly, apply a penalty
        if self.state is not None:
            # Simple placeholder: penalize distance from prev weights
            # In a real env, we'd track prev_weights specifically
            churn_penalty = 0.5 # Proxy for gas cost relative to daily yield
        else:
            churn_penalty = 0
            
        # 4. Diversification Bonus (Small bonus for not being 100% in one pool)
        # Entropy-based bonus
        diversification_bonus = -np.sum(weights * np.log(weights + 1e-8)) * 0.1
        
        reward = predicted_yield - risk_penalty - churn_penalty + diversification_bonus
        return reward

class PPORebalancer:
    """Wrapper for the PPO RL Agent training and inference."""
    def __init__(self, env: gym.Env):
        self.env = env
        self.model = PPO(
            "MlpPolicy", 
            env, 
            verbose=1,
            learning_rate=3e-4,
            n_steps=2048,
            batch_size=64,
            gamma=0.99,
            device="auto"
        )
        
    def train(self, total_timesteps: int = 10000):
        logger.info(f"Starting PPO Agent training for {total_timesteps} steps...")
        self.model.learn(total_timesteps=total_timesteps)
        logger.info("Training complete.")
        
    def save(self, path: str):
        self.model.save(path)
        
    def load(self, path: str):
        self.model = PPO.load(path, env=self.env)
        
    def predict(self, observation: np.ndarray) -> np.ndarray:
        action, _ = self.model.predict(observation, deterministic=True)
        # Normalize output weights
        return action / (np.sum(action) + 1e-8)

if __name__ == "__main__":
    # Test environment with dummy data
    dummy_data = [np.random.randn(5, 32).astype(np.float32) for _ in range(100)]
    env = DefiRebalanceEnv(pool_data=dummy_data)
    
    agent = PPORebalancer(env)
    agent.train(total_timesteps=5000)
    
    # Test inference
    obs, _ = env.reset()
    action = agent.predict(obs)
    print(f"Optimal Allocation Weights: {action}")
