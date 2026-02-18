-- DeFi Yield Optimization Database Schema
-- Using TimescaleDB for time-series optimization

-- Drop existing tables if they exist
DROP TABLE IF EXISTS protocol_yields CASCADE;
DROP TABLE IF EXISTS yields CASCADE;
DROP TABLE IF EXISTS yield_metrics CASCADE;
DROP TABLE IF EXISTS assets CASCADE;

-- 1. Metadata Table (Static info about assets)
CREATE TABLE assets (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    address CHAR(42) NOT NULL,
    decimals INT DEFAULT 18,
    protocol VARCHAR(20) NOT NULL, -- 'aave_v3', 'uniswap_v3', 'curve'
    chain VARCHAR(20) DEFAULT 'ethereum',
    UNIQUE(address, protocol)
);

-- 2. Unified Yields (Hypertable for ML)
-- This normalizes data across protocols for the model
CREATE TABLE yield_metrics (
    time TIMESTAMPTZ NOT NULL,
    asset_id INT REFERENCES assets(id),
    
    -- Target Variable for ML
    apy_percent DOUBLE PRECISION, 
    
    -- Features
    tvl_usd DOUBLE PRECISION,
    volume_24h_usd DOUBLE PRECISION,
    utilization_rate DOUBLE PRECISION, -- NULL for Uniswap
    volatility_24h DOUBLE PRECISION,
    
    -- Metadata for debugging
    block_number BIGINT,
    UNIQUE (time, asset_id)
);

-- 3. Yield Snapshots (Unified History for LSTM)
-- Stores raw historical data fetched from DefiLlama
CREATE TABLE yield_snapshots (
    time TIMESTAMPTZ NOT NULL,
    pool_id VARCHAR(64) NOT NULL,
    protocol VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    chain VARCHAR(32) DEFAULT 'Ethereum',
    
    apy_total DOUBLE PRECISION,
    tvl_usd DOUBLE PRECISION,
    volume_24h_usd DOUBLE PRECISION DEFAULT 0,
    volatility_24h DOUBLE PRECISION DEFAULT 0,
    utilization_rate DOUBLE PRECISION DEFAULT 0,
    
    UNIQUE(pool_id, time)
);


-- Note: TimescaleDB extension is optional but recommended for production
-- If you have TimescaleDB installed, uncomment the following:
-- CREATE EXTENSION IF NOT EXISTS timescaledb;
-- SELECT create_hypertable('yield_metrics', 'time', if_not_exists => TRUE);

-- Create indexes for common queries
CREATE INDEX idx_yield_metrics_asset_id ON yield_metrics (asset_id, time DESC);
CREATE INDEX idx_yield_metrics_time ON yield_metrics (time DESC);
CREATE INDEX idx_assets_protocol ON assets (protocol);
CREATE INDEX idx_assets_symbol ON assets (symbol);

-- Insert common assets for Aave V3 on Ethereum
INSERT INTO assets (symbol, address, decimals, protocol, chain) VALUES
('USDC', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', 6, 'aave_v3', 'ethereum'),
('USDT', '0xdAC17F958D2ee523a2206206994597C13D831ec7', 6, 'aave_v3', 'ethereum'),
('DAI', '0x6B175474E89094C44Da98b954EedeAC495271d0F', 18, 'aave_v3', 'ethereum'),
('WETH', '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2', 18, 'aave_v3', 'ethereum'),
('WBTC', '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599', 8, 'aave_v3', 'ethereum')
ON CONFLICT (address, protocol) DO NOTHING;

-- Insert common Uniswap V3 pairs
INSERT INTO assets (symbol, address, decimals, protocol, chain) VALUES
('USDC/WETH', '0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640', 18, 'uniswap_v3', 'ethereum'),
('WBTC/WETH', '0xCBCdF9626bC03E24f779434178A73a0B4bad62eD', 18, 'uniswap_v3', 'ethereum'),
('USDC/USDT', '0x3416cF6C708Da44DB2624D63ea0AAef7113527C6', 18, 'uniswap_v3', 'ethereum'),
('DAI/USDC', '0x5777d92f208679DB4b9778590Fa3CAB3aC9e2168', 18, 'uniswap_v3', 'ethereum')
ON CONFLICT (address, protocol) DO NOTHING;

-- Insert Curve pools
INSERT INTO assets (symbol, address, decimals, protocol, chain) VALUES
('3pool', '0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7', 18, 'curve', 'ethereum'),
('stETH', '0xDC24316b9AE028F1497c275EB9192a3Ea0f67022', 18, 'curve', 'ethereum'),
('FRAX', '0xd632f22692FaC7611d2AA1C0D552930D43CAEd3B', 18, 'curve', 'ethereum')
ON CONFLICT (address, protocol) DO NOTHING;

-- Create a view for latest metrics per asset
CREATE OR REPLACE VIEW latest_yields AS
SELECT DISTINCT ON (asset_id)
    a.symbol,
    a.protocol,
    ym.apy_percent,
    ym.tvl_usd,
    ym.volume_24h_usd,
    ym.utilization_rate,
    ym.time
FROM yield_metrics ym
JOIN assets a ON ym.asset_id = a.id
ORDER BY asset_id, time DESC;

COMMENT ON TABLE assets IS 'Static metadata for all tracked DeFi assets';
COMMENT ON TABLE yield_metrics IS 'Time-series yield data optimized for ML training and inference';
COMMENT ON COLUMN yield_metrics.apy_percent IS 'Target variable - Annual Percentage Yield';
COMMENT ON COLUMN yield_metrics.utilization_rate IS 'Lending protocol utilization (NULL for DEX pools)';
