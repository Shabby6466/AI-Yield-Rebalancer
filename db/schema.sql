-- AI-Driven DeFi Yield Rebalancing System
-- PostgreSQL Schema (Standard Tables)

-- ============================================================================
-- Extensions
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- Core Tables
-- ============================================================================

-- Protocol metadata
CREATE TABLE IF NOT EXISTS protocols (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    symbol VARCHAR(10) NOT NULL,
    chain VARCHAR(50) NOT NULL DEFAULT 'ethereum',
    address VARCHAR(255) NOT NULL,
    protocol_type VARCHAR(50) NOT NULL, -- 'lending', 'dex', 'amm'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Protocol APY and yield data
CREATE TABLE IF NOT EXISTS protocol_yields (
    id SERIAL PRIMARY KEY,
    protocol_id INTEGER NOT NULL REFERENCES protocols(id),
    asset VARCHAR(50) NOT NULL, -- 'USDC', 'DAI', etc
    apy_percent FLOAT NOT NULL,
    total_liquidity_usd FLOAT NOT NULL,
    available_liquidity_usd FLOAT NOT NULL,
    utilization_ratio FLOAT,
    variable_borrow_rate FLOAT,
    stability_fee FLOAT,
    tvl_usd FLOAT,
    volume_24h_usd FLOAT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(protocol_id, asset, recorded_at)
);

CREATE INDEX idx_protocol_yields_protocol_id ON protocol_yields (protocol_id, recorded_at DESC);
CREATE INDEX idx_protocol_yields_asset ON protocol_yields (asset, recorded_at DESC);
CREATE INDEX idx_protocol_yields_time ON protocol_yields (recorded_at DESC);

-- Protocol risk scores
CREATE TABLE IF NOT EXISTS protocol_risk_scores (
    id SERIAL PRIMARY KEY,
    protocol_id INTEGER NOT NULL REFERENCES protocols(id),
    risk_score INT NOT NULL CHECK (risk_score >= 0 AND risk_score <= 100),
    audit_score INT,
    security_incidents INT DEFAULT 0,
    exploit_count INT DEFAULT 0,
    last_audit_date DATE,
    contract_age_days INT,
    liquidity_depth_score INT,
    centralization_score INT,
    peg_stability_score INT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(protocol_id, recorded_at)
);

CREATE INDEX idx_risk_scores_protocol_id ON protocol_risk_scores (protocol_id, recorded_at DESC);
CREATE INDEX idx_risk_scores_time ON protocol_risk_scores (recorded_at DESC);

-- Gas prices
CREATE TABLE IF NOT EXISTS gas_prices (
    id SERIAL PRIMARY KEY,
    chain VARCHAR(50) NOT NULL DEFAULT 'ethereum',
    safe_gwei FLOAT NOT NULL,
    standard_gwei FLOAT NOT NULL,
    fast_gwei FLOAT NOT NULL,
    base_fee_per_gas FLOAT,
    priority_fee FLOAT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chain, recorded_at)
);

CREATE INDEX idx_gas_prices_time ON gas_prices (recorded_at DESC);

-- Asset prices
CREATE TABLE IF NOT EXISTS asset_prices (
    id SERIAL PRIMARY KEY,
    asset VARCHAR(50) NOT NULL,
    price_usd FLOAT NOT NULL,
    price_eth FLOAT,
    market_cap_usd FLOAT,
    volume_24h_usd FLOAT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset, recorded_at)
);

CREATE INDEX idx_asset_prices_asset ON asset_prices (asset, recorded_at DESC);
CREATE INDEX idx_asset_prices_time ON asset_prices (recorded_at DESC);

-- ============================================================================
-- Machine Learning Tables
-- ============================================================================

-- LSTM predictions
CREATE TABLE IF NOT EXISTS lstm_predictions (
    id SERIAL PRIMARY KEY,
    protocol_id INTEGER NOT NULL REFERENCES protocols(id),
    asset VARCHAR(50) NOT NULL,
    prediction_date DATE NOT NULL,
    predicted_apy_percent FLOAT NOT NULL,
    predicted_apy_direction VARCHAR(10), -- 'up', 'down', 'neutral'
    confidence_score FLOAT,
    actual_apy_percent FLOAT,
    mape_error FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(protocol_id, asset, prediction_date)
);

