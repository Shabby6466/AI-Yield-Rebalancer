"""
Timeseries Database for Historical Yield Data
Stores daily APY/TVL snapshots in SQLite for backtesting and analysis.
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import json

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'yield_history.db')


class TimeseriesDB:
    """SQLite-based storage for historical yield data"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS yield_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pool_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    apy REAL NOT NULL,
                    tvl_usd REAL DEFAULT 0,
                    chain TEXT DEFAULT '',
                    protocol TEXT DEFAULT '',
                    symbol TEXT DEFAULT '',
                    is_stablecoin INTEGER DEFAULT 0,
                    UNIQUE(pool_id, timestamp)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pool_time 
                ON yield_snapshots(pool_id, timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON yield_snapshots(timestamp)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    pools_collected INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'success',
                    duration_seconds REAL DEFAULT 0
                )
            """)
            conn.commit()
        logger.info(f"TimeseriesDB initialized at {self.db_path}")

    def insert_snapshot(self, pool_data: Dict) -> bool:
        """
        Insert a single pool snapshot.
        
        Args:
            pool_data: Dict with keys: pool_id, apy, tvlUsd, chain, project, symbol, stablecoin
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO yield_snapshots 
                    (pool_id, timestamp, apy, tvl_usd, chain, protocol, symbol, is_stablecoin)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pool_data.get('pool', ''),
                    datetime.utcnow().strftime('%Y-%m-%d %H:00:00'),  # Hourly granularity
                    pool_data.get('apy', 0),
                    pool_data.get('tvlUsd', 0),
                    pool_data.get('chain', ''),
                    pool_data.get('project', ''),
                    pool_data.get('symbol', ''),
                    1 if pool_data.get('stablecoin', False) else 0
                ))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to insert snapshot: {e}")
            return False

    def bulk_insert(self, pools: List[Dict]) -> int:
        """
        Insert multiple pool snapshots at once.
        
        Returns:
            Number of successfully inserted records
        """
        count = 0
        timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:00:00')
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                for pool in pools:
                    try:
                        conn.execute("""
                            INSERT OR REPLACE INTO yield_snapshots 
                            (pool_id, timestamp, apy, tvl_usd, chain, protocol, symbol, is_stablecoin)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            pool.get('pool', ''),
                            timestamp,
                            pool.get('apy', 0),
                            pool.get('tvlUsd', 0),
                            pool.get('chain', ''),
                            pool.get('project', ''),
                            pool.get('symbol', ''),
                            1 if pool.get('stablecoin', False) else 0
                        ))
                        count += 1
                    except Exception as e:
                        logger.warning(f"Skipping pool {pool.get('symbol', '?')}: {e}")
                
                conn.commit()
        except Exception as e:
            logger.error(f"Bulk insert failed: {e}")
        
        logger.info(f"Inserted {count}/{len(pools)} snapshots")
        return count

    def insert_historical(self, pool_id: str, history: List[Dict], metadata: Dict = None) -> int:
        """
        Insert historical data from DefiLlama /chart/{pool_id} endpoint.
        
        Args:
            pool_id: The pool UUID
            history: List of dicts with 'timestamp', 'apy', 'tvlUsd'
            metadata: Optional dict with 'chain', 'project', 'symbol', 'stablecoin'
            
        Returns:
            Number of inserted records
        """
        count = 0
        chain = metadata.get('chain', '') if metadata else ''
        protocol = metadata.get('project', '') if metadata else ''
        symbol = metadata.get('symbol', '') if metadata else ''
        is_stable = 1 if metadata and metadata.get('stablecoin', False) else 0
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                for point in history:
                    try:
                        ts = point.get('timestamp', '')
                        # Normalize timestamp to date only
                        if 'T' in str(ts):
                            ts = str(ts).split('T')[0]
                        
                        conn.execute("""
                            INSERT OR REPLACE INTO yield_snapshots 
                            (pool_id, timestamp, apy, tvl_usd, chain, protocol, symbol, is_stablecoin)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            pool_id,
                            ts,
                            point.get('apy', 0),
                            point.get('tvlUsd', 0),
                            chain, protocol, symbol, is_stable
                        ))
                        count += 1
                    except Exception:
                        pass
                conn.commit()
        except Exception as e:
            logger.error(f"Historical insert failed: {e}")
        
        return count

    def get_pool_history(self, pool_id: str, days: int = 30) -> List[Dict]:
        """
        Get historical data for a specific pool.
        
        Args:
            pool_id: Pool UUID
            days: Number of days to look back
            
        Returns:
            List of dicts with timestamp, apy, tvl_usd
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT timestamp, apy, tvl_usd, chain, protocol, symbol
                FROM yield_snapshots
                WHERE pool_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """, (pool_id, cutoff)).fetchall()
        
        return [dict(r) for r in rows]

    def get_all_pools_at(self, date: str) -> List[Dict]:
        """
        Get all pool snapshots for a specific date.
        Used by backtester to simulate "what was available on Day X?"
        
        Args:
            date: Date string (YYYY-MM-DD)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT pool_id, timestamp, apy, tvl_usd, chain, protocol, symbol, is_stablecoin
                FROM yield_snapshots
                WHERE timestamp LIKE ?
                ORDER BY apy DESC
            """, (f"{date}%",)).fetchall()
        
        return [dict(r) for r in rows]

    def get_top_pools_at(self, date: str, limit: int = 10, stablecoin_only: bool = True) -> List[Dict]:
        """
        Get top performing pools on a specific date.
        
        Args:
            date: Date string (YYYY-MM-DD)
            limit: Max results
            stablecoin_only: Filter for stablecoins
        """
        query = """
            SELECT pool_id, timestamp, apy, tvl_usd, chain, protocol, symbol, is_stablecoin
            FROM yield_snapshots
            WHERE timestamp LIKE ?
            AND tvl_usd > 1000000
            AND apy < 500
        """
        params = [f"{date}%"]
        
        if stablecoin_only:
            query += " AND is_stablecoin = 1"
        
        query += " ORDER BY apy DESC LIMIT ?"
        params.append(limit)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        
        return [dict(r) for r in rows]

    def get_unique_dates(self) -> List[str]:
        """Get all unique dates in the database"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT DISTINCT substr(timestamp, 1, 10) as date
                FROM yield_snapshots
                ORDER BY date ASC
            """).fetchall()
        
        return [r[0] for r in rows]

    def get_stats(self) -> Dict:
        """Get database statistics"""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM yield_snapshots").fetchone()[0]
            pools = conn.execute("SELECT COUNT(DISTINCT pool_id) FROM yield_snapshots").fetchone()[0]
            dates = conn.execute("SELECT COUNT(DISTINCT substr(timestamp, 1, 10)) FROM yield_snapshots").fetchone()[0]
            
            oldest = conn.execute("SELECT MIN(timestamp) FROM yield_snapshots").fetchone()[0]
            newest = conn.execute("SELECT MAX(timestamp) FROM yield_snapshots").fetchone()[0]
        
        return {
            "total_records": total,
            "unique_pools": pools,
            "unique_dates": dates,
            "oldest_record": oldest,
            "newest_record": newest,
            "db_path": self.db_path
        }

    def log_collection(self, pools_collected: int, duration: float, status: str = "success"):
        """Log a data collection event"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO collection_log (timestamp, pools_collected, status, duration_seconds)
                VALUES (?, ?, ?, ?)
            """, (datetime.utcnow().isoformat(), pools_collected, status, duration))
            conn.commit()


if __name__ == "__main__":
    # Quick test
    db = TimeseriesDB()
    print(f"DB Stats: {db.get_stats()}")
