-- ML Prediction & Execution History Tables
-- For use with MLPredictionService

CREATE TABLE IF NOT EXISTS ml_predictions (
    prediction_id SERIAL PRIMARY KEY,
    network VARCHAR(50) NOT NULL,
    pool_address VARCHAR(42) NOT NULL,
    asset_address VARCHAR(42) NOT NULL,
    protocol_name VARCHAR(100),
    predicted_apy FLOAT NOT NULL,
    risk_level VARCHAR(20) NOT NULL,
    confidence_score FLOAT NOT NULL,
    model_version VARCHAR(20) NOT NULL,
    lstm_prediction FLOAT,
    xgboost_risk_score FLOAT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rebalance_history (
    rebalance_id SERIAL PRIMARY KEY,
    network VARCHAR(50) NOT NULL,
    vault_address VARCHAR(42) NOT NULL,
    asset_address VARCHAR(42) NOT NULL,
    total_assets NUMERIC(38, 0) NOT NULL,
    tx_hash VARCHAR(66) NOT NULL,
    gas_used BIGINT,
    gas_price BIGINT,
    status VARCHAR(20) NOT NULL,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pool_allocations (
    allocation_id SERIAL PRIMARY KEY,
    rebalance_id INTEGER REFERENCES rebalance_history(rebalance_id),
    pool_address VARCHAR(42) NOT NULL,
    allocation_percentage INT NOT NULL,
    allocated_amount NUMERIC(38, 0) NOT NULL,
    pool_apy FLOAT,
    predicted_apy FLOAT,
    risk_level VARCHAR(20),
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ml_predictions_pool ON ml_predictions (pool_address, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_rebalance_history_tx ON rebalance_history (tx_hash);
