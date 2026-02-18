import asyncio
import logging
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mocking external dependencies to avoid network calls during test
from unittest.mock import MagicMock
sys.modules['src.data.defillama_client'] = MagicMock()
sys.modules['src.data.defillama_client'] = MagicMock()
sys.modules['src.data.defillama_ingestor'] = MagicMock()
sys.modules['src.optimizer.gas'] = MagicMock()
sys.modules['psycopg2'] = MagicMock()
sys.modules['psycopg2.pool'] = MagicMock()
sys.modules['src.backtest.prediction_tracker'] = MagicMock()

# Mocking settings to avoid loading .env or connecting to real DB
mock_settings = MagicMock()
mock_settings.APP_NAME = "Test App"
sys.modules['src.core.config'] = MagicMock()
sys.modules['src.core.config'].settings = mock_settings

# Ensure os.getenv("DATABASE_URL") returns None or something harmless if mocked
os.environ["DATABASE_URL"] = "" 
os.environ["DB_URL"] = ""

# Now import server
from src.api.server import app, startup_event, predict_yield_opportunity, clients, RebalanceRequest

# Setup logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_server_ml")

async def run_verification():
    logger.info("--- 🧪 Starting Server ML Verification ---")
    
    # 1. Test Startup
    logger.info("1. Implementation Test: Startup Event")
    await startup_event()
    
    # Check if ML_AVAILABLE flag was handled correctly
    from src.api.server import ML_AVAILABLE
    logger.info(f"ML_AVAILABLE status: {ML_AVAILABLE}")
    
    if not ML_AVAILABLE:
        logger.info("✅ Correctly detected missing ML libraries.")
        if not clients.get('lstm_ready') and not clients.get('risk_ready'):
             logger.info("✅ ML flags correctly set to False.")
        else:
             logger.error("❌ ML flags incorrect.")
    else:
        logger.warning("Unanticipated: ML libraries found? (Check environment)")

    # 2. Test Prediction Endpoint (Shadow Mode)
    logger.info("\n2. Implementation Test: Predict Endpoint")
    
    # Mock dependencies in clients using AsyncMock where needed
    from unittest.mock import AsyncMock
    
    mock_defillama = MagicMock()
    mock_defillama.fetch_pool_yields = AsyncMock(return_value=[{'pool': 'test-pool', 'apy': 5.0}])
    mock_defillama.fetch_top_pools = AsyncMock(return_value=[{'pool': 'target-pool', 'apy': 10.0, 'tvlUsd': 1000000, 'symbol': 'USDC', 'project': 'aave'}])
    clients['defillama'] = mock_defillama
    
    mock_sizer = MagicMock()
    
    # Create a Mock object that supports both attribute access and .get()
    class MockAllocation:
        def __init__(self, pool_id, tvl_usd, allocation_usd):
            self.pool_id = pool_id
            self.tvl_usd = tvl_usd
            self.allocation_usd = allocation_usd
        
        def get(self, key, default=None):
            return getattr(self, key, default)

    # TradeSizer.recommend is synchronous
    mock_sizer.recommend.return_value = {
        'weighted_apy': 10.0, 
        'allocations': [MockAllocation(pool_id='target-pool', tvl_usd=1000000, allocation_usd=10000)]
    }
    clients['sizer'] = mock_sizer
    
    mock_gas = MagicMock()
    # GasOptimizer.should_rebalance is synchronous
    mock_gas.should_rebalance.return_value = (True, 50.0, {'monthly_gain': 100.0})
    clients['gas'] = mock_gas
    
    # Mock FeaturePipeline to test Shadow Mode logic
    import pandas as pd
    mock_pipeline = MagicMock()
    # Mock load_data_for_pool to return a valid DataFrame
    mock_pipeline.load_data_for_pool.return_value = pd.DataFrame({
        'time': pd.date_range(start='2023-01-01', periods=60, freq='D'),
        'apy_percent': [5.0]*60,
        'tvl_usd': [1000000]*60
    })
    # Mock create_features
    mock_pipeline.create_features.return_value = pd.DataFrame({'feature1': [1]*60})
    # Mock prepare_sequences
    mock_pipeline.prepare_sequences.return_value = ([1], [1], [1]) # Non-empty sequence
    # Mock get_risk_features
    mock_pipeline.get_risk_features.return_value = pd.DataFrame({'risk_feat': [1]})
    
    clients['feature_pipeline'] = mock_pipeline
    
    # Create valid request
    req = RebalanceRequest(
        portfolio_id="test_port",
        current_allocations={"test-pool": 10000.0},
        force_execution=False
    )
    
    try:
        response = await predict_yield_opportunity(req)
        logger.info("✅ Prediction response received.")
        
        # Check for market_context
        market_context = response.market_context
        if market_context:
            logger.info("✅ Market context present.")
            if 'ml_shadow_mode' in market_context:
                 logger.info(f"ℹ️ ML Shadow Mode Data: {market_context.get('ml_shadow_mode')}")
                 # Verify structure
                 shadow = market_context['ml_shadow_mode']
                 if 'lstm_predicted_apy_7d' in shadow:
                     logger.info("✅ Shadow Mode data structure validation passed.")
                 else:
                     logger.error("❌ Shadow Mode data missing keys.")
            else:
                 logger.warning("⚠️ ML Shadow Mode key missing (FeaturePipeline might be None or skipped).")
        else:
            logger.error("❌ Market context missing.")
            
    except Exception as e:
        logger.error(f"❌ Prediction failed: {e}", exc_info=True)

    logger.info("\n--- 🏁 Verification Complete ---")

if __name__ == "__main__":
    asyncio.run(run_verification())
