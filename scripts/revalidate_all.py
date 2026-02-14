
import logging
import sqlite3
import os
from src.backtest.prediction_tracker import PredictionTracker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RevalidateAll")

def revalidate_all():
    db_path = os.path.join(os.getcwd(), 'data', 'predictions.db')
    tracker = PredictionTracker()
    
    # Force reset all to unvalidated
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE predictions SET validated = 0")
        conn.commit()
        
        conn.row_factory = sqlite3.Row
        all_preds = conn.execute("SELECT * FROM predictions ORDER BY id ASC").fetchall()
        
    logger.info(f"Re-validating all {len(all_preds)} records with Portfolio-Aware logic...")
    
    for row in all_preds:
        # Resolve actuals. For this mass re-validation, we use the values that were recorded at the time.
        # This effectively checks if the 'Decision' made sense given the costs known at that moment.
        tracker.validate_prediction(row['id'], row['current_pool_apy'], row['target_pool_apy'])

if __name__ == "__main__":
    revalidate_all()