CREATE INDEX idx_lstm_predictions_protocol_id ON lstm_predictions (protocol_id, prediction_date DESC);
CREATE INDEX idx_lstm_predictions_asset ON lstm_predictions (asset, prediction_date DESC);

-- XGBoost risk classifications
CREATE TABLE IF NOT EXISTS risk_classifications (
    id SERIAL PRIMARY KEY,
    protocol_id INTEGER NOT NULL REFERENCES protocols(id),
    classification_date DATE NOT NULL,
    risk_level VARCHAR(20) NOT NULL, -- 'low', 'medium', 'high'
    risk_score INT NOT NULL,
    confidence_score FLOAT,
    key_factors TEXT, -- JSON-encoded feature importances
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(protocol_id, classification_date)
);

CREATE INDEX idx_risk_classifications_protocol_id ON risk_classifications (protocol_id, classification_date DESC);
CREATE INDEX idx_risk_classifications_level ON risk_classifications (risk_level, classification_date DESC);

-- ============================================================================
-- Rebalancing & Execution Tables
-- ============================================================================

-- Proposed rebalancing actions
CREATE TABLE IF NOT EXISTS rebalance_proposals (
    id SERIAL PRIMARY KEY,
    proposal_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    from_protocol_id INTEGER REFERENCES protocols(id),
    to_protocol_id INTEGER REFERENCES protocols(id),
    asset VARCHAR(50) NOT NULL,
    amount_usd FLOAT NOT NULL,
    estimated_gas_cost_usd FLOAT,
    expected_apy_improvement_percent FLOAT,
    risk_level_before VARCHAR(20),
    risk_level_after VARCHAR(20),
    status VARCHAR(50) DEFAULT 'proposed', -- 'proposed', 'approved', 'rejected', 'executed'
    executed_at TIMESTAMP,
    tx_hash VARCHAR(255)
);

CREATE INDEX idx_rebalance_proposals_date ON rebalance_proposals (proposal_date DESC);
CREATE INDEX idx_rebalance_proposals_status ON rebalance_proposals (status);

-- Executed transactions
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    tx_hash VARCHAR(255) NOT NULL UNIQUE,
    tx_type VARCHAR(50) NOT NULL, -- 'deposit', 'withdraw', 'rebalance', 'harvest'
    protocol_id INTEGER REFERENCES protocols(id),
    asset VARCHAR(50) NOT NULL,
    amount_usd FLOAT NOT NULL,
    gas_used INT,
    gas_price_gwei FLOAT,
    gas_cost_usd FLOAT,
    slippage_percent FLOAT,
    status VARCHAR(50) NOT NULL, -- 'pending', 'confirmed', 'failed'
    block_number INT,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_transactions_tx_hash ON transactions (tx_hash);
CREATE INDEX idx_transactions_protocol_id ON transactions (protocol_id, timestamp DESC);
CREATE INDEX idx_transactions_timestamp ON transactions (timestamp DESC);

-- ============================================================================
-- Portfolio & Performance Tables
-- ============================================================================

-- Portfolio snapshots (daily)
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(id),
    asset VARCHAR(50) NOT NULL,
    amount FLOAT NOT NULL,
    amount_usd FLOAT NOT NULL,
    apy_percent FLOAT,
    risk_score INT,
    UNIQUE(snapshot_date, protocol_id, asset)
);

CREATE INDEX idx_portfolio_snapshots_protocol ON portfolio_snapshots (protocol_id, snapshot_date DESC);
CREATE INDEX idx_portfolio_snapshots_date ON portfolio_snapshots (snapshot_date DESC);

