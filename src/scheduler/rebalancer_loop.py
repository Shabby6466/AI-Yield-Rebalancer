
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.core.rebalancer_service import RebalancerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def main():
    load_dotenv()
    logger.info("🚀 Starting AI Yield Rebalancer Autonomous Loop...")
    
    while True:
        try:
            service = RebalancerService()
            await service.start()
        except Exception as e:
            logger.error(f"Error in rebalancer service: {e}. Retrying in 10s...", exc_info=True)
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
