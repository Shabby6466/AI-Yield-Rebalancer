"""
Backtesting Engine for AI Yield Rebalancer
Simulates the strategy on historical data to answer:
"If I had used this AI over the past N days, would I have made money?"
"""

import logging
import math
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from src.data.timeseries_db import TimeseriesDB
from src.optimizer.gas import GasOptimizer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Represents a single rebalance action"""
    date: str
    action: str  # "REBALANCE" or "HOLD"
    from_pool: str
    from_apy: float
    to_pool: str
    to_apy: float
    capital: float
    cost: float  # Gas + Swap fees
    reason: str


@dataclass
class BacktestResult:
    """Complete backtest output"""
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    annualized_return_pct: float
    total_trades: int
    total_holds: int
    total_costs: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate: float  # % of trades that improved yield
    avg_apy_earned: float
    trades: List[Trade] = field(default_factory=list)
    daily_values: List[Dict] = field(default_factory=list)  # For charting

    def summary(self) -> str:
        return f"""
========================================
  BACKTEST RESULTS
========================================
  Period:          {self.start_date} -> {self.end_date}
  Initial Capital: ${self.initial_capital:,.2f}
  Final Capital:   ${self.final_capital:,.2f}
  
  Total Return:    {self.total_return_pct:.2f}%
  Annualized:      {self.annualized_return_pct:.2f}%
  Avg APY Earned:  {self.avg_apy_earned:.2f}%
  
  Trades Made:     {self.total_trades}
  Holds:           {self.total_holds}
  Win Rate:        {self.win_rate:.1f}%
  Total Costs:     ${self.total_costs:.2f}
  
  Max Drawdown:    {self.max_drawdown_pct:.2f}%
  Sharpe Ratio:    {self.sharpe_ratio:.2f}
