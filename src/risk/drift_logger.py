
import logging
import time
from typing import Dict, List, Optional
from web3 import Web3
from datetime import datetime
import psycopg2
import os

logger = logging.getLogger(__name__)

class DriftLogger:
    """
    Monitors price drift for Uniswap V3 positions.
    Logs a DriftEvent if the current price/tick exits the defined ranges.
    """
    
    def __init__(self, w3: Web3, db_url: Optional[str] = None):
        self.w3 = w3
        self.db_url = db_url or os.getenv('DATABASE_URL')
        
    def check_drift(self, pool_address: str, tick_lower: int, tick_upper: int, current_tick: int) -> bool:
        """
        Check if the current tick is outside the range.
        Logs to DB if drift is detected.
        """
        is_drifting = current_tick < tick_lower or current_tick > tick_upper
        
        if is_drifting:
            logger.warning(f"🚨 DRIFT DETECTED: Pool {pool_address} tick {current_tick} is outside range [{tick_lower}, {tick_upper}]")
            self._log_drift_event(pool_address, tick_lower, tick_upper, current_tick)
            return True
            
        return False

    def _log_drift_event(self, pool_address: str, tick_lower: int, tick_upper: int, current_tick: int):
        """Log drift event to PostgreSQL"""
        if not self.db_url:
            logger.warning("No DATABASE_URL set, cannot log DriftEvent")
            return

        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO drift_events (
                            pool_address, tick_lower, tick_upper, current_tick, severity, message
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        pool_address,
                        tick_lower,
                        tick_upper,
                        current_tick,
                        'warning',
                        f"Price exited range: {current_tick} outside [{tick_lower}, {tick_upper}]"
                    ))
                conn.commit()
            logger.info(f"✓ DriftEvent logged for pool {pool_address}")
        except Exception as e:
            logger.error(f"Failed to log DriftEvent: {e}")

    def get_active_drift_duration_hours(self, pool_address: str) -> float:
        """Check how long a drift has been active (since first warning in current streak)"""
        if not self.db_url:
            return 0.0
            
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    # Find the earliest timestamp in the most recent continuous streak of warnings
                    cur.execute("""
                        SELECT MIN(timestamp) FROM drift_events
                        WHERE pool_address = %s
                        AND timestamp > NOW() - INTERVAL '7 days'
                    """, (pool_address,))
                    result = cur.fetchone()
                    if result and result[0]:
                        duration = (datetime.now() - result[0].replace(tzinfo=None)).total_seconds() / 3600
                        return duration
        except Exception as e:
            logger.warning(f"Could not fetch drift duration: {e}")
            
        return 0.0

class UniswapPositionMonitor:
    """High-level monitor for multiple positions"""
    
    def __init__(self, drift_logger: DriftLogger, uniswap_client):
        self.drift_logger = drift_logger
        self.uniswap_client = uniswap_client
        self.monitored_positions = [] # List of {pool_address, tick_lower, tick_upper}

    def add_position(self, pool_address: str, tick_lower: int, tick_upper: int):
        self.monitored_positions.append({
            "pool_address": pool_address,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper
        })

    async def monitor_all(self):
        """Fetch latest ticks and check for drift across all positions"""
        for pos in self.monitored_positions:
            pool_address = str(pos['pool_address'])
            tick_lower = int(pos['tick_lower'])
            tick_upper = int(pos['tick_upper'])
            
            metrics = self.uniswap_client.get_pool_metrics(pool_address)
            if metrics:
                current_tick = int(metrics['tick'])
                self.drift_logger.check_drift(
                    pool_address,
                    tick_lower,
                    tick_upper,
                    current_tick
                )
