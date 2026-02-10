"""
Configuration Settings
"""
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # App
    APP_NAME: str = "AI Yield Rebalancer"
    DEBUG: bool = True
    
    # Blockchain
    RPC_URL: str = "https://eth-mainnet.g.alchemy.com/v2/your-api-key"
    CHAIN_ID: int = 1
    
    # Risk
    MAX_SLIPPAGE: float = 0.005  # 0.5%
    MIN_YIELD_DIFF: float = 0.02 # 2% APY improvement required
    GAS_LIMIT_MULTIPLIER: float = 1.2
    
    # AI Logic
    MEAN_REVERSION_THRESHOLD: float = 0.75  # Confidence score
    VAMPIRE_ATTACK_THRESHOLD: float = 0.1   # 10% of pool liquidity
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
