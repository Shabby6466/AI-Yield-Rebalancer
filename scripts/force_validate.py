
import asyncio
import logging
import json
from src.backtest.prediction_tracker import PredictionTracker
from src.data.defillama_client import DefiLlamaClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ForceValidate")

async def force_validate():
    tracker = PredictionTracker(validation_days=0) # Set to 0 to catch all
    llama = DefiLlamaClient()
    
    # Get all pending predictions
    all_preds = tracker.get_all_predictions(limit=100)
    pending = [p for p in all_preds if not p['validated']]
    
    if not pending:
        logger.info("No pending predictions to validate.")
        return

    logger.info(f"🔍 Force-Validating {len(pending)} predictions against LIVE market data...")
    
    for pred in pending:
        try:
            # Skip SUCCESS/ABORTED records for accuracy metrics as they aren't 'forecasts' in the same way
            if pred['prediction_type'] in ["SUCCESS", "ABORTED"]:
                # We still mark them validated so they don't stay pending
                tracker.validate_prediction(pred['id'], pred['current_pool_apy'], pred['target_pool_apy'])
                continue

            # Fetch live data for both pools involved in the decision
            pool_ids = []
            if pred['current_pool_id'] and pred['current_pool_id'] not in ["none", "CASH"]:
                pool_ids.append(pred['current_pool_id'])
            if pred['target_pool_id'] and pred['target_pool_id'] not in ["none", "CASH"]:
                pool_ids.append(pred['target_pool_id'])
            
            live_data = await llama.fetch_pool_yields(pool_ids) if pool_ids else []
            
            # Find the matches
            current_match = next((p for p in live_data if p['pool'] == pred['current_pool_id']), None)
            target_match = next((p for p in live_data if p['pool'] == pred['target_pool_id']), None)
            
            # Resolve actual APYs
            actual_current = current_match['apy'] if current_match else pred['current_pool_apy']
            actual_target = target_match['apy'] if target_match else pred['target_pool_apy']
            
            # Validate
            tracker.validate_prediction(pred['id'], actual_current, actual_target)
            logger.info(f"✅ Validated #{pred['id']}: {pred['prediction_type']} (Live Current: {actual_current:.2f}% | Target: {actual_target:.2f}%)")
            
        except Exception as e:
            logger.error(f"❌ Failed to validate #{pred['id']}: {e}")

if __name__ == "__main__":
    asyncio.run(force_validate())
