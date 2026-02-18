
import os
import logging
import sys
import uuid
from datetime import datetime, timedelta
from dotenv import load_dotenv
import psycopg2
from src.backtest.prediction_tracker import PredictionTracker

load_dotenv()
from src.backtest.prediction_tracker import PredictionTracker

# Setup Logging to console
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def force_validation():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL not set")
        return

    try:
        # 1. Setup DB connection
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        
        # 2. Mock a generic pool object for the tracker if needed, 
        # but tracker just needs connection pool. 
        # We can use a simple class to mimic the pool.
        class SimplePool:
            def getconn(self): return psycopg2.connect(db_url)
            def putconn(self, conn): conn.close()
            
        pool = SimplePool()
        tracker = PredictionTracker(pool, validation_days=0) # 0 days to validate immediately
        
        # 3. Clean up old test data (optional, but good for clarity)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_predictions WHERE reason LIKE '%[TEST_VALIDATION]%'")
        
        
        # 4. Insert a fake prediction that is ready for validation
        print("Inserting fake prediction...")
        pool_a_id = str(uuid.uuid4())
        pool_b_id = str(uuid.uuid4())
        
        pred_id = tracker.record_prediction({
            "prediction_type": "REBALANCE",
            "current_pool_id": pool_a_id,
            "current_pool_apy": 5.0,
            "target_pool_id": pool_b_id,
            "target_pool_apy": 10.0,
            "confidence": 0.9,
            "reason": "[TEST_VALIDATION] forcing log check",
            "capital_usd": 10000,
            "gas_cost": 5.0,
            "slippage": 5.0
        })
        
        # 5. Insert fake yield data for these pools into yield_logs (or yield_snapshots?)
        # Tracker uses yield_logs in auto_validate_from_db.
        # Ensure yield_logs table exists or use whatever table tracker uses.
        # Checking tracker code: "FROM yield_logs"
        # Wait, does yield_logs exist? feature_pipeline uses yield_snapshots.
        # I should check if yield_logs exists. If not, I might need to create it or update tracker to use yield_snapshots.
        
        # Let's verify table existence first.
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('yield_snapshots');")
            if not cur.fetchone()[0]:
                print("⚠️ yield_snapshots table missing! Creating it for test...")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS yield_snapshots (
                        time TIMESTAMPTZ DEFAULT NOW(),
                        pool_id TEXT,
                        protocol TEXT,
                        symbol TEXT,
                        apy_total DOUBLE PRECISION,
                        tvl_usd DOUBLE PRECISION
                    );
                """)

            # Insert mock yield data
            # Use timestamps slightly in the future or past? 
            # Tracker selects "ORDER BY time DESC LIMIT 2"
            print("Inserting mock yield snapshots...")
            cur.execute("""
                INSERT INTO yield_snapshots (pool_id, protocol, symbol, apy_total, tvl_usd, time) 
                VALUES (%s, 'mock_proto', 'MOCK-A', 5.5, 1000000, NOW())
            """, (pool_a_id,))
            cur.execute("""
                INSERT INTO yield_snapshots (pool_id, protocol, symbol, apy_total, tvl_usd, time) 
                VALUES (%s, 'mock_proto', 'MOCK-B', 12.0, 2000000, NOW())
            """, (pool_b_id,))
        
        # 6. Trigger Validation
        print("\n--- TRIGGERING VALIDATION ---")
        tracker.auto_validate_from_db()
        print("-----------------------------\n")
        
    except Exception as e:
        print(f"❌ Test Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    force_validation()
