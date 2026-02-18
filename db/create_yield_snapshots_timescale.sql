-- 1. Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- 2. Create the base table
CREATE TABLE IF NOT EXISTS yield_snapshots (
    time TIMESTAMPTZ NOT NULL,
    pool_id UUID NOT NULL,
    protocol TEXT NOT NULL,
    symbol TEXT NOT NULL,
    apy_total DOUBLE PRECISION,
    apy_base DOUBLE PRECISION,
    apy_reward DOUBLE PRECISION,
    tvl_usd DOUBLE PRECISION,
    il_risk TEXT,
    is_outlier BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (time, pool_id)
);

-- 3. Convert to Hypertable (partitioned by time)
-- We'll use 7-day chunks for efficient queries
SELECT create_hypertable('yield_snapshots', 'time', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

-- 4. Create index for fast lookups by pool
CREATE INDEX IF NOT EXISTS idx_pool_id ON yield_snapshots (pool_id, time DESC);
