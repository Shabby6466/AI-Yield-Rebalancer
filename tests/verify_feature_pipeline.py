
import os
import pandas as pd
from dotenv import load_dotenv
from src.ml.feature_pipeline import FeaturePipeline

load_dotenv()

def verify_pipeline():
    print("Testing FeaturePipeline with yield_snapshots...")
    
    try:
        pipeline = FeaturePipeline()
        
        # 1. Load Data
        print("\n1. Loading Data...")
        df = pipeline.load_data()
        
        if df.empty:
            print("⚠️ No data loaded. Check database content.")
            return

        print(f"✅ Loaded {len(df)} records.")
        print(f"Columns: {df.columns.tolist()}")
        
        # 2. Check for pool_id
        if 'pool_id' not in df.columns:
            print("❌ 'pool_id' column missing! Fix mapping.")
            return
            
        # 3. Create Features
        print("\n2. Creating Features...")
        df_features = pipeline.create_features(df)
        print(f"✅ Features created: {df_features.shape}")
        
        # 4. Prepare Sequences
        print("\n3. Preparing Sequences...")
        X, y, meta = pipeline.prepare_sequences(df_features, sequence_length=5, prediction_horizon=1)
        
        if len(X) > 0:
            print(f"✅ Created {len(X)} sequences.")
            print(f"Sample Meta: {meta[0]}")
        else:
            print("⚠️ No sequences created (might need more data history).")
            
    except Exception as e:
        print(f"❌ Pipeline Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_pipeline()
