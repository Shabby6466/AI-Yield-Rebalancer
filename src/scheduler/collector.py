"""
Scheduled Data Collector
Periodically fetches yield data from DefiLlama and stores it in the timeseries DB.
Can run as a standalone process or be imported.
"""

import asyncio
import time
import logging
import signal
import sys
from datetime import datetime
from typing import Optional

from src.data.defillama_client import DefiLlamaClient
from src.data.timeseries_db import TimeseriesDB

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


class YieldCollector:
    """Collects and stores yield data on a schedule"""

    def __init__(self, interval_hours: float = 6.0, backfill_days: int = 90):
        """
        Args:
            interval_hours: How often to collect (default: every 6 hours)
            backfill_days: How many days of history to fetch on first run
        """
        self.interval = interval_hours * 3600  # Convert to seconds
        self.backfill_days = backfill_days
        self.client = DefiLlamaClient()
        self.db = TimeseriesDB()
        self._running = False

    async def collect_current(self) -> int:
        """
        Fetch current yield data for all stablecoin pools and store it.
        
        Returns:
            Number of pools collected
        """
        start = time.time()
        logger.info("Starting yield collection...")

        try:
            # Fetch all pools
            all_pools = await self.client.fetch_all_yields()

            # Filter: Stablecoins on Ethereum/Base with >$1M TVL
            valid_chains = {'Ethereum', 'Base'}
            filtered = [
                p for p in all_pools
                if p.get('chain') in valid_chains
                and p.get('tvlUsd', 0) > 1_000_000
                and p.get('stablecoin', False)
                and p.get('apy', 0) < 500  # Filter scams
            ]

            # Store in DB
            count = self.db.bulk_insert(filtered)
            duration = time.time() - start

            self.db.log_collection(count, duration, "success")
            logger.info(f"Collected {count} pools in {duration:.1f}s")

            return count

        except Exception as e:
            duration = time.time() - start
            self.db.log_collection(0, duration, f"error: {str(e)}")
            logger.error(f"Collection failed: {e}")
            return 0

    async def backfill_pool(self, pool_id: str, metadata: dict = None) -> int:
        """
        Fetch and store historical data for a specific pool.
        Uses DefiLlama /chart/{pool_id} endpoint.
        
        Returns:
            Number of historical records inserted
        """
        try:
            history = await self.client.fetch_historical_yield(pool_id)
            if not history:
                logger.warning(f"No history for pool {pool_id}")
                return 0

            count = self.db.insert_historical(pool_id, history, metadata=metadata)
            logger.info(f"Backfilled {count} records for pool {pool_id}")
            return count

        except Exception as e:
            logger.error(f"Backfill failed for {pool_id}: {e}")
            return 0

    async def backfill_top_pools(self, limit: int = 20) -> int:
        """
        Backfill historical data for the top N stablecoin pools.
        This should be run once to populate the database.
        
        Returns:
            Total records inserted
        """
        logger.info(f"Starting backfill for top {limit} pools...")

        # Get current top pools
        top_pools = await self.client.fetch_top_pools(
            chains=["Ethereum", "Base"],
            min_tvl=1_000_000,
            limit=limit
        )

        total = 0
        for i, pool in enumerate(top_pools):
            pool_id = pool['pool']
            symbol = pool.get('symbol', 'unknown')
            logger.info(f"[{i+1}/{len(top_pools)}] Backfilling {symbol}...")

            count = await self.backfill_pool(pool_id, metadata=pool)
            total += count

            # Rate limit: DefiLlama is free, be respectful
            await asyncio.sleep(1)

        logger.info(f"Backfill complete: {total} total records for {len(top_pools)} pools")
        return total

    async def run_once(self):
        """Run a single collection cycle"""
        try:
            # Check DB stats
            # Use raw SQL because get_stats might return 0 if tables exist but are empty
            stats = self.db.get_stats()
            
            # If DB is empty, do a backfill first
            if stats['total_records'] == 0:
                logger.info("Empty database detected. Running initial backfill...")
                await self.backfill_top_pools(limit=20)

            # Then collect current snapshot
            await self.collect_current()
        except Exception as e:
            logger.error(f"Single run failed: {e}")

        # Print stats
        stats = self.db.get_stats()
        logger.info(f"DB Stats: {stats}")

    async def run_loop(self):
        """Run continuously on a schedule"""
        self._running = True
        logger.info(f"Scheduler started. Collecting every {self.interval/3600:.1f} hours.")

        while self._running:
            await self.run_once()

            # Wait for next cycle
            next_run = datetime.utcnow().timestamp() + self.interval
            next_run_str = datetime.fromtimestamp(next_run).strftime('%H:%M:%S')
            logger.info(f"Next collection at {next_run_str} UTC")

            # Sleep in small increments so we can be interrupted
            elapsed = 0
            while elapsed < self.interval and self._running:
                await asyncio.sleep(min(60, self.interval - elapsed))
                elapsed += 60

    def stop(self):
        """Stop the scheduler loop"""
        self._running = False
        logger.info("Scheduler stopping...")


def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Yield Data Collector")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--backfill", action="store_true", help="Backfill historical data only")
    parser.add_argument("--interval", type=float, default=6.0, help="Collection interval in hours")
    parser.add_argument("--pools", type=int, default=20, help="Number of top pools to track")
    args = parser.parse_args()

    collector = YieldCollector(interval_hours=args.interval)

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        collector.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    if args.backfill:
        asyncio.run(collector.backfill_top_pools(limit=args.pools))
    elif args.once:
        asyncio.run(collector.run_once())
    else:
        asyncio.run(collector.run_loop())


if __name__ == "__main__":
    main()
