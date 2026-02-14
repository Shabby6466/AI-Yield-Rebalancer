"""
Improved LSTM Training Script
Leverages 300+ collected samples to build robust time-series sequences
"""

import os
import sys
import logging
import pickle
import psycopg2
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split

import torch
import pytorch_lightning as pl
from pytorch_lightning.callbacks import ModelCheckpoint, EarlyStopping
from pytorch_lightning.loggers import TensorBoardLogger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ml.lstm_predictor import YieldPredictorLSTM, create_dataloaders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def prepare_time_series_data(db_url: str, sequence_length: int = 7):
    """
    Prepare time-series training data by creating windows from historical yields
    
    Args:
        db_url: Database connection string
        sequence_length: Number of timesteps per sequence
        
    Returns:
        features, targets, scaler
    """
    logger.info(f"\n{'='*80}")
    logger.info(f"PREPARING TIME-SERIES DATA")
    logger.info(f"{'='*80}\n")
    
    logger.info(f"Sequence length: {sequence_length} steps")
    
    # Connect and fetch yield history
    db_conn = psycopg2.connect(db_url)
    cursor = db_conn.cursor()
    
    # Get all yield records ordered by asset and timestamp
    cursor.execute("""
        SELECT asset, apy_percent, recorded_at, 
               tvl_usd, utilization_ratio, 
               COALESCE(variable_borrow_rate, 50) as risk
        FROM protocol_yields
        ORDER BY asset, recorded_at
    """)
    
    records = cursor.fetchall()
    db_conn.close()
    
    logger.info(f"Total records fetched: {len(records)}")
    
    if len(records) < sequence_length + 1:
        raise ValueError(
            f"Need at least {sequence_length + 1} records, have {len(records)}"
        )
    
    # Group by asset to create time-series windows
    from collections import defaultdict
    asset_data = defaultdict(list)
    
    for asset, apy, timestamp, tvl, util, risk in records:
        asset_data[asset].append({
            'apy': apy,
            'tvl': tvl or 0,
            'util': util or 0,
            'risk': risk or 50,
            'timestamp': timestamp
        })
    
    logger.info(f"Assets with history: {len(asset_data)}")
    
    # Create sequences
    X = []  # Input sequences
    y = []  # Target APYs (Average over horizon)
    prediction_horizon = 7
    
    for asset, history in asset_data.items():
        if len(history) >= sequence_length + prediction_horizon:
            # Sort by timestamp
            history = sorted(history, key=lambda x: x['timestamp'])
            
            # Create sliding windows
            for i in range(len(history) - sequence_length - prediction_horizon + 1):
                # Input: past sequence_length points
                base_seq = np.array([
                    [h['apy'], h['tvl'], h['util'], h['risk']]
                    for h in history[i:i+sequence_length]
                ])
                
                # Expand 4 base features -> 32 technical features
                from src.execution.ml_prediction_service import MLPredictionService
                # We can't easily instantiate it here without network, so we use a dummy expansion or local copy
                # Let's implement the expansion locally for the script to avoid cyclic imports or heavy initialization
                expanded_seq = np.zeros((sequence_length, 32))
                expanded_seq[:, 0:4] = base_seq
                
                # Moving Averages & Trends
                for j in range(2, sequence_length):
                    expanded_seq[j, 4:8] = np.mean(base_seq[j-2:j+1, 0:4], axis=0) # 3-step MA
                for j in range(6, sequence_length):
                    expanded_seq[j, 8:12] = np.mean(base_seq[j-6:j+1, 0:4], axis=0) # 7-step MA
                for j in range(1, sequence_length):
                    expanded_seq[j, 16:20] = base_seq[j, 0:4] - base_seq[j-1, 0:4] # Momentum
                
                # Target: average of next few steps (prediction_horizon)
                target_apy = np.mean([
                    h['apy'] for h in history[i + sequence_length : i + sequence_length + prediction_horizon]
                ])
                
                X.append(expanded_seq)
                y.append(target_apy)
    
    X = np.array(X).astype(np.float32)
    y = np.array(y).astype(np.float32)
    
    logger.info(f"Created {len(X)} sequences")
    logger.info(f"Sequence shape: {X.shape}")
    logger.info(f"Target shape: {y.shape}")
    logger.info(f"Target range: {y.min():.3f}% to {y.max():.3f}%")
    
    # Normalize features (32 features per timestep)
    X_flat = X.reshape(-1, 32)  # Flatten all sequences for scaling
    scaler = StandardScaler()
    X_flat_norm = scaler.fit_transform(X_flat)
    X_norm = X_flat_norm.reshape(X.shape)
    
    # Save scaler
    os.makedirs('models', exist_ok=True)
    with open('models/feature_scaler.pkl', 'wb') as f:
        pickle.dump(scaler, f)
    logger.info("✅ Saved feature scaler to models/feature_scaler.pkl")
    
    return X_norm, y, scaler


