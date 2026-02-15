
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Configure logging to both console and file for dashboard tailing
log_file = "data/rebalancer.log"
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

from src.core.rebalancer_service import RebalancerService

async def main():
    load_dotenv()
    logger.info("🚀 Starting AI Yield Rebalancer Autonomous Loop...")
    service = RebalancerService()
    w3 = service.w3
    
    while True:
        try:
            await service.start()
            current_block = w3.eth.block_number
            logger.info(f"Check complete for block {current_block}. Waiting for next block...")
            
            while w3.eth.block_number <= current_block:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in rebalancer service: {e}. Retrying in 10s...", exc_info=True)
            await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Rebalancer stopped by user.")
