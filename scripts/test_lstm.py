"""
Test LSTM Yield Predictor
Makes predictions on live protocol data
"""

import os
import sys
import logging
import pickle
import numpy as np
import torch
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.feature_engineering import FeatureEngineer
from src.ml.lstm_predictor import YieldPredictorLSTM

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def test_predictions():
    """Test yield predictions on live data"""
    load_dotenv()
    
    # Load feature scaler
    logger.info("Loading feature scaler...")
    with open('models/feature_scaler.pkl', 'rb') as f:
        scaler = pickle.load(f)
    
    # Load trained model
    logger.info("Loading trained LSTM model...")
    model = YieldPredictorLSTM.load_from_checkpoint(
        'models/lstm_predictor_final.ckpt'
    )
    model.eval()
    
    # Connect to database
    logger.info("\nConnecting to database...")
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME", "defi_yield_db"),
        user=os.getenv("DB_USER", "faizan"),
        password=os.getenv("DB_PASSWORD", ""),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432")
    )
    cursor = conn.cursor()
    
    # Get latest protocol data
    logger.info("\nFetching latest protocol data...")
    cursor.execute("""
        SELECT protocol_id, protocol_name, protocol_type
        FROM protocols
        WHERE is_active = true
        ORDER BY protocol_name
    """)
    protocols = cursor.fetchall()
    
    logger.info(f"\n{'='*80}")
    logger.info(f"LSTM YIELD PREDICTIONS - {datetime.now()}")
    logger.info(f"{'='*80}\n")
    
    # Mock database wrapper for FeatureEngineer
    class DBWrapper:
        def __init__(self, conn):
            self.conn = conn
        
        async def fetch_one(self, query, *args):
            cursor = self.conn.cursor()
            cursor.execute(query, args)
            row = cursor.fetchone()
            if row:
                columns = [desc[0] for desc in cursor.description]
                return dict(zip(columns, row))
            return None
        
        async def fetch_all(self, query, *args):
            cursor = self.conn.cursor()
            cursor.execute(query, args)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]
    
    db_wrapper = DBWrapper(conn)
    feature_engineer = FeatureEngineer(db_wrapper)
    predictions_made = 0
    
    for protocol in protocols:
        protocol_id, protocol_name, protocol_type = protocol
        
        try:
            # Get latest yield record
            cursor.execute("""
                SELECT * FROM protocol_yields
                WHERE protocol_id = %s
                ORDER BY timestamp DESC
                LIMIT 1
            """, (protocol_id,))
            
            yield_record = cursor.fetchone()
            if not yield_record:
                continue
            
            # Convert to dict
            columns = [desc[0] for desc in cursor.description]
            yield_dict = dict(zip(columns, yield_record))
            
            # Extract protocol features
            logger.info(f"\n {protocol_name} ({yield_dict.get('market_name', '')})")
            logger.info(f"   Current APY: {yield_dict['current_apy']:.3f}%")
            
            # Create features
            features = await feature_engineer.create_feature_vector(
                protocol_id=protocol_id,
                timestamp=yield_dict['timestamp']
            )
            
            if features is None:
                logger.warning("   ⚠️  Could not generate features")
                continue
            
            # Normalize features
            feature_vector = features.to_vector().reshape(1, -1)
            feature_norm = scaler.transform(feature_vector)
            
            # Create sequence (repeat for sequence length)
            # In production, would use historical sequence
            sequence_length = 3  # Same as training
            feature_sequence = np.repeat(feature_norm, sequence_length, axis=0)
            feature_tensor = torch.FloatTensor(feature_sequence).unsqueeze(0)
            
            # Predict
            with torch.no_grad():
                predicted_apy, attention_weights = model(feature_tensor)
                pred = predicted_apy.item()
            
            # Display prediction
            diff = pred - yield_dict['current_apy']
            direction = "" if diff > 0 else ""
            
            logger.info(f"   🎯 Predicted APY (7d): {pred:.3f}%")
            logger.info(f"   {direction} Expected change: {diff:+.3f}%")
            logger.info(f"    Feature quality: {features.risk_score:.1f}/100")
            
            predictions_made += 1
            
        except Exception as e:
            logger.error(f"   ❌ Prediction failed: {e}")
    
    cursor.close()
    conn.close()
    
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ Made {predictions_made} predictions")
    logger.info(f"{'='*80}\n")
    
    logger.info("💡 Note: Predictions use single-point sequences")
    logger.info("   For better accuracy, collect more historical data")
    logger.info("   Run: python scripts/scheduler.py")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_predictions())
