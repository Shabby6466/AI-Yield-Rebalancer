import psycopg2
import os
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_to_timescaledb():
    """
    Migrates standard PostgreSQL tables to TimescaleDB Hypertables.
    Requires the TimescaleDB extension to be installed in the database.
    """
    load_dotenv()
    
    conn_string = f"dbname='{os.getenv('DB_NAME', 'defi_yield_db')}' user='{os.getenv('DB_USER')}' password='{os.getenv('DB_PASSWORD')}' host='{os.getenv('DB_HOST', 'localhost')}'"
    
    try:
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        cursor = conn.cursor()
        
        logger.info("Enabling TimescaleDB extension...")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
        
        # 0. Ensure tables exist (from schema.sql if needed) or just create asset_prices here
        logger.info("Ensuring all tables exist...")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS asset_prices (
            id SERIAL,
            asset VARCHAR(50) NOT NULL,
            price_usd FLOAT NOT NULL,
            price_eth FLOAT,
            market_cap_usd FLOAT,
            volume_24h_usd FLOAT,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(id, recorded_at),
            UNIQUE(asset, recorded_at)
        );
        """)

        # 1. Convert protocol_yields to hypertable
        # TimescaleDB requires the partitioning column (recorded_at) to be part of the unique/primary key
        logger.info("Modifying protocol_yields constraints for TimescaleDB...")
        try:
            # Drop old PK, add new composite PK
            cursor.execute("ALTER TABLE protocol_yields DROP CONSTRAINT IF EXISTS protocol_yields_pkey CASCADE;")
            cursor.execute("ALTER TABLE protocol_yields ADD PRIMARY KEY (id, recorded_at);")
        except Exception as e:
            logger.warning(f"PK modification for protocol_yields: {e}")

        logger.info("Converting protocol_yields to hypertable...")
        try:
            cursor.execute("SELECT create_hypertable('protocol_yields', 'recorded_at', if_not_exists => TRUE, migrate_data => TRUE);")
        except Exception as e:
            logger.warning(f"Hypertable creation for protocol_yields failed: {e}")

        # 2. Convert asset_prices to hypertable
        logger.info("Converting asset_prices to hypertable...")
        try:
            cursor.execute("SELECT create_hypertable('asset_prices', 'recorded_at', if_not_exists => TRUE, migrate_data => TRUE);")
        except Exception as e:
            logger.warning(f"Hypertable creation for asset_prices failed: {e}")

        # 3. Create Continuous Aggregates for Daily Metrics
        logger.info("Creating continuous aggregates for daily yields...")
        cursor.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS daily_yield_summary
        WITH (timescaledb.continuous) AS
        SELECT 
            protocol_id,
            asset,
            time_bucket('1 day', recorded_at) AS bucket,
            avg(apy_percent) as avg_apy,
            max(apy_percent) as max_apy,
            min(apy_percent) as min_apy,
            avg(total_liquidity_usd) as avg_liquidity
        FROM protocol_yields
        GROUP BY protocol_id, asset, bucket
        WITH NO DATA;
        """)

        # 4. Set Retention Policy (e.g., keep 2 years of raw data)
        logger.info("Setting data retention policy...")
        cursor.execute("SELECT add_retention_policy('protocol_yields', INTERVAL '2 years', if_not_exists => TRUE);")

        logger.info("TimescaleDB migration complete!")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")

if __name__ == "__main__":
    migrate_to_timescaledb()