-- Daily performance metrics
CREATE TABLE IF NOT EXISTS daily_performance (
    date DATE NOT NULL PRIMARY KEY,
    total_portfolio_value_usd FLOAT NOT NULL,
    daily_yield_usd FLOAT,
    daily_return_percent FLOAT,
    cumulative_return_percent FLOAT,
    max_drawdown_percent FLOAT,
    sharpe_ratio FLOAT,
    number_of_rebalances INT DEFAULT 0,
    total_gas_cost_usd FLOAT DEFAULT 0,
    weighted_avg_apy_percent FLOAT,
    weighted_avg_risk_score INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_daily_performance_date ON daily_performance (date DESC);

-- ============================================================================
-- Risk Monitoring Tables
-- ============================================================================

-- Kill switch events
CREATE TABLE IF NOT EXISTS kill_switch_events (
    id SERIAL PRIMARY KEY,
    trigger_type VARCHAR(100) NOT NULL, -- e.g., 'peg_deviation', 'tvl_drop', 'utilization_high'
    protocol_id INTEGER REFERENCES protocols(id),
    severity VARCHAR(20) NOT NULL, -- 'info', 'warning', 'critical'
    threshold_value FLOAT,
    actual_value FLOAT,
    action_taken VARCHAR(255),
    status VARCHAR(50) DEFAULT 'triggered', -- 'triggered', 'resolved'
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_kill_switch_timestamp ON kill_switch_events (timestamp DESC);
CREATE INDEX idx_kill_switch_protocol ON kill_switch_events (protocol_id, timestamp DESC);

-- Data quality issues
CREATE TABLE IF NOT EXISTS data_quality_logs (
    id SERIAL PRIMARY KEY,
    issue_type VARCHAR(100) NOT NULL, -- 'missing_data', 'outlier', 'stale_data'
    protocol_id INTEGER REFERENCES protocols(id),
    asset VARCHAR(50),
    description TEXT,
    severity VARCHAR(20),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Drift events for Uniswap V3
CREATE TABLE IF NOT EXISTS drift_events (
    id SERIAL PRIMARY KEY,
    pool_address VARCHAR(255) NOT NULL,
    tick_lower INT NOT NULL,
    tick_upper INT NOT NULL,
    current_tick INT NOT NULL,
    severity VARCHAR(20) NOT NULL, -- 'info', 'warning', 'critical'
    message TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_drift_events_pool ON drift_events (pool_address, timestamp DESC);
CREATE INDEX idx_drift_events_timestamp ON drift_events (timestamp DESC);

-- ============================================================================
-- ROI & Performance Tracking Tables
-- ============================================================================

-- Track ROI per rebalance event
CREATE TABLE IF NOT EXISTS rebalance_roi (
    id SERIAL PRIMARY KEY,
    rebalance_id INT REFERENCES rebalance_proposals(id),
    tx_hash VARCHAR(255),
    entry_portfolio_value_usd FLOAT NOT NULL, -- Portfolio value at rebalance start
    entry_timestamp TIMESTAMP NOT NULL,
    allocation_from JSONB, -- Previous allocation {protocol: percentage}
    allocation_to JSONB,   -- New allocation {protocol: percentage}
    gas_cost_usd FLOAT DEFAULT 0,
    slippage_percent FLOAT DEFAULT 0,
    net_cost_usd FLOAT, -- gas_cost + slippage
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_rebalance_roi_tx_hash ON rebalance_roi (tx_hash);
CREATE INDEX idx_rebalance_roi_timestamp ON rebalance_roi (entry_timestamp DESC);

-- Snapshot of ROI calculation at different time intervals
CREATE TABLE IF NOT EXISTS roi_snapshots (
    id SERIAL PRIMARY KEY,
    rebalance_roi_id INT REFERENCES rebalance_roi(id),
    snapshot_date TIMESTAMP NOT NULL,
    current_portfolio_value_usd FLOAT NOT NULL,
    yield_earned_usd FLOAT DEFAULT 0, -- Yield from protocols since rebalance
    realized_gains_usd FLOAT DEFAULT 0, -- Gains from price moves
    unrealized_gains_usd FLOAT DEFAULT 0, -- Unrealized P&L
    total_gain_loss_usd FLOAT, -- Total P&L (yield + realized + unrealized)
    roi_percent FLOAT, -- ROI% = (total_gain_loss / entry_value) * 100
    apy_achieved_percent FLOAT, -- Annualized return based on holding period
    status VARCHAR(50) DEFAULT 'active', -- 'active', 'closed'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_roi_snapshots_rebalance ON roi_snapshots (rebalance_roi_id);
CREATE INDEX idx_roi_snapshots_date ON roi_snapshots (snapshot_date DESC);

-- Cumulative ROI statistics
CREATE TABLE IF NOT EXISTS roi_summary (
    id SERIAL PRIMARY KEY,
    summary_date DATE NOT NULL UNIQUE,
    total_rebalances_count INT DEFAULT 0,
    cumulative_portfolio_gain_usd FLOAT DEFAULT 0,
    cumulative_roi_percent FLOAT DEFAULT 0, -- Overall ROI since inception
    net_yield_earned_usd FLOAT DEFAULT 0, -- Gross yield
    total_gas_cost_usd FLOAT DEFAULT 0, -- Total gas spent
    total_slippage_usd FLOAT DEFAULT 0, -- Total slippage
    best_rebalance_roi_percent FLOAT,
    worst_rebalance_roi_percent FLOAT,
    avg_rebalance_roi_percent FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_roi_summary_date ON roi_summary (summary_date DESC);

-- ============================================================================
-- Audit & Logging Tables
-- ============================================================================

-- Audit log for all important actions
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    action VARCHAR(255) NOT NULL,
    entity_type VARCHAR(100),
    entity_id INT,
    user_id VARCHAR(255),
    changes TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_log_timestamp ON audit_log (timestamp DESC);
CREATE INDEX idx_audit_log_entity ON audit_log (entity_type, entity_id);

-- ============================================================================
-- Views for Common Queries
-- ============================================================================

-- Latest protocol yields by protocol
CREATE OR REPLACE VIEW latest_protocol_yields AS
SELECT DISTINCT ON (protocol_id, asset)
    py.id,
    py.protocol_id,
    py.asset,
    py.apy_percent,
    py.total_liquidity_usd,
    py.available_liquidity_usd,
    py.utilization_ratio,
    py.recorded_at
FROM protocol_yields py
ORDER BY py.protocol_id, py.asset, py.recorded_at DESC;

-- Latest risk scores
CREATE OR REPLACE VIEW latest_risk_scores AS
SELECT DISTINCT ON (protocol_id)
    prs.id,
    prs.protocol_id,
    prs.risk_score,
    prs.audit_score,
    prs.exploit_count,
    prs.security_incidents,
    prs.recorded_at
FROM protocol_risk_scores prs
ORDER BY prs.protocol_id, prs.recorded_at DESC;

-- Protocol performance ranking
CREATE OR REPLACE VIEW protocol_rankings AS
SELECT
    p.id,
    p.name,
    p.symbol,
    COALESCE(lpy.apy_percent, 0) as apy_percent,
    COALESCE(lrs.risk_score, 50) as risk_score,
    (COALESCE(lpy.apy_percent, 0) - (COALESCE(lrs.risk_score, 50) / 10.0)) as risk_adjusted_yield,
    COALESCE(lpy.total_liquidity_usd, 0) as total_liquidity_usd,
    COALESCE(lpy.recorded_at, CURRENT_TIMESTAMP) as data_timestamp
FROM protocols p
LEFT JOIN latest_protocol_yields lpy ON p.id = lpy.protocol_id AND lpy.asset = 'USDC'
LEFT JOIN latest_risk_scores lrs ON p.id = lrs.protocol_id;

-- ============================================================================
-- Sample Data (for testing)
-- ============================================================================

-- Insert sample protocols
INSERT INTO protocols (name, symbol, address, protocol_type) VALUES
    ('Aave V3', 'AAVE', '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2', 'lending'),
    ('Curve Finance', 'CRV', '0xD51a44d3FDF375bD38e886bdD2629d1b78b88D3B', 'amm'),
    ('Uniswap V3', 'UNI', '0xE592427A0AEce92De3Edee1F18E0157C05861564', 'dex')
ON CONFLICT (name) DO NOTHING;

-- ============================================================================
-- ML Data Ingestion Tables (Synced from TimeseriesDB logic)
-- ============================================================================

-- Raw yield data for ML Training (Compatible with yield_snapshots in TimeseriesDB)
CREATE TABLE IF NOT EXISTS yield_snapshots (
    id SERIAL PRIMARY KEY,
    time TIMESTAMP NOT NULL,
    pool_id VARCHAR(100) NOT NULL,
    protocol VARCHAR(100),
    symbol VARCHAR(50),
    apy_total FLOAT,
    apy_base FLOAT,
    apy_reward FLOAT,
    tvl_usd FLOAT,
    il_risk VARCHAR(50),
    is_outlier BOOLEAN DEFAULT FALSE,
    chain VARCHAR(50) DEFAULT 'ethereum',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pool_id, time)
);

CREATE INDEX IF NOT EXISTS idx_yield_snapshots_time ON yield_snapshots (time DESC);
CREATE INDEX IF NOT EXISTS idx_yield_snapshots_pool ON yield_snapshots (pool_id, time DESC);
