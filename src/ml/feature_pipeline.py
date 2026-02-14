"""
Feature Engineering Pipeline for ML Models
Loads data from PostgreSQL and creates ML-ready features
"""

import pandas as pd
import numpy as np
import psycopg2
from typing import Tuple, Optional
from datetime import datetime, timedelta
import logging
from dotenv import load_dotenv
import os

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FeaturePipeline:
    """Transform raw yield data into ML features"""
    
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or os.getenv('DATABASE_URL')
        
    def load_data(self, start_date: Optional[str] = None, 
                  end_date: Optional[str] = None) -> pd.DataFrame:
        """Load data from PostgreSQL"""
        
        query = """
            SELECT 
                ym.time,
                ym.asset_id,
                a.protocol,
                a.symbol,
                a.pool_id,
                ym.apy_percent,
                ym.tvl_usd,
                ym.volume_24h_usd,
                ym.volatility_24h,
                ym.utilization_rate
            FROM yield_metrics ym
            JOIN assets a ON ym.asset_id = a.id
        """
        
        if start_date or end_date:
            query += " WHERE 1=1"
            if start_date:
                query += f" AND ym.time >= '{start_date}'"
            if end_date:
                query += f" AND ym.time <= '{end_date}'"
                
        query += " ORDER BY ym.asset_id, ym.time"
        
        logger.info(f"Loading data from database...")
        conn = psycopg2.connect(self.db_url)
        df = pd.read_sql(query, conn)
        conn.close()
        
        logger.info(f"✓ Loaded {len(df):,} records from {df['time'].min()} to {df['time'].max()}")
        logger.info(f"  - Assets: {df['asset_id'].nunique()}")
        logger.info(f"  - Protocols: {', '.join(df['protocol'].unique())}")
        
        return df
    
    def create_features(self, df: pd.DataFrame, lookback_days: int = 30) -> pd.DataFrame:
        """
        Create time-series features for each asset
        
        Features created:
        - Temporal: hour, day_of_week, day_of_month
        - Lag features: apy_lag_1d, apy_lag_7d
        - Rolling statistics: apy_ma_7d, apy_ma_30d, apy_std_7d, tvl_ma_7d
        - Trends: apy_trend_7d, tvl_trend_7d, volume_trend_7d
        - Ratios: volume_tvl_ratio
        """
        
        logger.info("Creating features...")
        df = df.copy()
        df = df.sort_values(['asset_id', 'time'])
        
        # Temporal features
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['day_of_week'] = pd.to_datetime(df['time']).dt.dayofweek
        df['day_of_month'] = pd.to_datetime(df['time']).dt.day
        
        # Protocol one-hot encoding
        protocol_dummies = pd.get_dummies(df['protocol'], prefix='protocol')
        df = pd.concat([df, protocol_dummies], axis=1)
        
        # Per-asset rolling features
        feature_df_list = []
        
        for asset_id in df['asset_id'].unique():
            asset_df = df[df['asset_id'] == asset_id].copy()
            asset_df = asset_df.sort_values('time')
            
            # Lag features
            asset_df['apy_lag_1d'] = asset_df['apy_percent'].shift(1)
            asset_df['apy_lag_7d'] = asset_df['apy_percent'].shift(7)
            asset_df['tvl_lag_1d'] = asset_df['tvl_usd'].shift(1)
            
            # Rolling means
            asset_df['apy_ma_7d'] = asset_df['apy_percent'].rolling(7, min_periods=1).mean()
            asset_df['apy_ma_30d'] = asset_df['apy_percent'].rolling(30, min_periods=1).mean()
            asset_df['tvl_ma_7d'] = asset_df['tvl_usd'].rolling(7, min_periods=1).mean()
            asset_df['tvl_ma_30d'] = asset_df['tvl_usd'].rolling(30, min_periods=1).mean()
            
            # Rolling std (volatility)
            asset_df['apy_std_7d'] = asset_df['apy_percent'].rolling(7, min_periods=1).std()
            asset_df['apy_std_30d'] = asset_df['apy_percent'].rolling(30, min_periods=1).std()
            asset_df['tvl_std_7d'] = asset_df['tvl_usd'].rolling(7, min_periods=1).std()
            
            # Trends (% change over window)
            asset_df['apy_trend_7d'] = asset_df['apy_percent'].pct_change(7) * 100
            asset_df['tvl_trend_7d'] = asset_df['tvl_usd'].pct_change(7) * 100
            
            # Volume features (if available)
            if 'volume_24h_usd' in asset_df.columns:
                asset_df['volume_ma_7d'] = asset_df['volume_24h_usd'].rolling(7, min_periods=1).mean()
                asset_df['volume_trend_7d'] = asset_df['volume_24h_usd'].pct_change(7) * 100
                asset_df['volume_tvl_ratio'] = asset_df['volume_24h_usd'] / (asset_df['tvl_usd'] + 1e-10)
            
            # Momentum indicators
            asset_df['apy_momentum'] = asset_df['apy_ma_7d'] - asset_df['apy_ma_30d']
            asset_df['tvl_momentum'] = asset_df['tvl_ma_7d'] - asset_df['tvl_ma_30d']
            
            feature_df_list.append(asset_df)
        
        df_features = pd.concat(feature_df_list, ignore_index=True)
        
        # Fill NaNs from rolling windows
        df_features = df_features.fillna(method='bfill').fillna(0)
        
        logger.info(f"✓ Created {len(df_features.columns)} features")
        
        return df_features
    
    def prepare_sequences(self, df: pd.DataFrame, 
                         sequence_length: int = 30,
                         prediction_horizon: int = 7,
                         feature_cols: Optional[list] = None) -> Tuple[np.ndarray, np.ndarray, list]:
        """
        Prepare sequences for time-series models (LSTM)
        
        Args:
            df: DataFrame with features
            sequence_length: Number of timesteps to look back
            prediction_horizon: Days ahead to predict
            feature_cols: List of feature columns to use
            
        Returns:
            X: Input sequences (samples, sequence_length, features)
            y: Target values (samples,)
            metadata: List of dicts with timestamp, asset info
        """
        
        if feature_cols is None:
            # Default feature set
            feature_cols = [
                'apy_percent', 'tvl_usd', 'volume_24h_usd',
                'apy_ma_7d', 'apy_ma_30d', 'apy_std_7d',
                'tvl_ma_7d', 'tvl_trend_7d', 'volume_tvl_ratio',
                'apy_momentum', 'tvl_momentum',
                'hour', 'day_of_week', 'day_of_month'
            ]
            # Add protocol columns
            protocol_cols = [c for c in df.columns if c.startswith('protocol_')]
            feature_cols.extend(protocol_cols)
            
            # Filter to existing columns
            feature_cols = [c for c in feature_cols if c in df.columns]
        
        logger.info(f"Using {len(feature_cols)} features: {', '.join(feature_cols[:10])}...")
        
        X_list = []
        y_list = []
        metadata_list = []
        
        for asset_id in df['asset_id'].unique():
            asset_df = df[df['asset_id'] == asset_id].copy()
            asset_df = asset_df.sort_values('time').reset_index(drop=True)
            
            # Skip if insufficient data
            if len(asset_df) < sequence_length + prediction_horizon:
                continue
            
            # Extract feature matrix
            features = asset_df[feature_cols].values
            targets = asset_df['apy_percent'].values
            
            # Create sequences
            for i in range(len(asset_df) - sequence_length - prediction_horizon + 1):
                X_sequence = features[i:i + sequence_length]
                # Improved Target Logic: Predict the average APY over the next N days
                # instead of just a single point in the future to reduce volatility.
                y_target = np.mean(targets[i + sequence_length:i + sequence_length + prediction_horizon])
                
                X_list.append(X_sequence)
                y_list.append(y_target)
                
                metadata_list.append({
                    'asset_id': asset_id,
                    'protocol': asset_df.iloc[i]['protocol'],
                    'symbol': asset_df.iloc[i]['symbol'],
                    'timestamp': asset_df.iloc[i + sequence_length - 1]['time']
                })
        
        X = np.array(X_list)
        y = np.array(y_list)
        
        logger.info(f"✓ Created {len(X):,} sequences")
        logger.info(f"  - Shape: X={X.shape}, y={y.shape}")
        
        return X, y, metadata_list
    
    def split_data(self, X: np.ndarray, y: np.ndarray, metadata: list,
                   test_size: float = 0.2, val_size: float = 0.1) -> dict:
        """
        Time-series split (no shuffling)
        """
        
        n = len(X)
        test_idx = int(n * (1 - test_size))
        val_idx = int(test_idx * (1 - val_size))
        
        split = {
            'X_train': X[:val_idx],
            'y_train': y[:val_idx],
            'X_val': X[val_idx:test_idx],
            'y_val': y[val_idx:test_idx],
            'X_test': X[test_idx:],
            'y_test': y[test_idx:],
            'meta_train': metadata[:val_idx],
            'meta_val': metadata[val_idx:test_idx],
            'meta_test': metadata[test_idx:]
        }
        
        logger.info(f"✓ Split data:")
        logger.info(f"  - Train: {len(split['X_train']):,} samples")
        logger.info(f"  - Val:   {len(split['X_val']):,} samples")
        logger.info(f"  - Test:  {len(split['X_test']):,} samples")
        
        return split


if __name__ == "__main__":
    # Test the pipeline
    pipeline = FeaturePipeline()
    
    # Load data
    df = pipeline.load_data()
    
    # Create features
    df_features = pipeline.create_features(df)
    
    # Prepare sequences
    X, y, metadata = pipeline.prepare_sequences(df_features, sequence_length=30, prediction_horizon=7)
    
    # Split data
    data = pipeline.split_data(X, y, metadata)
    
    print("\n" + "="*60)
    print("Feature Pipeline Test Complete")
    print("="*60)
    print(f"Train samples: {len(data['X_train'])}")
    print(f"Val samples: {len(data['X_val'])}")
    print(f"Test samples: {len(data['X_test'])}")
    print(f"Feature dims: {data['X_train'].shape[-1]}")
