"""
Prediction Tracker & Validation Engine (PostgreSQL/TimescaleDB)
Records AI predictions, then validates them against actual outcomes.
Builds an accuracy score over time to measure how good the "Brain" really is.

Flow:
1. AI says "REBALANCE to Pool X" or "HOLD Pool Y"
2. We record the prediction with timestamp
3. After N days, we check: Was the prediction correct?
4. Track accuracy metrics over time

Uses shared connection pooling to avoid connection leaks in FastAPI.
"""

import logging
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
import psycopg2
from psycopg2.extras import Json, RealDictCursor

logger = logging.getLogger(__name__)


class PredictionTracker:
    """Records and validates AI predictions using PostgreSQL/TimescaleDB"""

    def __init__(self, conn_pool, validation_days: int = 1):
        """
        Args:
            conn_pool: Shared psycopg2 Pool object (from FastAPI startup)
            validation_days: Duration to wait before verifying a prediction
        """
        self.pool = conn_pool
        self.validation_days = validation_days
        self._ensure_table()
        logger.info(f"PredictionTracker initialized with {validation_days} day validation window")

    def _ensure_table(self):
        """Initializes the table in Postgres/TimescaleDB"""
        query = """
        CREATE TABLE IF NOT EXISTS ai_predictions (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            prediction_type VARCHAR(20) NOT NULL,
            current_pool_id VARCHAR(100),
            current_pool_apy DOUBLE PRECISION,
            target_pool_id VARCHAR(100),
            target_pool_apy DOUBLE PRECISION,
            confidence DOUBLE PRECISION,
            reason TEXT,
            capital_usd DOUBLE PRECISION,
            validated BOOLEAN DEFAULT FALSE,
            validation_date TIMESTAMPTZ,
            actual_current_apy DOUBLE PRECISION,
            actual_target_apy DOUBLE PRECISION,
            was_correct BOOLEAN,
            profit_if_followed DOUBLE PRECISION,
            market_context JSONB,
            gas_cost DOUBLE PRECISION DEFAULT 0,
            slippage DOUBLE PRECISION DEFAULT 0,
            predicted_apy DOUBLE PRECISION DEFAULT 0,
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pred_valid ON ai_predictions(validated);
        CREATE INDEX IF NOT EXISTS idx_pred_time ON ai_predictions(timestamp);
        """
        conn = self.pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(query)
                conn.commit()
                logger.info("ai_predictions table initialized")
        except psycopg2.Error as e:
            logger.error(f"Failed to create table: {e}")
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    def record_prediction(self, data: Dict) -> int:
        """
        Saves a prediction from the Inference Engine
        
        Args:
            data: Dictionary with keys:
                - prediction_type: "REBALANCE" or "HOLD"
                - current_pool_id: str
                - current_pool_apy: float
                - target_pool_id: str (optional for HOLD)
                - target_pool_apy: float (optional for HOLD)
                - confidence: float (0-1)
                - reason: str
                - capital_usd: float
                - market_context: dict (optional)
                - gas_cost: float (optional)
                - slippage: float (optional)
                - predicted_apy: float (optional)
                - notes: str (optional)
        
        Returns:
            int: Prediction ID
        """
        query = """
        INSERT INTO ai_predictions (
            prediction_type, current_pool_id, current_pool_apy,
            target_pool_id, target_pool_apy, confidence, reason, capital_usd,
            market_context, gas_cost, slippage, predicted_apy, notes
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """
        conn = self.pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(query, (
                    data['prediction_type'],
                    data['current_pool_id'],
                    data['current_pool_apy'],
                    data.get('target_pool_id'),
                    data.get('target_pool_apy'),
                    data['confidence'],
                    data['reason'],
                    data['capital_usd'],
                    Json(data.get('market_context')) if data.get('market_context') else None,
                    data.get('gas_cost', 0),
                    data.get('slippage', 0),
                    data.get('predicted_apy', 0),
                    data.get('notes')
                ))
                pred_id = cur.fetchone()[0]
                conn.commit()
                logger.info(f"Recorded prediction #{pred_id}: {data['prediction_type']} "
                           f"(Current: {data['current_pool_apy']:.2f}% -> Target: {data.get('target_pool_apy', 0):.2f}%)")
                return pred_id
        except psycopg2.Error as e:
            logger.error(f"Failed to record prediction: {e}")
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def validate_prediction(self, pred_id: int, 
                            actual_current_apy: float, 
                            actual_target_apy: float) -> Dict:
        """
        Validate a prediction against what actually happened.
        
        Financial Logic:
        - HOLD: Correct if current APY >= target APY, OR moving costs > potential gains
        - REBALANCE: Correct if (target_apy - current_apy) * capital / 365 * days > costs
        
        Returns:
            Validation result dict
        """
        conn = self.pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM ai_predictions WHERE id = %s", (pred_id,))
                pred = cur.fetchone()
                
                if not pred:
                    return {"status": "not_found", "id": pred_id}

                prediction_type = pred['prediction_type']
                capital = pred['capital_usd']
                
                # Financial Validation Logic with cost adjustments
                total_friction = (pred['gas_cost'] or 0) + (pred['slippage'] or 0)
                days = self.validation_days
                
                # Pro-rated gain calculation (APY is annualized, scale to validation period)
                gain_hold = capital * (actual_current_apy / 100) * (days / 365) if actual_current_apy else 0
                gain_move = capital * (actual_target_apy / 100) * (days / 365) if actual_target_apy else 0
                net_move_profit = gain_move - gain_hold - total_friction

                if prediction_type == "HOLD":
                    # HOLD is correct if:
                    # 1. Current APY was better than target, OR
                    # 2. Moving would not be profitable after costs
                    is_already_optimal = "[HOLD_OPTIMAL]" in (pred.get('reason') or "")
                    was_correct = (actual_current_apy >= actual_target_apy) or (net_move_profit <= 0) or is_already_optimal
                    profit = gain_hold
                    
                    logger.info(f"🔍 Validate #{pred_id} [HOLD]: Current {actual_current_apy:.2f}% vs Target {actual_target_apy:.2f}%")
                    logger.info(f"   Net Move Profit: ${net_move_profit:.2f}. Correct? {was_correct}")
                    
                elif prediction_type == "REBALANCE":
                    # REBALANCE is correct if moving actually resulted in more money
                    was_correct = net_move_profit > 0
                    profit = gain_move - total_friction
                    
                    logger.info(f"🔍 Validate #{pred_id} [REBALANCE]: Target {actual_target_apy:.2f}% (Gain ${gain_move:.2f}) - Cost ${total_friction:.2f}")
                    logger.info(f"   Net Profit: ${net_move_profit:.2f}. Correct? {was_correct}")
                else:
                    was_correct = None
                    profit = 0

                # Update with validation results
                cur.execute("""
                    UPDATE ai_predictions SET
                        validated = TRUE,
                        validation_date = NOW(),
                        actual_current_apy = %s,
                        actual_target_apy = %s,
                        was_correct = %s,
                        profit_if_followed = %s
                    WHERE id = %s
                """, (actual_current_apy, actual_target_apy, was_correct, profit, pred_id))
                conn.commit()
                
                logger.info(f"Validated prediction #{pred_id}: {prediction_type} -> "
                           f"Correct={was_correct}, Profit=${profit:.2f}")
                
                return {
                    "id": pred_id,
                    "prediction_type": prediction_type,
                    "correct": was_correct,
                    "profit": profit,
                    "validation_date": datetime.now(timezone.utc).isoformat()
                }
        except psycopg2.Error as e:
            logger.error(f"Failed to validate prediction {pred_id}: {e}")
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def auto_validate_from_db(self) -> Dict:
        """
        Closed-loop validation:
        1. Finds predictions > validation_days old that aren't validated.
        2. Fetches actual yield data from the yield_logs table.
        3. Grades the AI automatically.
        
        Returns:
            Summary dict with validation counts
        """
        conn = self.pool.getconn()
        validated_count = 0
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Get pending predictions that are ready for validation
                query_pending = """
                    SELECT id, current_pool_id, target_pool_id 
                    FROM ai_predictions 
                    WHERE validated = FALSE 
                    AND timestamp <= NOW() - INTERVAL '%s days'
                    ORDER BY timestamp ASC;
                """
                
                cur.execute(query_pending, (self.validation_days,))
                pending = cur.fetchall()
                
                if not pending:
                    logger.info("No predictions pending validation")
                    return {"validated": 0, "pending": 0, "status": "no_pending"}
                
                logger.info(f"Found {len(pending)} predictions ready for validation")
                
                # 2. Fetch latest actuals from yield_logs for each prediction
                for pred in pending:
                    try:
                        query_actuals = """
                            SELECT DISTINCT ON (pool_id) pool_id, apy_total as apy 
                            FROM yield_snapshots 
                            WHERE pool_id IN (%s, %s)
                            ORDER BY pool_id, time DESC;
                        """
                        
                        cur.execute(query_actuals, (pred['current_pool_id'], pred['target_pool_id']))
                        actual_data = {row['pool_id']: row['apy'] for row in cur.fetchall()}
                        
                        # Only validate if we have data for both pools
                        if pred['current_pool_id'] in actual_data and pred['target_pool_id'] in actual_data:
                            self.validate_prediction(
                                pred['id'],
                                actual_data[pred['current_pool_id']],
                                actual_data[pred['target_pool_id']]
                            )
                            validated_count += 1
                        else:
                            logger.warning(f"Missing yield data for prediction {pred['id']}")
                    except Exception as e:
                        logger.error(f"Failed to validate prediction {pred['id']}: {e}")
                        continue
                
                conn.commit()
                logger.info(f"Auto-validation complete: {validated_count}/{len(pending)} validated")
                return {"validated": validated_count, "pending": len(pending) - validated_count, "status": "success"}
                
        except psycopg2.Error as e:
            logger.error(f"Auto-validation failed: {e}")
            conn.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            self.pool.putconn(conn)

    def get_pending_validations(self) -> List[Dict]:
        """Get predictions that are old enough to validate but haven't been yet"""
        conn = self.pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                    SELECT * FROM ai_predictions
                    WHERE validated = FALSE 
                    AND timestamp <= NOW() - INTERVAL '%s days'
                    ORDER BY timestamp ASC;
                """
                cur.execute(query, (self.validation_days,))
                return cur.fetchall()
        except psycopg2.Error as e:
            logger.error(f"Failed to fetch pending validations: {e}")
            return []
        finally:
            self.pool.putconn(conn)

    def get_accuracy_stats(self) -> Dict:
        """Get overall prediction accuracy statistics"""
        conn = self.pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Total predictions
                cur.execute("SELECT COUNT(*) as total FROM ai_predictions")
                total = cur.fetchone()['total']
                
                # Validated predictions
                cur.execute("SELECT COUNT(*) as validated FROM ai_predictions WHERE validated = TRUE")
                validated = cur.fetchone()['validated']
                
                # Correct predictions
                cur.execute("SELECT COUNT(*) as correct FROM ai_predictions WHERE validated = TRUE AND was_correct = TRUE")
                correct = cur.fetchone()['correct']
                
                # Pending validations
                cur.execute("SELECT COUNT(*) as pending FROM ai_predictions WHERE validated = FALSE")
                pending = cur.fetchone()['pending']
                
                # Total profit
                cur.execute("SELECT COALESCE(SUM(profit_if_followed), 0) as total_profit FROM ai_predictions WHERE validated = TRUE")
                total_profit = cur.fetchone()['total_profit']
                
                # Total friction (gas + slippage)
                cur.execute("SELECT COALESCE(SUM(gas_cost + slippage), 0) as total_friction FROM ai_predictions")
                total_friction = cur.fetchone()['total_friction']
                
                accuracy = (correct / validated * 100) if validated > 0 else 0
                
                return {
                    "total_predictions": total,
                    "validated": validated,
                    "correct": correct,
                    "accuracy_percent": round(accuracy, 2),
                    "pending": pending,
                    "total_profit": round(total_profit, 2),
                    "total_friction": round(total_friction, 2),
                    "net_profit": round(total_profit - total_friction, 2)
                }
        except psycopg2.Error as e:
            logger.error(f"Failed to fetch accuracy stats: {e}")
            return {"error": str(e)}
        finally:
            self.pool.putconn(conn)

    def get_accuracy_by_type(self) -> List[Dict]:
        """Get accuracy stats grouped by prediction type (HOLD vs REBALANCE)"""
        conn = self.pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                    SELECT 
                        prediction_type,
                        COUNT(*) as total,
                        SUM(CASE WHEN validated = TRUE THEN 1 ELSE 0 END) as validated,
                        SUM(CASE WHEN was_correct = TRUE THEN 1 ELSE 0 END) as correct,
                        ROUND(AVG(confidence) * 100, 2) as avg_confidence_percent,
                        ROUND(SUM(profit_if_followed), 2) as total_profit
                    FROM ai_predictions
                    GROUP BY prediction_type;
                """
                cur.execute(query)
                results = cur.fetchall()
                
                for row in results:
                    if row['validated'] > 0:
                        row['accuracy_percent'] = round(row['correct'] / row['validated'] * 100, 2)
                    else:
                        row['accuracy_percent'] = None
                
                return results
        except psycopg2.Error as e:
            logger.error(f"Failed to fetch accuracy by type: {e}")
            return []
        finally:
            self.pool.putconn(conn)
    
    def get_detailed_stats(self) -> Dict:
        """Fetch comprehensive AI performance metrics from PostgreSQL"""
        query = """
        SELECT 
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE validated = TRUE) as validated,
            COUNT(*) FILTER (WHERE was_correct = TRUE AND validated = TRUE) as correct,
            SUM(profit_if_followed) FILTER (WHERE validated = TRUE) as net_profit,
            SUM(gas_cost + slippage) as total_friction,
            AVG(confidence) as avg_confidence,
            -- Accuracy by Decision Type
            COUNT(*) FILTER (WHERE prediction_type = 'HOLD' AND validated = TRUE) as hold_count,
            COUNT(*) FILTER (WHERE prediction_type = 'HOLD' AND was_correct = TRUE AND validated = TRUE) as hold_correct,
            COUNT(*) FILTER (WHERE prediction_type = 'REBALANCE' AND validated = TRUE) as reb_count,
            COUNT(*) FILTER (WHERE prediction_type = 'REBALANCE' AND was_correct = TRUE AND validated = TRUE) as reb_correct
        FROM ai_predictions;
        """
        conn = self.pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                res = cur.fetchone()
                
                # Handle None values
                validated = res['validated'] or 0
                correct = res['correct'] or 0
                hold_count = res['hold_count'] or 0
                hold_correct = res['hold_correct'] or 0
                reb_count = res['reb_count'] or 0
                reb_correct = res['reb_correct'] or 0
                
                # Calculate percentages safely
                acc = (correct / validated * 100) if validated > 0 else 0
                hold_acc = (hold_correct / hold_count * 100) if hold_count > 0 else 0
                reb_acc = (reb_correct / reb_count * 100) if reb_count > 0 else 0

                return {
                    "overview": {
                        "accuracy_pct": round(acc, 2),
                        "total_profit_usd": round(res['net_profit'] or 0, 2),
                        "total_friction_usd": round(res['total_friction'] or 0, 2),
                        "avg_confidence": round(res['avg_confidence'] or 0, 2)
                    },
                    "decisions": {
                        "hold_accuracy": round(hold_acc, 2),
                        "rebalance_accuracy": round(reb_acc, 2),
                        "total_predictions": res['total'] or 0
                    },
                    "status": {
                        "pending_validation": (res['total'] or 0) - validated
                    }
                }
        except psycopg2.Error as e:
            logger.error(f"Failed to fetch detailed stats: {e}")
            return {}
        finally:
            self.pool.putconn(conn)
    
    def get_all_predictions(self, limit: int = 50) -> List[Dict]:
        """Get all predictions, most recent first"""
        conn = self.pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                    SELECT 
                        id, timestamp, prediction_type, 
                        current_pool_id, target_pool_id,
                        current_pool_apy, target_pool_apy,
                        confidence, reason, capital_usd,
                        validated, validation_date,
                        actual_current_apy, actual_target_apy,
                        was_correct, profit_if_followed,
                        market_context, gas_cost, slippage
                    FROM ai_predictions
                    ORDER BY timestamp DESC
                    LIMIT %s
                """
                cur.execute(query, (limit,))
                rows = cur.fetchall()
                return [dict(row) for row in rows]
        except psycopg2.Error as e:
            logger.error(f"Failed to fetch predictions: {e}")
            return []
        finally:
            self.pool.putconn(conn)
