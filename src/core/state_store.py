import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class StateStore:
    """Handles persistence of the rebalancer's portfolio state with absolute pathing."""
    
    def __init__(self, file_name: str = "system_state.json"):
        # Portable pathing that works in Docker and Local
        prj_root = Path(__file__).parent.parent.parent
        data_dir = prj_root / "data"
        os.makedirs(data_dir, exist_ok=True)
        self.file_path = data_dir / file_name
        
    def save_state(self, pool_id: str, symbol: str, apy: float):
        try:
            state = {
                "current_pool_id": pool_id,
                "current_pool_symbol": symbol,
                "current_apy": apy,
                "last_updated": os.times()[4]
            }
            with open(self.file_path, 'w') as f:
                json.dump(state, f)
            logger.info(f"State saved to {self.file_path}: {symbol} at {apy:.2f}%")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def load_state(self) -> dict:
        try:
            if self.file_path.exists():
                with open(self.file_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            
        return {
            "current_pool_id": None,
            "current_pool_symbol": "CASH",
            "current_apy": 0.0
        }
