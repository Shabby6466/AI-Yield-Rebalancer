
import logging
import os
import sys
import torch
import numpy as np
from torch.utils.data import DataLoader

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ml.feature_pipeline import FeaturePipeline
from src.ml.lstm_model import YieldPredictor, YieldDataset

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def train_lstm():
    # 1. Initialize Pipeline
    pipeline = FeaturePipeline()
    
    # 2. Load Data
    logger.info("Loading data from database...")
    df = pipeline.load_data()
    
    if df.empty:
        logger.error("No data found in yield_snapshots. Run the data collector first.")
        return
        
    # 3. Create Features
    logger.info("Generating features...")
    df_features = pipeline.create_features(df)
    
    # 4. Prepare Sequences
    logger.info("Preparing sequences...")
    # Using default input_dim from pipeline (usually around 20-30 features)
    X, y, metadata = pipeline.prepare_sequences(
        df_features, 
        sequence_length=30, 
        prediction_horizon=7
    )
    
    if len(X) == 0:
        logger.error("Not enough data to create sequences. Need at least 37 days of data.")
        return

    # 5. Split Data
    data = pipeline.split_data(X, y, metadata)
    
    # 6. Create DataLoaders
    train_dataset = YieldDataset(data['X_train'], data['y_train'])
    val_dataset = YieldDataset(data['X_val'], data['y_val'])
    
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    
    # 7. Initialize Model
    input_dim = X.shape[-1]
    logger.info(f"Initializing LSTM with input_dim={input_dim}")
    
    predictor = YieldPredictor(input_dim=input_dim)
    
    # 8. Train
    os.makedirs('models', exist_ok=True)
    history = predictor.train(train_loader, val_loader, epochs=50, patience=10)
    
    # 9. Save
    predictor.save('models/lstm_yield_predictor.pth')
    logger.info("LSTM Model training complete and saved.")

if __name__ == "__main__":
    try:
        train_lstm()
    except Exception as e:
        logger.error(f"Training failed: {e}")
        import traceback
        traceback.print_exc()
