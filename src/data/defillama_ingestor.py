"""
DefiLlama-only ingestion pipeline

Fetches yields from DeFiLlama and batch-inserts into TimescaleDB using psycopg2

-- Create a background policy to drop chunks older than 6 months
-- This keeps your database small, fast, and optimized for AI inference
SELECT add_retention_policy('yield_snapshots', drop_after => INTERVAL '6 months');

"""


import os
import asyncio
import logging
import signal
from datetime import datetime
from typing import Optional, Any

import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import execute_values
from src.data.defillama_client import DefiLlamaClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class YieldIngestor:
    def __init__(self, db_url: str, min_connections: int = 2, max_connections: int = 5):
        self.db_url = db_url
        self.client = DefiLlamaClient()  # Persistent session client
        self.shutdown_event = asyncio.Event()
        self.current_ingestion_task: Optional[asyncio.Task[Any]] = None
        
        # Initialize connection pool for robustness
        try:
            self.conn_pool = SimpleConnectionPool(min_connections, max_connections, db_url)
            logger.info(f"Connection pool initialized: {min_connections}-{max_connections} connections")
        except Exception as e:
            logger.error(f"Failed to initialize connection pool: {e}")
            self.conn_pool = None
        
        self.stables = ["USDC", "USDT", "DAI", "USDE", "PYUSD"]

    async def fetch_and_store(self):
        """Fetch data from DeFiLlama and ingest into TimescaleDB"""
        logger.info("Fetching data from DeFiLlama...")
        try:
            data = await self.client.fetch_all_yields()

            timestamp = datetime.utcnow()
            records = []

            target_chains = {"Ethereum", "Base"}
            target_projects = {"uniswap-v3", "aave-v3", "compound-v3", "lido"}
            min_tvl_threshold = 1_000_000


            for p in data:
                is_correct_chain = p.get("chain") in target_chains
                is_correct_project = p.get("project") in target_projects
                is_stable = p.get("stablecoin") is True
                has_liquidity = float(p.get("tvlUsd", 0)) >= min_tvl_threshold

                if is_correct_chain and is_stable and has_liquidity and is_correct_project:
                    records.append((
                        timestamp,
                        p.get("pool"),
                        p.get("project"),
                        p.get("symbol"),
                        float(p.get("apy", 0) or 0),
                        float(p.get("apyBase", 0) or 0),
                        float(p.get("apyReward", 0) or 0),
                        float(p.get("tvlUsd", 0) or 0),
                        p.get("ilRisk", ""),
                        bool(p.get("outlier", False)),
                    ))

            if records:
                self._insert_to_db(records)
                logger.info(f"Ingested {len(records)} high-quality stablecoin pools.")
            else:
                logger.warning("No pools met the high-quality filters this hour.")

        except Exception as e:
            logger.error(f"DeFiLlama fetch failed: {e}")
            raise

    def _insert_to_db(self, records):
        """Batch insert using connection pool for robustness"""
        query = """
            INSERT INTO yield_snapshots 
            (time, pool_id, protocol, symbol, apy_total, apy_base, apy_reward, tvl_usd, il_risk, is_outlier)
            VALUES %s
            ON CONFLICT (time, pool_id) DO NOTHING;
        """

        conn = None
        try:
            # Get connection from pool instead of creating a new one
            if self.conn_pool:
                conn = self.conn_pool.getconn()
            else:
                # Fallback to direct connection if pool unavailable
                conn = psycopg2.connect(self.db_url)
            
            with conn.cursor() as cur:
                execute_values(cur, query, records, template=None, page_size=100)
            conn.commit()
            logger.info(f"Inserted {len(records)} records into yield_snapshots")
        except psycopg2.OperationalError as e:
            logger.error(f"Database connection error: {e}")
            if conn:
                conn.rollback()
        except Exception as e:
            logger.error(f"Database error during insert: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                if self.conn_pool:
                    # Return connection to pool
                    self.conn_pool.putconn(conn)
                else:
                    # Close if using fallback
                    conn.close()

    async def close(self):
        """
        Cleanup and close persistent HTTP session and connection pool on shutdown.
        Ensures graceful termination without resource leaks.
        """
        logger.info("Closing ingestor resources...")
        
        # Close HTTP session
        await self.client.close()
        logger.info("DeFiLlama client session closed")
        
        # Close connection pool
        if self.conn_pool:
            self.conn_pool.closeall()
            logger.info("Database connection pool closed")


async def main_loop():
    """Main ingestion loop with graceful shutdown support"""
    db_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    if not db_url:
        logger.error("DATABASE_URL not set in environment")
        return

    ingestor = YieldIngestor(db_url)

    # Run once, or loop forever depending on env
    run_once = os.getenv("INGEST_RUN_ONCE", "false").lower() in ("1", "true", "yes")

    if run_once:
        try:
            await ingestor.fetch_and_store()
        finally:
            await ingestor.close()
        return

    # Continuous loop with graceful shutdown
    while not ingestor.shutdown_event.is_set():
        try:
            logger.info("Starting ingestion run...")
            ingestor.current_ingestion_task = asyncio.current_task()
            await ingestor.fetch_and_store()
        except asyncio.CancelledError:
            logger.info("Ingestion cancelled, cleaning up...")
            break
        except Exception as e:
            logger.error(f"Ingestion run failed: {e}")
        finally:
            ingestor.current_ingestion_task = None
        
        # Wait before next run, but allow early exit via shutdown_event
        if not ingestor.shutdown_event.is_set():
            logger.info("Waiting 1 hour for next snapshot...")
            try:
                await asyncio.wait_for(
                    ingestor.shutdown_event.wait(),
                    timeout=3600.0
                )
            except asyncio.TimeoutError:
                # Timeout is normal - proceed to next iteration
                pass
    
    logger.info("Ingestion loop exiting...")
    await ingestor.close()


def handle_signal(signum, frame):
    """Signal handler for graceful shutdown (SIGINT, SIGTERM)"""
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}, initiating graceful shutdown...")
    # Signal will be handled by the event loop


