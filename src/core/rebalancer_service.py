import logging
import asyncio
from typing import List, Dict, Any
from web3 import Web3
from eth_account import Account
from src.ml.rl_agent import PPORebalancer, DefiRebalanceEnv
from src.risk.circuit_breaker import CircuitBreaker
from src.execution.flashbots_relay import FlashbotsRelayer
from src.data.dune_client import DuneClient
from src.data.defillama_client import DefiLlamaClient
from src.risk.slippage_client import SlippageClient
from src.backtest.prediction_tracker import PredictionTracker
import numpy as np
import os
from dotenv import load_dotenv

# Configure logging to both console and file for dashboard tailing
log_file = "data/rebalancer.log"
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class RebalancerService:
    """
    The Orchestrator. Coordinates the 4-layer system:
    1. Data Ingestion (DeFiLlama/Dune/RPC)
    2. Inference (PPO RL Agent)
    3. Safety Checks (Circuit Breaker)
    4. Execution (Flashbots)
    """
    
    def __init__(self):
        load_dotenv()
        self.w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
        self.signer = Account.from_key(os.getenv("KEEPER_PRIVATE_KEY"))
        
        # Initialize Layers
        self.guard = CircuitBreaker(self.w3, os.getenv("STRATEGY_HUB_ADDRESS"))
        self.hands = FlashbotsRelayer(self.w3, self.signer)
        self.dune = DuneClient(os.getenv("DUNE_API_KEY"))
        self.defillama = DefiLlamaClient()
        self.slippage = SlippageClient()
        
        # Load RL Agent (The Brain) with Risk Preference
        self.risk_tolerance = float(os.getenv("RISK_TOLERANCE", 1.0))
        logger.info(f"Using Risk Tolerance: {self.risk_tolerance} (1.0=Balanced, <1.0=Aggressive)")
        
        self.env = DefiRebalanceEnv(pool_data=[], risk_tolerance=self.risk_tolerance) 
        self.brain = PPORebalancer(self.env)
        if os.path.exists("models/ppo_rebalancer_v1.zip"):
            self.brain.load("models/ppo_rebalancer_v1.zip")
            
        self.tracker = PredictionTracker()
            
    async def run_cycle(self):
        """Execute one rebalancing cycle."""
        logger.info("--- Starting Rebalancing Cycle ---")
        
        # 1. Layer 1: Data Ingestion & Market Health
        if not self.guard.check_market_health():
            logger.warning("Market health check failed. Skipping cycle.")
            return

        # 2. Layer 1/2: Ingest data for inference
        # In a real cycle, we'd pull the latest features from TimescaleDB
        latest_features = await self._fetch_latest_market_state()
        
        # 3. Layer 2: Brain Inference
        # Get optimal weights from RL Agent
        weights = self.brain.predict(latest_features)
        logger.info(f"AI Decision Weights: {weights}")
        
        # Record prediction for dashboard
        self._record_cycle_prediction(weights, latest_features)
        
        # 4. Layer 3: Risk Assessment (Slippage/Liquidity)
        # Check if the proposed moves are safe
        rebalance_txs = self._construct_rebalance_txs(weights)
        if not rebalance_txs:
            logger.info("No rebalancing required (Current == Target).")
            return

        # NEW: Check slippage for the proposed rebalance
        # Assuming we're rebalancing $100k for the POC
        estimated_slippage = await self.slippage.get_expected_slippage(
            "USDC", "USDT", 100000 
        )
        if not self.slippage.is_safe_to_rebalance(estimated_slippage):
            logger.warning(f"Slippage too high ({estimated_slippage:.2%}). Aborting rebalance.")
            return
            
        # 5. Layer 4: Execution
        if os.getenv("NETWORK") == "local":
            logger.info("Local network detected. Sending transactions directly...")
            success = self._send_direct_bundle(rebalance_txs)
        else:
            current_block = self.w3.eth.block_number
            success = self.hands.send_rebalance_bundle(rebalance_txs, current_block + 1)
        
        if success:
            logger.info("✅ Rebalancing bundle successfully executed.")
        else:
            logger.error("❌ Rebalancing bundle failed.")

    def _record_cycle_prediction(self, weights: np.ndarray, features: np.ndarray):
        """Log the cycle's decision to the PredictionTracker."""
        try:
            # For multi-pool, we track the top allocated target vs current
            # Simplify for POC: compare top weight vs others
            top_idx = np.argmax(weights)
            prediction_type = "REBALANCE" if weights[top_idx] > 0.5 else "HOLD"
            
            self.tracker.record_prediction(
                prediction_type=prediction_type,
                current_pool_id="portfolio_current",
                current_pool_apy=float(np.mean(features[:, 0])), # Avg APY of pools
                target_pool_id=f"pool_{top_idx}",
                target_pool_apy=float(features[top_idx, 0]),
                confidence=float(weights[top_idx]),
                reason=f"Risk Tolerance: {self.risk_tolerance}, Top Allocation: {weights[top_idx]:.2%}",
                capital_usd=100000.0 # Standard test capital
            )
        except Exception as e:
            logger.error(f"Failed to record prediction: {e}")

    def _send_direct_bundle(self, bundle: List[Dict]) -> bool:
        """Submit transactions directly for local testing."""
        try:
            for tx in bundle:
                signed_tx = self.w3.eth.account.sign_transaction(tx, self.signer.key)
                tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                logger.info(f"Direct TX Sent: {tx_hash.hex()}")
                # Wait for receipt on local node
                self.w3.eth.wait_for_transaction_receipt(tx_hash)
            return True
        except Exception as e:
            logger.error(f"Direct execution failed: {e}")
            return False

    async def _fetch_latest_market_state(self) -> Any:
        """Fetch latest pool data from DeFiLlama."""
        logger.info("Fetching latest market state from DeFiLlama...")
        top_pools = await self.defillama.fetch_top_pools(limit=self.env.max_pools)
        
        # In a real scenario, we'd convert these pools into the 32-dim feature vector
        # For now, we simulate the feature extraction from the DeFiLlama response
        features = []
        for pool in top_pools:
            # Prototype feature vector mapping (simplified)
            pool_feat = np.zeros(32, dtype=np.float32)
            pool_feat[0] = pool.get('apy', 0)
            pool_feat[4] = np.log1p(pool.get('tvlUsd', 0))
            # ... fill other features ...
            features.append(pool_feat)
            
        # Ensure we have exactly max_pools
        while len(features) < self.env.max_pools:
            features.append(np.zeros(32, dtype=np.float32))
            
        return np.array(features).reshape(self.env.max_pools, 32)

    def _construct_rebalance_txs(self, target_weights: List[float]) -> List[Dict]:
        """
        Calculates the difference between current and target portfolio 
        and generates transaction data for StrategyHub.
        """
        logger.info("Constructing rebalancing transactions...")
        # In a real scenario, we'd fetch current positions from the Vault contract
        # Here we simulate a rebalance from cash or existing pools
        
        # Prototype: Rebalance 10% of portfolio to the top pool
        if target_weights[0] > 0.5: # If AI wants >50% in the top pool
            rebalance_tx = {
                'to': os.getenv("STRATEGY_HUB_ADDRESS"),
                'value': 0,
                'data': '0x', # Placeholder for StrategyHub.rebalance(poolId, amount)
                'gas': 300000,
                'maxFeePerGas': self.w3.eth.gas_price * 2,
                'maxPriorityFeePerGas': self.w3.to_wei(2, 'gwei'),
                'nonce': self.w3.eth.get_transaction_count(self.signer.address),
                'chainId': self.w3.eth.chain_id
            }
            return [rebalance_tx]
            
        return []

    async def start(self):
        """Continuously run the rebalancer."""
        while True:
            try:
                await self.run_cycle()
            except Exception as e:
                logger.error(f"Error in rebalancer cycle: {e}")
            
            # Wait for cooldown (e.g., 1 hour)
            interval = int(os.getenv("REBALANCE_INTERVAL", 3600))
            await asyncio.sleep(interval)

if __name__ == "__main__":
    service = RebalancerService()
    asyncio.run(service.start())
