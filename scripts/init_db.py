
import os
import psycopg2
from psycopg2 import sql
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set in environment")
        return

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()

        # 1. Try to create TimescaleDB extension
        try:
            logger.info("Attempting to enable TimescaleDB extension...")
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
            logger.info("✅ TimescaleDB extension enabled.")
            has_timescale = True
        except Exception as e:
            logger.warning(f"⚠️ Could not enable TimescaleDB extension: {e}")
            logger.warning("Proceeding with standard PostgreSQL table.")
            has_timescale = False
            # We need to rollback if the previous transaction failed (but we set autocommit=True, so each statement is its own tx)
            # However, if it failed, we might need to reset connection state if not in autocommit, but we are.

        # 2. Create the table
        logger.info("Recreating yield_snapshots table with correct schema...")
        cur.execute("DROP TABLE IF EXISTS yield_snapshots CASCADE;")
        
        create_table_sql = """
        CREATE TABLE yield_snapshots (
            time TIMESTAMPTZ NOT NULL,
            pool_id VARCHAR(64) NOT NULL,
            protocol VARCHAR(32) NOT NULL,
            symbol VARCHAR(32) NOT NULL,
            chain VARCHAR(32) DEFAULT 'Ethereum',
            
            apy_total DOUBLE PRECISION,
            apy_base DOUBLE PRECISION,
            apy_reward DOUBLE PRECISION,
            
            tvl_usd DOUBLE PRECISION,
            volume_24h_usd DOUBLE PRECISION DEFAULT 0,
            volatility_24h DOUBLE PRECISION DEFAULT 0,
            utilization_rate DOUBLE PRECISION DEFAULT 0,
            
            il_risk VARCHAR(32),
            is_outlier BOOLEAN DEFAULT FALSE,
            
            PRIMARY KEY (pool_id, time)
        );

        """
        cur.execute(create_table_sql)
        logger.info("✅ yield_snapshots table created.")

        # 3. Convert to Hypertable (if TimescaleDB is available)
        if has_timescale:
            try:
                logger.info("Converting to hypertable...")
                cur.execute("SELECT create_hypertable('yield_snapshots', 'time', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);")
                logger.info("✅ Converted to hypertable.")
            except Exception as e:
                logger.error(f"❌ Failed to convert to hypertable: {e}")
        
        # 4. Create Index
        logger.info("Creating index...")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pool_id ON yield_snapshots (pool_id, time DESC);")
        logger.info("✅ Index created.")

        cur.close()
        conn.close()
        logger.info("🎉 Database initialization complete!")

    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")

if __name__ == "__main__":
    init_db()
