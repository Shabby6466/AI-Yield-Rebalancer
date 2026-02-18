import json
import os
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

class StateStore:
    """Handles persistence of the rebalancer's portfolio state with absolute pathing."""
    
    def __init__(self, file_name: str = "system_state.json"):
        # Portable pathing that works in Docker and Local
        prj_root = Path(__file__).parent.parent.parent
        data_dir = prj_root / "data"
        os.makedirs(data_dir, exist_ok=True)
        self.file_path = data_dir / file_name
        
    def update_state(self, **kwargs):
        """Flexible update for any field in the state."""
        try:
            state = self.load_state()
            state.update(kwargs)
            state["last_updated"] = datetime.utcnow().isoformat()
            with open(self.file_path, 'w') as f:
                json.dump(state, f)
            logger.info(f"State updated: {kwargs}")
        except Exception as e:
            logger.error(f"Failed to update state: {e}")

    def save_state(self, pool_id: str, symbol: str, apy: float, initial_capital: float = 0.0, cumulative_costs: float = 0.0):
        """Legacy wrapper for update_state."""
        self.update_state(
            current_pool_id=pool_id,
            current_pool_symbol=symbol,
            current_apy=apy,
            initial_capital=initial_capital,
            cumulative_costs=cumulative_costs
        )

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
            "current_apy": 0.0,
            "initial_capital": 0.0,
            "cumulative_costs": 0.0,
            "total_yield_earned": 0.0,
            "net_roi_pct": 0.0,
            "last_harvest_time": None,
            "last_harvest_yield": 0.0,
            "current_total_value": 0.0,
        }
