"""
ROI Calculator Service
Tracks and calculates Net ROI for rebalancing operations
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


class ROICalculator:
    """Calculate and track ROI for rebalancing operations"""
    
    def __init__(self):
        """Initialize ROI calculator with database connection"""
        self.db_config = {
            'dbname': os.getenv('DB_NAME', 'yield_rebalancer'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD', 'postgres'),
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': os.getenv('DB_PORT', '5432')
        }
        self.conn = None
        self._connect()
    
    def _connect(self):
        """Connect to database"""
        try:
            self.conn = psycopg2.connect(
                dbname=self.db_config['dbname'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                host=self.db_config['host'],
                port=self.db_config['port']
            )
            logger.info("Connected to ROI database")
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            self.conn = None
    
    def record_rebalance_entry(
        self,
        rebalance_id: int,
        tx_hash: str,
        entry_portfolio_value_usd: float,
        allocation_from: Dict[str, float],
        allocation_to: Dict[str, float],
        gas_cost_usd: float = 0,
        slippage_percent: float = 0
    ) -> int:
        """
        Record entry point for a rebalance operation
        
        Args:
            rebalance_id: ID from rebalance_proposals table
            tx_hash: Transaction hash
            entry_portfolio_value_usd: Portfolio value at time of rebalance
            allocation_from: Previous allocation {protocol: percentage}
            allocation_to: New allocation {protocol: percentage}
            gas_cost_usd: Gas cost in USD
            slippage_percent: Slippage percentage
            
        Returns:
            rebalance_roi_id for tracking
        """
        try:
            if not self.conn:
                self._connect()
            
            cur = self.conn.cursor()
            
            net_cost_usd = gas_cost_usd + (entry_portfolio_value_usd * slippage_percent / 100)
            
            query = """
            INSERT INTO rebalance_roi (
                rebalance_id,
                tx_hash,
                entry_portfolio_value_usd,
                entry_timestamp,
                allocation_from,
                allocation_to,
                gas_cost_usd,
                slippage_percent,
                net_cost_usd
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """
            
            cur.execute(query, (
                rebalance_id,
                tx_hash,
                entry_portfolio_value_usd,
                datetime.utcnow(),
                json.dumps(allocation_from),
                json.dumps(allocation_to),
                gas_cost_usd,
                slippage_percent,
                net_cost_usd
            ))
            
            roi_id = cur.fetchone()[0]
            self.conn.commit()
            
            logger.info(f"Recorded rebalance entry: ROI ID {roi_id}, TX: {tx_hash}")
            return roi_id
            
        except Exception as e:
            logger.error(f"Error recording rebalance entry: {e}")
            if self.conn:
                self.conn.rollback()
            return -1
    
    def calculate_roi_snapshot(
        self,
        rebalance_roi_id: int,
        current_portfolio_value_usd: float,
        yield_earned_usd: float = 0,
        realized_gains_usd: float = 0,
        unrealized_gains_usd: float = 0
    ) -> Optional[Dict]:
        """
        Calculate and store ROI snapshot at current time
        
        Args:
            rebalance_roi_id: ID of rebalance_roi record
            current_portfolio_value_usd: Current portfolio value
            yield_earned_usd: Yield earned from protocols
            realized_gains_usd: Realized P&L from allocation changes
            unrealized_gains_usd: Unrealized P&L
            
        Returns:
            Dictionary with ROI metrics
        """
        try:
            if not self.conn:
                self._connect()
            
            cur = self.conn.cursor(cursor_factory=RealDictCursor)
            
            # Get entry data
            query = "SELECT * FROM rebalance_roi WHERE id = %s"
            cur.execute(query, (rebalance_roi_id,))
            entry = cur.fetchone()
            
            if not entry:
                logger.error(f"Rebalance ROI record {rebalance_roi_id} not found")
                return None
            
            entry_value = entry['entry_portfolio_value_usd']
            entry_time = entry['entry_timestamp']
            
            # Calculate metrics
            total_gain_loss_usd = yield_earned_usd + realized_gains_usd + unrealized_gains_usd
            roi_percent = (total_gain_loss_usd / entry_value * 100) if entry_value > 0 else 0
            
            # Calculate APY (annualized)
            time_held = datetime.utcnow() - entry_time
            days_held = max(time_held.total_seconds() / 86400, 1)  # At least 1 day
            apy_achieved = (roi_percent / days_held) * 365 if days_held > 0 else 0
            
            # Store snapshot
            snapshot_query = """
            INSERT INTO roi_snapshots (
                rebalance_roi_id,
                snapshot_date,
                current_portfolio_value_usd,
                yield_earned_usd,
                realized_gains_usd,
                unrealized_gains_usd,
                total_gain_loss_usd,
                roi_percent,
                apy_achieved_percent
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """
            
            cur.execute(snapshot_query, (
                rebalance_roi_id,
                datetime.utcnow(),
                current_portfolio_value_usd,
                yield_earned_usd,
                realized_gains_usd,
                unrealized_gains_usd,
                total_gain_loss_usd,
                roi_percent,
                apy_achieved
            ))
            
            snapshot_id = cur.fetchone()['id']
            self.conn.commit()
            
            result = {
                'snapshot_id': snapshot_id,
                'roi_percent': round(roi_percent, 4),
                'apy_achieved_percent': round(apy_achieved, 4),
                'total_gain_loss_usd': round(total_gain_loss_usd, 2),
                'yield_earned_usd': round(yield_earned_usd, 2),
                'days_held': round(days_held, 2)
            }
            
            logger.info(f"ROI Snapshot: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Error calculating ROI snapshot: {e}")
            if self.conn:
                self.conn.rollback()
            return None
    
    def get_cumulative_roi(self) -> Optional[Dict]:
        """
        Get cumulative ROI across all rebalances
        
        Returns:
            Dictionary with cumulative ROI metrics
        """
        try:
            if not self.conn:
                self._connect()
            
            cur = self.conn.cursor(cursor_factory=RealDictCursor)
            
            # Get all active rebalances
            query = """
            SELECT 
                COUNT(*) as total_rebalances,
                SUM(COALESCE(gas_cost_usd, 0)) as total_gas_cost,
                SUM(COALESCE(slippage_percent, 0)) as total_slippage_percent,
                AVG(COALESCE(slippage_percent, 0)) as avg_slippage_percent
            FROM rebalance_roi
            WHERE created_at >= CURRENT_DATE - INTERVAL '30 days';
            """
            
            cur.execute(query)
            stats = cur.fetchone()
            
            # Get ROI snapshots
            roi_query = """
            SELECT 
                MAX(roi_percent) as best_roi,
                MIN(roi_percent) as worst_roi,
                AVG(roi_percent) as avg_roi,
                SUM(total_gain_loss_usd) as cumulative_gain,
                SUM(yield_earned_usd) as total_yield_earned
            FROM roi_snapshots
            WHERE snapshot_date >= CURRENT_DATE - INTERVAL '30 days'
            AND status = 'active';
            """
            
            cur.execute(roi_query)
            roi_stats = cur.fetchone()
            
            result = {
                'total_rebalances': stats['total_rebalances'] or 0,
                'cumulative_roi_percent': round(roi_stats['avg_roi'] or 0, 4),
                'best_roi_percent': round(roi_stats['best_roi'] or 0, 4),
                'worst_roi_percent': round(roi_stats['worst_roi'] or 0, 4),
                'cumulative_gain_usd': round(roi_stats['cumulative_gain'] or 0, 2),
                'total_yield_earned_usd': round(roi_stats['total_yield_earned'] or 0, 2),
                'total_gas_cost_usd': round(stats['total_gas_cost'] or 0, 2),
                'avg_slippage_percent': round(stats['avg_slippage_percent'] or 0, 4)
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting cumulative ROI: {e}")
            return None
    
    def get_latest_roi_snapshot(self) -> Optional[Dict]:
        """
        Get the latest ROI snapshot for dashboard display
        
        Returns:
            Dictionary with latest ROI metrics
        """
        try:
            if not self.conn:
                self._connect()
            
            cur = self.conn.cursor(cursor_factory=RealDictCursor)
            
            query = """
            SELECT 
                rs.id,
                rs.roi_percent,
                rs.apy_achieved_percent,
                rs.total_gain_loss_usd,
                rs.yield_earned_usd,
                rs.realized_gains_usd,
                rs.unrealized_gains_usd,
                rs.snapshot_date,
                rr.entry_portfolio_value_usd,
                rr.gas_cost_usd,
                rr.slippage_percent,
                rr.allocation_to
            FROM roi_snapshots rs
            JOIN rebalance_roi rr ON rs.rebalance_roi_id = rr.id
            WHERE rs.status = 'active'
            ORDER BY rs.snapshot_date DESC
            LIMIT 1;
            """
            
            cur.execute(query)
            snapshot = cur.fetchone()
            
            if not snapshot:
                return None
            
            result = {
                'roi_percent': round(snapshot['roi_percent'], 4),
                'apy_achieved_percent': round(snapshot['apy_achieved_percent'], 4),
                'total_gain_loss_usd': round(snapshot['total_gain_loss_usd'], 2),
                'yield_earned_usd': round(snapshot['yield_earned_usd'], 2),
                'gas_cost_usd': round(snapshot['gas_cost_usd'], 2),
                'entry_portfolio_value_usd': round(snapshot['entry_portfolio_value_usd'], 2),
                'snapshot_date': snapshot['snapshot_date'].isoformat() if snapshot['snapshot_date'] else None,
                'allocation': json.loads(snapshot['allocation_to']) if snapshot['allocation_to'] else {}
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting latest ROI snapshot: {e}")
            return None
    
    def update_daily_roi_summary(self, summary_date: str = None) -> bool:
        """
        Update daily ROI summary statistics
        
        Args:
            summary_date: Date to summarize (defaults to today)
            
        Returns:
            True if successful
        """
        try:
            if not self.conn:
                self._connect()
            
            if not summary_date:
                summary_date = datetime.utcnow().date()
            
            cur = self.conn.cursor()
            
            # Get daily stats
            stats_query = """
            SELECT 
                COUNT(DISTINCT rr.id) as rebalance_count,
                SUM(COALESCE(rs.total_gain_loss_usd, 0)) as total_gain,
                AVG(COALESCE(rs.roi_percent, 0)) as avg_roi,
                MAX(COALESCE(rs.roi_percent, 0)) as best_roi,
                MIN(COALESCE(rs.roi_percent, 0)) as worst_roi,
                SUM(COALESCE(rr.gas_cost_usd, 0)) as total_gas,
                SUM(COALESCE(rs.yield_earned_usd, 0)) as total_yield
            FROM rebalance_roi rr
            LEFT JOIN roi_snapshots rs ON rr.id = rs.rebalance_roi_id
            WHERE DATE(rr.entry_timestamp) = %s
            AND rs.status = 'active';
            """
            
            cur.execute(stats_query, (summary_date,))
            stats = cur.fetchone()
            
            if not stats:
                logger.warning(f"No ROI data for {summary_date}")
                return False
            
            rebalance_count, total_gain, avg_roi, best_roi, worst_roi, total_gas, total_yield = stats
            
            # Upsert summary
            summary_query = """
            INSERT INTO roi_summary (
                summary_date,
                total_rebalances_count,
                cumulative_portfolio_gain_usd,
                cumulative_roi_percent,
                net_yield_earned_usd,
                total_gas_cost_usd,
                best_rebalance_roi_percent,
                worst_rebalance_roi_percent,
                avg_rebalance_roi_percent
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (summary_date) DO UPDATE SET
                total_rebalances_count = EXCLUDED.total_rebalances_count,
                cumulative_portfolio_gain_usd = EXCLUDED.cumulative_portfolio_gain_usd,
                cumulative_roi_percent = EXCLUDED.cumulative_roi_percent,
                net_yield_earned_usd = EXCLUDED.net_yield_earned_usd,
                total_gas_cost_usd = EXCLUDED.total_gas_cost_usd,
                best_rebalance_roi_percent = EXCLUDED.best_rebalance_roi_percent,
                worst_rebalance_roi_percent = EXCLUDED.worst_rebalance_roi_percent,
                avg_rebalance_roi_percent = EXCLUDED.avg_rebalance_roi_percent,
                updated_at = CURRENT_TIMESTAMP;
            """
            
            cur.execute(summary_query, (
                summary_date,
                rebalance_count or 0,
                total_gain or 0,
                avg_roi or 0,
                total_yield or 0,
                total_gas or 0,
                best_roi,
                worst_roi,
                avg_roi or 0
            ))
            
            self.conn.commit()
            logger.info(f"Updated ROI summary for {summary_date}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating ROI summary: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("Closed ROI database connection")


# Testing
if __name__ == "__main__":
    calculator = ROICalculator()
    
    # Test recording entry
    roi_id = calculator.record_rebalance_entry(
        rebalance_id=1,
        tx_hash="0x123abc",
        entry_portfolio_value_usd=100000,
        allocation_from={"Aave": 50, "Compound": 50},
        allocation_to={"Aave": 70, "Compound": 30},
        gas_cost_usd=50,
        slippage_percent=0.1
    )
    
    # Test calculating snapshot
    if roi_id > 0:
        snapshot = calculator.calculate_roi_snapshot(
            rebalance_roi_id=roi_id,
            current_portfolio_value_usd=101000,
            yield_earned_usd=500,
            realized_gains_usd=300
        )
        print(f"ROI Snapshot: {snapshot}")
    
    # Get cumulative ROI
    cum_roi = calculator.get_cumulative_roi()
    print(f"Cumulative ROI: {cum_roi}")
    
    # Get latest snapshot
    latest = calculator.get_latest_roi_snapshot()
    print(f"Latest ROI: {latest}")
    
    calculator.close()
