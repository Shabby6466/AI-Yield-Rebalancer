import asyncio
import os
import logging
import sys
from datetime import datetime
from decimal import Decimal
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.rebalancer_service import RebalancerService
from src.scheduler.collector import YieldCollector
from src.core.state_store import StateStore

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("ActualSystemDemo")

async def run_demo():
    """
    Switching to Production: Actual System Demo.
    1. Refreshes data from DeFiLlama.
    2. Resets state to a clean 'CASH' position for demonstration.
    3. Starts the Rebalancer in a continuous loop.
    """
    load_dotenv()
    
    logger.info("="*60)
    logger.info("🚀 AI-YIELD REBALANCER: ACTUAL SYSTEM DEMO")
    logger.info("="*60)
    
    # 1. Refresh Data (Actual System Requirement)
    logger.info("📡 Step 1: Refreshing real-time market data from DeFiLlama...")
    collector = YieldCollector()
    await collector.run_once()
    logger.info("✓ Market data refreshed.")

    # 2. Reset System State (DISABLED for Persistence)
    # logger.info("🔄 Step 2: resetting system state to 'CASH' for demo clarity...")
    # state_store = StateStore()
    # state_store.update_state(
    #     current_pool_id=None,
    #     current_pool_symbol="CASH",
    #     current_apy=0.0,
    #     initial_capital=0.0, # Will be set by service from wallet
    #     cumulative_costs=0.0
    # )
    # logger.info("✓ System state reset skipped.")

    # 3. Start Rebalancer
    logger.info("🧠 Step 3: Starting Rebalancer Service...")
    rebalancer = RebalancerService()
    
    # Run the continuous loop
    await rebalancer.start()

if __name__ == "__main__":
    try:
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        logger.info("Demo stopped by user.")
    except Exception as e:
        logger.error(f"Demo failed: {e}", exc_info=True)
