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
from sqlalchemy import create_engine, text
import psycopg2 # Keep for error handling if needed, or remove if unused

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FeaturePipeline:
    """Transform raw yield data into ML features"""
    
    def __init__(self, db_url: Optional[str] = None, protocol_list: Optional[list] = None):
        self.db_url = db_url or os.getenv('DATABASE_URL')
        self.engine = create_engine(self.db_url)
        # Standard protocols seen during training to ensure consistent dimensions
        self.protocol_list = protocol_list or [
            'uniswap-v3', 'curve-dex', 'compound-v3', 'mock_proto', 'aave-v3'
        ]
        
    def load_data(self, start_date: Optional[str] = None, 
                  end_date: Optional[str] = None) -> pd.DataFrame:
        """Load data from PostgreSQL"""
        
        # SQLAlchemy parameterized query
        query_text = """
            SELECT 
                time,
                pool_id,
                protocol,
                symbol,
                apy_total as apy_percent,
                tvl_usd,
                0 as volume_24h_usd,
                0 as volatility_24h,
                0 as utilization_rate
            FROM yield_snapshots
            WHERE 1=1
        """
        params = {}
        
        if start_date:
            query_text += " AND time >= :start_date"
            params['start_date'] = start_date
        if end_date:
            query_text += " AND time <= :end_date"
            params['end_date'] = end_date
                
        query_text += " ORDER BY pool_id, time"
        
        logger.info(f"Loading data from database...")
        with self.engine.connect() as conn:
            df = pd.read_sql(text(query_text), conn, params=params)
        
        logger.info(f"✓ Loaded {len(df):,} records from {df['time'].min()} to {df['time'].max()}")
        logger.info(f"  - Pools: {df['pool_id'].nunique()}")
        logger.info(f"  - Protocols: {', '.join(df['protocol'].unique())}")
        
        return df

    def load_data_for_pool(self, pool_id: str, start_date: Optional[str] = None) -> pd.DataFrame:
        """Load data for a specific pool"""
        # SQLAlchemy uses :param style for bound parameters
        query = text("""
            SELECT 
                time,
                pool_id,
                protocol,
                symbol,
                apy_total as apy_percent,
                tvl_usd,
                0 as volume_24h_usd,
                0 as volatility_24h,
                0 as utilization_rate
            FROM yield_snapshots
            WHERE pool_id = :pool_id
        """)
        params = {"pool_id": pool_id}
        
        if start_date:
            query = text(str(query) + " AND time >= :start_date")
            params["start_date"] = start_date
            
        # Add order by (careful with text object string manipulation)
        # Better to construct string first then wrap in text()
        
        query_str = """
            SELECT 
                time,
                pool_id,
                protocol,
                symbol,
                apy_total as apy_percent,
                tvl_usd,
                0 as volume_24h_usd,
                0 as volatility_24h,
                0 as utilization_rate
            FROM yield_snapshots
            WHERE pool_id = :pool_id
        """
        if start_date:
            query_str += " AND time >= :start_date"
        
        query_str += " ORDER BY time ASC"
        
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text(query_str), conn, params=params)
            return df
        except Exception as e:
            logger.error(f"Failed to load data for pool {pool_id}: {e}")
            return pd.DataFrame()
    
    def create_features(self, df: pd.DataFrame, lookback_days: int = 30, 
                       gas_price_wei: float = 20000000000, 
                       market_sentiment: float = 0.5) -> pd.DataFrame:
        """
        Create time-series features for each asset
        
        Args:
            df: Raw data dataframe
            lookback_days: Window size
            gas_price_wei: Current network gas price (global context)
            market_sentiment: 0-1 score (0=Bear, 1=Bull) or volatility index
        
        Features created:
        - Temporal: hour, day_of_week, day_of_month
        - Lag features: apy_lag_1d, apy_lag_7d
        - Rolling statistics: apy_ma_7d, apy_ma_30d, apy_std_7d, tvl_ma_7d
        - Trends: apy_trend_7d, tvl_trend_7d, volume_trend_7d
        - Ratios: volume_tvl_ratio
        - Contextual: gas_price, sentiment
        """
        
        logger.info("Creating features...")
        df = df.copy()
        df = df.sort_values(['pool_id', 'time'])
        
        # Temporal features
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['day_of_week'] = pd.to_datetime(df['time']).dt.dayofweek
        df['day_of_month'] = pd.to_datetime(df['time']).dt.day
        
        # Contextual Features (Broadcast to all rows)
        # In a real pipeline, these would be joined on timestamp
        df['gas_price_gwei'] = gas_price_wei / 1e9
        df['market_sentiment'] = market_sentiment
        
        # Protocol one-hot encoding (Fixed categories to ensure consistent dimensionality)
        df['protocol'] = pd.Categorical(df['protocol'], categories=self.protocol_list)
        protocol_dummies = pd.get_dummies(df['protocol'], prefix='protocol')
        df = pd.concat([df, protocol_dummies], axis=1)
        
        # Per-asset rolling features
        feature_df_list = []
        
        for pool_id in df['pool_id'].unique():
            asset_df = df[df['pool_id'] == pool_id].copy()
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
        
        # Fill NaNs from rolling windows (Avoid filling categorical 'protocol' with 0)
        numeric_cols = df_features.select_dtypes(include=[np.number]).columns
        df_features[numeric_cols] = df_features[numeric_cols].bfill().fillna(0)
        
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
            # Default feature set (Matches model expected 19 dims: 14 base + 5 protocols)
            feature_cols = [
                'apy_percent', 'tvl_usd', 'volume_24h_usd',
                'apy_ma_7d', 'apy_ma_30d', 'apy_std_7d',
                'tvl_ma_7d', 'tvl_trend_7d', 'volume_tvl_ratio',
                'apy_momentum', 'tvl_momentum',
                'hour', 'day_of_week', 'day_of_month'
            ]
            # Add protocol columns (5 fixed columns)
            protocol_cols = [f'protocol_{p}' for p in self.protocol_list]
            feature_cols.extend(protocol_cols)
            
            # Filter to existing columns
            feature_cols = [c for c in feature_cols if c in df.columns]
        
        logger.info(f"Using {len(feature_cols)} features: {', '.join(feature_cols[:10])}...")
        
        X_list = []
        y_list = []
        metadata_list = []
        
        for pool_id in df['pool_id'].unique():
            asset_df = df[df['pool_id'] == pool_id].copy()
            asset_df = asset_df.sort_values('time').reset_index(drop=True)
            
            # Skip if insufficient data
            if len(asset_df) < sequence_length + prediction_horizon:
                continue
            
            # Extract feature matrix
            features = asset_df[feature_cols].values.astype(np.float32)
            targets = asset_df['apy_percent'].values.astype(np.float32)
            
            # Create sequences
            for i in range(len(asset_df) - sequence_length - prediction_horizon + 1):
                X_sequence = features[i:i + sequence_length]
                # Improved Target Logic: Predict the average APY over the next N days
                # instead of just a single point in the future to reduce volatility.
                y_target = np.mean(targets[i + sequence_length:i + sequence_length + prediction_horizon])
                
                X_list.append(X_sequence)
                y_list.append(y_target)
                
                metadata_list.append({
                    'pool_id': pool_id,
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

    def get_risk_features(self, protocol_data: dict) -> pd.DataFrame:
        """
        Map external protocol data to the 45-feature vector expected by RiskScorer.
        Fills missing values with safe defaults.
        """
        # Map known keys from DefiLlama/Internal DB to RiskScorer keys
        features = {
            # Smart Contract Security
            'audit_score': protocol_data.get('audit_score', 50),
            'num_audits': protocol_data.get('audits', 0),
            'time_since_last_audit': protocol_data.get('days_since_audit', 365),
            'bug_bounty_exists': 1 if protocol_data.get('bug_bounty') else 0,
            'max_bounty_payout': protocol_data.get('bounty_amount', 0),
            'code_complexity': 10000, # Default
            'external_dependencies': 5, # Default
            'upgradeability_score': 50, # Default
            'admin_key_count': 1, # Default
            'timelock_duration': 24, # Default
            'historical_exploits': protocol_data.get('hacks', 0),
            'funds_lost_usd': 0,
            'immunefi_score': 50,
            'code_coverage': 80,
            'formal_verification': 0,
            
            # Protocol Maturity
            'days_since_deployment': protocol_data.get('age_days', 30),
            'cumulative_tvl_days': 1000000,
            'total_transactions': 1000,
            'unique_users': 100,
            'governance_decentralization': 50,
            'dao_maturity': 30,
            'team_doxxed': 1 if protocol_data.get('open_source') else 0,
            'venture_backing_usd': 0,
            'insurance_coverage': 0,
            'regulatory_compliance': 50,
            
            # Economic Risks
            'impermanent_loss_max': 0, # Assuming stablecoins mostly
            'liquidation_risk': 20,
            'oracle_reliability': 80, # Assuming Chainlink usually
            'stablecoin_peg_stability': 0.001,
            'collateral_diversity': 0.8,
            'debt_ceiling_util': 0.5,
            'bad_debt_ratio': 0,
            'reserve_ratio': 1.0,
            'token_inflation_rate': 0,
            'sell_pressure_score': 50,
            'liquidity_fragmentation': 20,
            'mev_exposure': 10,
            
            # Operational Risks
            'exit_liquidity': 80,
            'withdrawal_delay': 0,
            'dependency_score': 50,
            'bridge_risk': 10,
            'centralization_score': 50,
            'incident_response_time': 24,
            'community_activity': 50,
            'regulatory_scrutiny': 20
        }
        
        return pd.DataFrame([features])


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
