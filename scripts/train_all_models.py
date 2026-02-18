
import sys
import os
import subprocess
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def train_all():
    logger.info("Starting model training pipeline...")
    
    # Ensure models directory exists
    os.makedirs('models', exist_ok=True)
    
    # 1. Train Risk Scorer
    logger.info("\n=== Training Risk Scorer (XGBoost) ===")
    try:
        subprocess.run([sys.executable, "src/ml/risk_scorer.py"], check=True)
        logger.info("✓ Risk Scorer trained successfully.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to train Risk Scorer: {e}")
        return

    # 2. Train Yield Predictor (LSTM)
    logger.info("\n=== Training Yield Predictor (LSTM) ===")
    try:
        subprocess.run([sys.executable, "scripts/train_lstm_model.py"], check=True)
        logger.info("✓ LSTM Predictor trained successfully.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to train LSTM Predictor: {e}")
        return

    logger.info("\n🎉 All models trained and saved to models/")

if __name__ == "__main__":
    train_all()
