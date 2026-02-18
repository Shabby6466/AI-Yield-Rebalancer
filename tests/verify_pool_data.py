
import os
import pandas as pd
from dotenv import load_dotenv
from src.ml.feature_pipeline import FeaturePipeline
import psycopg2

load_dotenv()

def verify_pool_loading():
    print("Testing FeaturePipeline.load_data_for_pool...")
    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL not set")
        return

    try:
        # 1. Get a standard pool ID from DB to test with
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT pool_id FROM yield_snapshots LIMIT 1;")
        res = cur.fetchone()
        conn.close()
        
        if not res:
            print("⚠️ No data in yield_snapshots to test with.")
            return
            
        test_pool_id = str(res[0])
        print(f"Testing with Pool ID: {test_pool_id}")
        
        # 2. Initialize Pipeline
        pipeline = FeaturePipeline()
        
        # 3. Load Data for that pool
        df = pipeline.load_data_for_pool(test_pool_id)
        
        if df.empty:
            print(f"⚠️ No data loaded for pool {test_pool_id}.")
        else:
            print(f"✅ Loaded {len(df)} records for pool {test_pool_id}")
            print(f"Columns: {df.columns.tolist()}")

        # 4. Test Batch Load
        print("\nTesting FeaturePipeline.load_data (Validation batch)...")
        df_batch = pipeline.load_data(start_date="2023-01-01")
        if not df_batch.empty:
             print(f"✅ Batch loaded {len(df_batch)} records.")
        else:
             print("⚠️ Batch load returned empty (might be expected if no data > 2023).")
            
    except Exception as e:
        print(f"❌ Test Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_pool_loading()
