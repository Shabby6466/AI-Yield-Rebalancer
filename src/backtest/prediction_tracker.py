"""
Prediction Tracker & Validation Engine
Records AI predictions, then validates them against actual outcomes.
Builds an accuracy score over time to measure how good the "Brain" really is.

Flow:
1. AI says "REBALANCE to Pool X" or "HOLD Pool Y"
2. We record the prediction with timestamp
3. After N days, we check: Was the prediction correct?
4. Track accuracy metrics over time
"""

import sqlite3
import os
import logging
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'predictions.db')


@dataclass
class Prediction:
    """A single AI prediction record"""
    id: Optional[int]
    timestamp: str
    prediction_type: str     # "REBALANCE" or "HOLD"
    current_pool_id: str
    current_pool_apy: float
    target_pool_id: str
    target_pool_apy: float
    confidence: float
    reason: str
    capital_usd: float
    # Validation fields (filled later)
    validated: bool = False
    validation_date: Optional[str] = None
    actual_current_apy: Optional[float] = None  # What APY was N days later
    actual_target_apy: Optional[float] = None
    was_correct: Optional[bool] = None
    profit_if_followed: Optional[float] = None  # $ profit/loss if advice was followed
    # Market Context & Metrics for Deep Dive
    market_context: Optional[str] = None # JSON string of top pools considered
    gas_cost: Optional[float] = 0.0
    slippage: Optional[float] = 0.0
    volatility_score: Optional[float] = 0.0
    predicted_apy: Optional[float] = 0.0
    notes: Optional[str] = None


