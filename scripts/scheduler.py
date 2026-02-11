"""
Automated Data Collection Scheduler
Runs data collection every 15 minutes to build historical dataset
"""

import asyncio
import logging
import os
import sys
import schedule
import time
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.collect_data import collect_and_store_current_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_collection():
    """Run data collection"""
    logger.info(f"\n{'='*80}")
    logger.info(f"SCHEDULED COLLECTION - {datetime.now()}")
    logger.info(f"{'='*80}\n")
    
    try:
        asyncio.run(collect_and_store_current_data())
        logger.info("✅ Collection successful")
    except Exception as e:
        logger.error(f"❌ Collection failed: {e}")


def main():
    """Main scheduler loop"""
    load_dotenv()
    
    # Get interval from environment or default to 5 minutes (was 15)
    interval_minutes = int(os.getenv("COLLECTION_INTERVAL_MIN", "5"))
    
    logger.info(f"\n{'='*80}")
    logger.info("DeFi YIELD DATA COLLECTION SCHEDULER")
    logger.info(f"{'='*80}\n")
    logger.info(f"📅 Schedule: Every {interval_minutes} minutes")
    logger.info("🎯 Target: Build 18-month historical dataset")
    logger.info(f"⏰ Started: {datetime.now()}\n")
    
    # Run immediately on start
    logger.info("Running initial collection...")
    run_collection()
    
    # Schedule collection at configurable interval
    schedule.every(interval_minutes).minutes.do(run_collection)
    
    logger.info(f"\n✅ Scheduler active (interval: {interval_minutes}min). Press Ctrl+C to stop.")
    logger.info("💡 Tip: Customize interval with: COLLECTION_INTERVAL_MIN=2 python scripts/scheduler.py\n")
    
    # Keep running
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)  # Check every 30 seconds
    except KeyboardInterrupt:
        logger.info("\n\n⏹️  Scheduler stopped by user")


if __name__ == "__main__":
    main()
