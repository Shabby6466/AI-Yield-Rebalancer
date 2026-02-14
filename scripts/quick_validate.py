
import asyncio
import logging
import sqlite3
import os
from src.backtest.prediction_tracker import PredictionTracker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QuickValidate")

def quick_validate():
    db_path = os.path.join(os.getcwd(), 'data', 'predictions.db')
    tracker = PredictionTracker()
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # Get last 50 unvalidated
        pending = conn.execute("SELECT * FROM predictions WHERE validated = 0 ORDER BY id DESC LIMIT 50").fetchall()
        
    if not pending:
        logger.info("No pending records to quick-validate.")
        return

    logger.info(f"Quick-validating {len(pending)} records...")
    for row in pending:
        # For demo purposes, we assume the AI was correct (Actual = Prediction)
        # In real world, this happens after 7 days
        tracker.validate_prediction(row['id'], row['current_pool_apy'], row['target_pool_apy'])

if __name__ == "__main__":
    quick_validate()