========================================
"""


class BacktestEngine:
    """
    Simulates the AI rebalancing strategy on historical data.
    
    Strategy logic:
    1. Each day, look at available pools
    2. Find the best pool (highest APY, >$1M TVL, stablecoin)
    3. If current pool APY is much worse, simulate rebalance (with costs)
    4. Track daily PnL and portfolio value
    """

    def __init__(self, min_apy_improvement: float = 2.0, cost_multiplier: float = 10.0):
        """
        Args:
            min_apy_improvement: Minimum APY percentage point improvement to trigger rebalance
            cost_multiplier: Risk multiplier for cost calculation (higher = more conservative)
        """
        self.db = TimeseriesDB()
        self.gas = GasOptimizer(risk_multiplier=cost_multiplier)
        self.min_improvement = min_apy_improvement

    def run(self, 
            initial_capital: float = 100_000,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None,
            max_apy_cap: float = 200.0) -> BacktestResult:
        """
        Run the backtest simulation.
        
        Args:
            initial_capital: Starting capital in USD
            start_date: Start date (YYYY-MM-DD), defaults to earliest data
            end_date: End date (YYYY-MM-DD), defaults to latest data
            max_apy_cap: Ignore pools with APY > this (filter noise)
            
        Returns:
            BacktestResult with complete analysis
        """
        # Get available dates
        all_dates = self.db.get_unique_dates()
        if not all_dates:
            raise ValueError("No data in database. Run the collector first: python -m src.scheduler.collector --backfill")

        # Apply date filters
        if start_date:
            all_dates = [d for d in all_dates if d >= start_date]
        if end_date:
            all_dates = [d for d in all_dates if d <= end_date]

        if len(all_dates) < 2:
            raise ValueError(f"Need at least 2 days of data. Found {len(all_dates)} days.")

        logger.info(f"Backtesting {len(all_dates)} days: {all_dates[0]} -> {all_dates[-1]}")
        logger.info(f"Capital: ${initial_capital:,.0f} | Min Improvement: {self.min_improvement}% | APY Cap: {max_apy_cap}%")

        # --- Simulation State ---
        capital = initial_capital
        current_pool_id = None
        current_apy = 0.0
        current_symbol = "CASH"

        trades: List[Trade] = []
        daily_values: List[Dict] = []
        daily_returns: List[float] = []
        peak_value = initial_capital
        max_drawdown = 0.0
        total_costs = 0.0
        total_yield_earned = 0.0
        wins = 0

        # --- Day-by-Day Simulation ---
        for i, date in enumerate(all_dates):
            # Get all available pools on this day
            pools = self.db.get_all_pools_at(date)
            
            # Filter: stablecoins, reasonable APY, sufficient TVL
            pools = [
                p for p in pools
                if p.get('is_stablecoin', 0) == 1
                and 0 < p.get('apy', 0) < max_apy_cap
                and p.get('tvl_usd', 0) > 1_000_000
            ]

            if not pools:
                # No data for this day, carry forward
                daily_values.append({
                    "date": date,
                    "value": capital,
                    "apy": current_apy,
                    "pool": current_symbol,
                    "action": "NO_DATA"
                })
                continue

            # Find best pool today
            best_pool = max(pools, key=lambda p: p.get('apy', 0))
            best_apy = best_pool['apy']
            best_id = best_pool['pool_id']
            best_symbol = best_pool.get('symbol', best_id[:8])

            # If we're not in any pool yet, enter the best one (Day 1)
            if current_pool_id is None:
                current_pool_id = best_id
                current_apy = best_apy
                current_symbol = best_symbol
                
                trades.append(Trade(
                    date=date, action="ENTER", 
                    from_pool="CASH", from_apy=0,
                    to_pool=current_symbol, to_apy=current_apy,
                    capital=capital, cost=0, reason="Initial entry"
                ))
            else:
                # Check if current pool still has data today
                current_data = next((p for p in pools if p['pool_id'] == current_pool_id), None)
                if current_data:
                    current_apy = current_data['apy']

                # Decision: Should we rebalance?
                apy_diff = best_apy - current_apy
                
                if apy_diff > self.min_improvement and best_id != current_pool_id:
                    # Simulate cost check
                    is_profitable, cost, breakdown = self.gas.should_rebalance(
                        current_apy / 100, best_apy / 100, capital
                    )

                    if is_profitable:
                        # Execute rebalance
                        capital -= cost
                        total_costs += cost
                        wins += 1

                        trades.append(Trade(
                            date=date, action="REBALANCE",
                            from_pool=current_symbol, from_apy=current_apy,
                            to_pool=best_symbol, to_apy=best_apy,
                            capital=capital, cost=cost,
                            reason=f"+{apy_diff:.2f}% APY gain, cost ${cost:.2f}"
                        ))

                        current_pool_id = best_id
                        current_apy = best_apy
                        current_symbol = best_symbol
                    else:
                        trades.append(Trade(
                            date=date, action="HOLD",
                            from_pool=current_symbol, from_apy=current_apy,
                            to_pool=best_symbol, to_apy=best_apy,
                            capital=capital, cost=0,
                            reason=f"Cost too high (${cost:.2f}) for +{apy_diff:.2f}% gain"
                        ))

            # Apply daily yield
            daily_yield = capital * (current_apy / 100) / 365
            capital += daily_yield
            total_yield_earned += daily_yield

            # Track daily return
            if len(daily_values) > 0:
                prev_value = daily_values[-1]['value']
                daily_ret = (capital - prev_value) / prev_value if prev_value > 0 else 0
                daily_returns.append(daily_ret)

            # Track drawdown
            if capital > peak_value:
                peak_value = capital
            dd = (peak_value - capital) / peak_value * 100
            if dd > max_drawdown:
                max_drawdown = dd

            daily_values.append({
                "date": date,
                "value": round(capital, 2),
                "apy": round(current_apy, 2),
                "pool": current_symbol,
                "action": trades[-1].action if trades else "HOLD"
            })

        # --- Calculate Final Metrics ---
        total_days = len(all_dates)
        total_return = ((capital - initial_capital) / initial_capital) * 100
        annualized = total_return * (365 / total_days) if total_days > 0 else 0

        # Sharpe Ratio (annualized, risk-free rate = 5%)
        if daily_returns:
            import statistics
            avg_daily = statistics.mean(daily_returns)
            std_daily = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 1
            risk_free_daily = 0.05 / 365
            sharpe = ((avg_daily - risk_free_daily) / std_daily) * math.sqrt(365) if std_daily > 0 else 0
        else:
            sharpe = 0

        # Average APY earned (weighted by time)
        avg_apy = sum(d['apy'] for d in daily_values) / len(daily_values) if daily_values else 0

        # Win rate
        rebalance_trades = [t for t in trades if t.action == "REBALANCE"]
        hold_trades = [t for t in trades if t.action == "HOLD"]

        win_rate = (wins / len(rebalance_trades) * 100) if rebalance_trades else 0

        result = BacktestResult(
            start_date=all_dates[0],
            end_date=all_dates[-1],
            initial_capital=initial_capital,
            final_capital=round(capital, 2),
            total_return_pct=round(total_return, 2),
            annualized_return_pct=round(annualized, 2),
            total_trades=len(rebalance_trades),
            total_holds=len(hold_trades),
            total_costs=round(total_costs, 2),
            max_drawdown_pct=round(max_drawdown, 2),
            sharpe_ratio=round(sharpe, 2),
            win_rate=round(win_rate, 1),
            avg_apy_earned=round(avg_apy, 2),
            trades=trades,
            daily_values=daily_values
        )

        logger.info(result.summary())
        return result


# --- Benchmark: Buy and Hold (No Rebalancing) ---
class BuyAndHoldBenchmark:
    """
    Benchmark: What if you just stayed in one pool the entire time?
    Useful for comparing against the AI strategy.
    """

    def __init__(self):
        self.db = TimeseriesDB()

    def run(self, pool_id: str, initial_capital: float = 100_000,
            start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict:
        """
        Simulate holding a single pool for the entire period.
        """
        history = self.db.get_pool_history(pool_id, days=365)
        
        if start_date:
            history = [h for h in history if h['timestamp'] >= start_date]
        if end_date:
            history = [h for h in history if h['timestamp'] <= end_date]

        if not history:
            return {"error": "No data found"}

        capital = initial_capital
        daily_values = []

        for point in history:
            daily_yield = capital * (point['apy'] / 100) / 365
            capital += daily_yield
            daily_values.append({
                "date": point['timestamp'],
                "value": round(capital, 2),
                "apy": point['apy']
            })

        total_return = ((capital - initial_capital) / initial_capital) * 100

        return {
            "strategy": "Buy and Hold",
            "pool_id": pool_id,
            "initial_capital": initial_capital,
            "final_capital": round(capital, 2),
            "total_return_pct": round(total_return, 2),
            "days": len(history),
            "daily_values": daily_values
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backtest AI Yield Strategy")
    parser.add_argument("--capital", type=float, default=100_000, help="Initial capital")
    parser.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--min-improvement", type=float, default=2.0, help="Min APY gain to trigger rebalance")
    args = parser.parse_args()

    engine = BacktestEngine(min_apy_improvement=args.min_improvement)
    result = engine.run(
        initial_capital=args.capital,
        start_date=args.start,
        end_date=args.end
    )
    print(result.summary())