class PredictionTracker:
    """Records and validates AI predictions"""

    def __init__(self, db_path: str = None, validation_days: int = 1):
        """
        Args:
            db_path: Path to predictions database
            validation_days: Days to wait before validating a prediction
        """
        self.db_path = db_path or DB_PATH
        self.validation_days = validation_days
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    prediction_type TEXT NOT NULL,
                    current_pool_id TEXT NOT NULL,
                    current_pool_apy REAL,
                    target_pool_id TEXT,
                    target_pool_apy REAL,
                    confidence REAL,
                    reason TEXT,
                    capital_usd REAL,
                    validated INTEGER DEFAULT 0,
                    validation_date TEXT,
                    actual_current_apy REAL,
                    actual_target_apy REAL,
                    was_correct INTEGER,
                    profit_if_followed REAL,
                    market_context TEXT,
                    gas_cost REAL,
                    slippage REAL,
                    volatility_score REAL,
                    predicted_apy REAL,
                    notes TEXT
                )
            """)
            conn.commit()
        logger.info(f"PredictionTracker initialized at {self.db_path}")

    def record_prediction(self, 
                          prediction_type: str,
                          current_pool_id: str,
                          current_pool_apy: float,
                          target_pool_id: str,
                          target_pool_apy: float,
                          confidence: float,
                          reason: str,
                          capital_usd: float,
                          market_context: Optional[Dict] = None,
                          gas_cost: float = 0.0,
                          slippage: float = 0.0,
                          volatility_score: float = 0.0,
                          predicted_apy: float = 0.0) -> int:
        """
        Record a new AI prediction.
        
        Returns:
            Prediction ID
        """
        timestamp = datetime.utcnow().isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO predictions 
                (timestamp, prediction_type, current_pool_id, current_pool_apy,
                 target_pool_id, target_pool_apy, confidence, reason, capital_usd,
                 market_context, gas_cost, slippage, volatility_score, predicted_apy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp, prediction_type, current_pool_id, current_pool_apy,
                target_pool_id, target_pool_apy, confidence, reason, capital_usd,
                json.dumps(market_context) if market_context else None,
                gas_cost, slippage, volatility_score, predicted_apy
            ))
            conn.commit()
            pred_id = cursor.lastrowid
        
        logger.info(f"Recorded prediction #{pred_id}: {prediction_type} "
                     f"(Current: {current_pool_apy:.2f}% -> Target: {target_pool_apy:.2f}%)")
        return pred_id

    def validate_prediction(self, pred_id: int, 
                            actual_current_apy: float, 
                            actual_target_apy: float) -> Dict:
        """
        Validate a prediction against what actually happened.
        
        A prediction is "correct" if:
        - HOLD: Current pool APY stayed >= Target pool APY (staying was right)
        - REBALANCE: Target pool APY stayed > Current pool APY (moving was right)
        
        Returns:
            Validation result dict
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            pred = conn.execute(
                "SELECT * FROM predictions WHERE id = ?", (pred_id,)
            ).fetchone()
        
        if not pred:
            return {"error": f"Prediction #{pred_id} not found"}
        
        pred = dict(pred)
        prediction_type = pred['prediction_type']
        original_current = pred['current_pool_apy']
        original_target = pred['target_pool_apy']
        capital = pred['capital_usd']
        
        # Determination of Correctness (Cost-Adjusted)
        # We factor in gas and slippage from the original prediction
        total_costs = (pred.get('gas_cost') or 0.0) + (pred.get('slippage') or 0.0)
        days = self.validation_days
        
        # Calculate theoretical gains over the validation period
        gain_hold = capital * (actual_current_apy / 100) * (days / 365)
        gain_move = capital * (actual_target_apy / 100) * (days / 365)
        net_move_profit = gain_move - gain_hold - total_costs

        if prediction_type == "HOLD":
            # HOLD is correct if:
            # 1. Current APY is literally better
            # 2. OR Target is better but move is not profitable after costs
            # 3. OR the AI recognized it's already in the best possible pool (Avoiding churn)
            is_already_optimal = "[HOLD_OPTIMAL]" in (pred.get('reason') or "") or "[HOLD_ALREADY_OPTIMAL]" in (pred.get('reason') or "")
            was_correct = (actual_current_apy >= actual_target_apy) or (net_move_profit <= 0) or is_already_optimal
            profit_if_followed = gain_hold
            
        elif prediction_type == "REBALANCE":
            # REBALANCE is correct if moving actually resulted in more money than staying
            was_correct = net_move_profit > 0
            profit_if_followed = gain_move - total_costs
        else:
            was_correct = None
            profit_if_followed = 0
        
        # Update database
        validation_date = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE predictions SET
                    validated = 1,
                    validation_date = ?,
                    actual_current_apy = ?,
                    actual_target_apy = ?,
                    was_correct = ?,
                    profit_if_followed = ?,
                    notes = ?
                WHERE id = ?
            """, (
                validation_date,
                actual_current_apy,
                actual_target_apy,
                1 if was_correct else 0,
                round(profit_if_followed, 2),
                f"Validated after {days} days",
                pred_id
            ))
            conn.commit()
        
        result = {
            "prediction_id": pred_id,
            "type": prediction_type,
            "was_correct": was_correct,
            "original_current_apy": original_current,
            "original_target_apy": original_target,
            "actual_current_apy": actual_current_apy,
            "actual_target_apy": actual_target_apy,
            "profit_if_followed": round(profit_if_followed, 2),
            "validation_days": days
        }
        
        logger.info(f"Validated #{pred_id}: {'CORRECT' if was_correct else 'WRONG'} | "
                     f"Profit: ${profit_if_followed:.2f}")
        return result

    def get_pending_validations(self) -> List[Dict]:
        """Get predictions that are old enough to validate but haven't been yet"""
        cutoff = (datetime.utcnow() - timedelta(days=self.validation_days)).isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM predictions
                WHERE validated = 0 AND timestamp <= ?
                ORDER BY timestamp ASC
            """, (cutoff,)).fetchall()
        
        return [dict(r) for r in rows]

    def get_accuracy_stats(self) -> Dict:
        """Get overall prediction accuracy statistics"""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM predictions"
            ).fetchone()[0]
            
            validated = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE validated = 1"
            ).fetchone()[0]
            
            correct = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE validated = 1 AND was_correct = 1"
            ).fetchone()[0]
            
            pending = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE validated = 0"
            ).fetchone()[0]
            
            total_profit = conn.execute(
                "SELECT COALESCE(SUM(profit_if_followed), 0) FROM predictions WHERE validated = 1"
            ).fetchone()[0]
            
            # Sum of Gas + Slippage (Friction)
            total_friction = conn.execute(
                "SELECT COALESCE(SUM(gas_cost + slippage), 0) FROM predictions"
            ).fetchone()[0]
            
            # Fetch latest ROI from market_context
            latest_row = conn.execute(
                "SELECT market_context FROM predictions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            
            net_roi_pct = 0.0
            total_savings = 0.0
            if latest_row and latest_row[0]:
                try:
                    ctx = json.loads(latest_row[0])
                    metrics = ctx.get('metrics', {})
                    net_roi_pct = metrics.get('net_roi_pct', 0.0)
                    total_savings = metrics.get('wait_savings', 0.0)
                except:
                    pass

            # By type
            hold_correct = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE validated = 1 AND was_correct = 1 AND prediction_type = 'HOLD'"
            ).fetchone()[0]
            hold_total = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE validated = 1 AND prediction_type = 'HOLD'"
            ).fetchone()[0]
            
            rebalance_correct = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE validated = 1 AND was_correct = 1 AND prediction_type = 'REBALANCE'"
            ).fetchone()[0]
            rebalance_total = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE validated = 1 AND prediction_type = 'REBALANCE'"
            ).fetchone()[0]
        
        accuracy = (correct / validated * 100) if validated > 0 else 0
        hold_accuracy = (hold_correct / hold_total * 100) if hold_total > 0 else 0
        rebalance_accuracy = (rebalance_correct / rebalance_total * 100) if rebalance_total > 0 else 0
        
        return {
            "total_predictions": total,
            "validated": validated,
            "pending": pending,
            "correct": correct,
            "accuracy_pct": round(accuracy, 1),
            "hold_accuracy_pct": round(hold_accuracy, 1),
            "rebalance_accuracy_pct": round(rebalance_accuracy, 1),
            "total_profit_if_followed": round(total_profit, 2),
            "total_friction": round(total_friction, 2),
            "net_roi_pct": round(net_roi_pct, 2),
            "total_savings": round(total_savings, 2)
        }

    def get_all_predictions(self, limit: int = 50) -> List[Dict]:
        """Get all predictions, most recent first"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM predictions
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()
        
        return [dict(r) for r in rows]

    def auto_validate_from_db(self) -> List[Dict]:
        """
        Automatically validate pending predictions using data from TimeseriesDB.
        This is the core of the "Prediction → Backtest" loop.
        """
        from src.data.timeseries_db import TimeseriesDB
        tsdb = TimeseriesDB()
        
        pending = self.get_pending_validations()
        results = []
        
        for pred in pending:
            # Get current APY for both pools from latest data
            current_history = tsdb.get_pool_history(pred['current_pool_id'], days=1)
            target_history = tsdb.get_pool_history(pred['target_pool_id'], days=1)
            
            if current_history and target_history:
                actual_current = current_history[-1]['apy']
                actual_target = target_history[-1]['apy']
                
                result = self.validate_prediction(
                    pred['id'], actual_current, actual_target
                )
                results.append(result)
            else:
                logger.warning(f"Cannot validate #{pred['id']}: Missing pool data")
        
        if results:
            correct = sum(1 for r in results if r.get('was_correct'))
            logger.info(f"Auto-validated {len(results)} predictions: "
                        f"{correct}/{len(results)} correct")
        
        return results


if __name__ == "__main__":
    # Demo
    tracker = PredictionTracker(validation_days=7)
    
    # Record a prediction
    pred_id = tracker.record_prediction(
        prediction_type="HOLD",
        current_pool_id="aave-usdc-eth",
        current_pool_apy=8.5,
        target_pool_id="compound-usdc-base",
        target_pool_apy=12.0,
        confidence=0.25,
        reason="Mean Reversion Risk: APY spike likely temporary",
        capital_usd=100_000
    )
    
    # Simulate validation (in production, this happens days later)
    result = tracker.validate_prediction(
        pred_id,
        actual_current_apy=9.2,   # Current pool went up slightly
        actual_target_apy=6.0     # Target pool crashed (spike was indeed temporary!)
    )
    
    print(f"Prediction was: {'CORRECT' if result['was_correct'] else 'WRONG'}")
    print(f"Profit if followed: ${result['profit_if_followed']:.2f}")
    
    # Get stats
    stats = tracker.get_accuracy_stats()
    print(f"\nOverall Accuracy: {stats['accuracy_pct']}%")
    print(f"Total Predictions: {stats['total_predictions']}")
