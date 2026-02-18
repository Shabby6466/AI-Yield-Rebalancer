import pandas as pd
import numpy as np
from src.ml.feature_pipeline import FeaturePipeline
from datetime import datetime, timedelta

def verify_inference_features():
    print("Verifying inference feature dimensions...")
    
    # 1. Initialize Pipeline
    pipeline = FeaturePipeline()
    
    # 2. Create mock data for a SINGLE pool
    # The error was "Expected 19, got 15"
    # Old logic: 14 base + 1 protocol = 15
    # New logic: 16 (14 + gas + sentiment) + 5 protocols = 21? 
    # Wait, 14 base + 5 protocols = 19.
    # Let's see what the current pipeline produces.
    
    data = []
    pool_id = "test-pool"
    base_time = datetime.utcnow()
    
    for i in range(40):
        data.append({
            'time': base_time - timedelta(days=40-i),
            'pool_id': pool_id,
            'protocol': 'aave-v3', # Only one protocol
            'symbol': 'USDC',
            'apy_percent': 5.0 + i * 0.1,
            'tvl_usd': 1000000,
            'volume_24h_usd': 100000,
            'volatility_24h': 0,
            'utilization_rate': 0
        })
    
    df = pd.DataFrame(data)
    
    # 3. Create Features
    print("Creating features...")
    df_features = pipeline.create_features(df)
    
    # 4. Prepare Sequences
    print("Preparing sequences...")
    X, y, metadata = pipeline.prepare_sequences(df_features, sequence_length=30)
    
    print(f"X shape: {X.shape}")
    print(f"Feature dimension: {X.shape[-1]}")
    
    expected_dim = 19 # My calculated expected dim based on 14 base + 5 protocols
    # Wait, I added gas_price_gwei and market_sentiment to the default list.
    # So 14 + 2 + 5 = 21?
    # Let's check the default list in FeaturePipeline.
    
    if X.shape[-1] == expected_dim:
        print(f"✅ Success! Feature dimension is {X.shape[-1]}")
    else:
        print(f"❌ Mismatch! Feature dimension is {X.shape[-1]}, expected {expected_dim}")
        # List the actual columns used
        feature_cols = [
            'apy_percent', 'tvl_usd', 'volume_24h_usd',
            'apy_ma_7d', 'apy_ma_30d', 'apy_std_7d',
            'tvl_ma_7d', 'tvl_trend_7d', 'volume_tvl_ratio',
            'apy_momentum', 'tvl_momentum',
            'hour', 'day_of_week', 'day_of_month',
            'gas_price_gwei', 'market_sentiment'
        ]
        protocol_cols = [f'protocol_{p}' for p in pipeline.protocol_list]
        feature_cols.extend(protocol_cols)
        print(f"Active features ({len(feature_cols)}): {feature_cols}")

if __name__ == "__main__":
    verify_inference_features()