async def main_with_signal_handling():
    """Wrapper to set up signal handlers and run the main loop"""
    db_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    if not db_url:
        logger.error("DATABASE_URL not set in environment")
        return

    ingestor = YieldIngestor(db_url)
    
    # Run once, or loop forever depending on env
    run_once = os.getenv("INGEST_RUN_ONCE", "false").lower() in ("1", "true", "yes")
    
    if run_once:
        try:
            await ingestor.fetch_and_store()
        finally:
            await ingestor.close()
        return

    # Setup signal handlers for graceful shutdown
    def _signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, setting shutdown event...")
        ingestor.shutdown_event.set()
    
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    
    # Continuous loop with graceful shutdown
    while not ingestor.shutdown_event.is_set():
        try:
            logger.info("Starting ingestion run...")
            ingestor.current_ingestion_task = asyncio.current_task()
            await ingestor.fetch_and_store()
        except asyncio.CancelledError:
            logger.info("Ingestion cancelled, cleaning up...")
            break
        except Exception as e:
            logger.error(f"Ingestion run failed: {e}")
        finally:
            ingestor.current_ingestion_task = None
        
        # Wait before next run, but allow early exit via shutdown_event
        if not ingestor.shutdown_event.is_set():
            logger.info("Waiting 1 hour for next snapshot...")
            try:
                await asyncio.wait_for(
                    ingestor.shutdown_event.wait(),
                    timeout=3600.0
                )
            except asyncio.TimeoutError:
                # Timeout is normal - proceed to next iteration
                pass
    
    logger.info("Ingestion loop exiting...")
    await ingestor.close()


async def shutdown_handler(ingestor: YieldIngestor):
    """Cleanup on shutdown"""
    logger.info("Shutting down ingestor...")
    await ingestor.close()
    logger.info("Ingestor shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main_with_signal_handling())
    except KeyboardInterrupt:
        logger.info("Ingestor interrupted by user; exiting.")
    except Exception as e:
        logger.error(f"Ingestor failed: {e}", exc_info=True)