def train_model(
    X: np.ndarray,
    y: np.ndarray,
    epochs: int = 100,
    batch_size: int = 32,
    learning_rate: float = 0.001
):
    """Train LSTM model on time-series data"""
    
    logger.info(f"\n{'='*80}")
    logger.info("TRAINING LSTM YIELD PREDICTOR")
    logger.info(f"{'='*80}\n")
    
    # Split data
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    
    logger.info(f"Training samples: {len(X_train)}")
    logger.info(f"Validation samples: {len(X_val)}")
    
    # Create datasets
    from torch.utils.data import TensorDataset, DataLoader
    
    X_train_t = torch.FloatTensor(X_train)
    y_train_t = torch.FloatTensor(y_train)
    X_val_t = torch.FloatTensor(X_val)
    y_val_t = torch.FloatTensor(y_val)
    
    train_dataset = TensorDataset(X_train_t, y_train_t)
    val_dataset = TensorDataset(X_val_t, y_val_t)
    
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size)
    
    logger.info(f"Train batches: {len(train_loader)}")
    logger.info(f"Val batches: {len(val_loader)}")
    
    # Create model
    input_size = X.shape[2]  # 4 features
    model = YieldPredictorLSTM(
        input_size=input_size,
        hidden_size=128,
        num_layers=2,
        dropout=0.3,
        learning_rate=learning_rate
    )
    
    logger.info(f"\nModel architecture:")
    logger.info(f"  Input size: {input_size} features")
    logger.info(f"  Sequence length: {X.shape[1]} steps")
    logger.info(f"  Hidden size: 128")
    logger.info(f"  LSTM layers: 2")
    logger.info(f"  Dropout: 0.3")
    
    # Callbacks
    checkpoint_callback = ModelCheckpoint(
        dirpath='models/checkpoints',
        filename='lstm-{epoch:02d}-{val_loss:.4f}',
        save_top_k=3,
        monitor='val_loss',
        mode='min'
    )
    
    early_stopping = EarlyStopping(
        monitor='val_loss',
        patience=15,
        mode='min'
    )
    
    # Logger
    tb_logger = TensorBoardLogger('models/logs', name='lstm_predictor')
    
    # Trainer
    trainer = pl.Trainer(
        max_epochs=epochs,
        callbacks=[checkpoint_callback, early_stopping],
        logger=tb_logger,
        accelerator='auto',
        devices=1,
        log_every_n_steps=5,
        deterministic=True
    )
    
    logger.info(f"\n{'='*80}")
    logger.info("Starting training...")
    logger.info(f"{'='*80}\n")
    
    trainer.fit(model, train_loader, val_loader)
    
    # Save final model
    trainer.save_checkpoint('models/lstm_predictor_final.ckpt')
    
    logger.info(f"\n{'='*80}")
    logger.info("TRAINING COMPLETE")
    logger.info(f"{'='*80}")
    logger.info(f"✅ Model saved to: models/lstm_predictor_final.ckpt")
    logger.info(f"✅ Best checkpoint: {checkpoint_callback.best_model_path}")
    logger.info(f"✅ TensorBoard logs: models/logs/\n")
    
    # Test
    trainer.test(model, val_loader)
    
    logger.info("\n📝 Next steps:")
    logger.info("   1. View training progress: tensorboard --logdir models/logs/")
    logger.info("   2. Test predictions: python scripts/test_lstm.py")
    logger.info("   3. Deploy for inference: python scripts/inference.py")
    
    return model, trainer


def main():
    """Main training entry point"""
    load_dotenv()
    
    try:
        # Prepare data
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            # Build from individual env vars
            db_url = f"dbname={os.getenv('DB_NAME', 'defi_yield_db')} " \
                     f"user={os.getenv('DB_USER', 'faizan')} " \
                     f"password={os.getenv('DB_PASSWORD', '')} " \
                     f"host={os.getenv('DB_HOST', 'localhost')} " \
                     f"port={os.getenv('DB_PORT', '5432')}"
        
        X, y, scaler = prepare_time_series_data(db_url, sequence_length=7)
        
        # Train model
        model, trainer = train_model(
            X, y,
            epochs=100,
            batch_size=32,
            learning_rate=0.001
        )
        
        logger.info("\n✅ Training pipeline complete!")
        
    except Exception as e:
        logger.error(f"❌ Training failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
