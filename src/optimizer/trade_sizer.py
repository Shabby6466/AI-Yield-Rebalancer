"""
APY-Based Trade Sizing Engine
Instead of 100% all-in, allocates capital proportionally based on:
- Risk-adjusted yield (Sharpe-like scoring)
- TVL depth (larger pools get more trust)
- Yield stability (volatile APYs get less allocation)
- Max position cap (never more than X% in one pool)
"""

import logging
import math
from typing import List, Dict, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PoolAllocation:
    """Represents a sized position in a pool"""
    pool_id: str
    symbol: str
    chain: str
    protocol: str
    apy: float
    tvl_usd: float
    score: float           # Risk-adjusted score (0-100)
    allocation_pct: float  # % of capital to allocate
    allocation_usd: float  # $ amount to allocate
    reason: str


class TradeSizer:
    """
    Determines HOW MUCH to put into each pool, not just WHICH pool.
    
    Scoring Formula:
        Score = (APY_Weight * Normalized_APY) 
              + (TVL_Weight * Normalized_TVL) 
              - (Volatility_Penalty * Normalized_Vol)
    
    Then allocations are proportional to scores, capped at max_single_allocation.
    """

    def __init__(self, 
                 max_single_allocation: float = 0.50,  # Max 50% in one pool
                 min_allocation: float = 0.05,          # Min 5% to bother
                 max_pools: int = 3,                     # Max 3 concurrent positions
                 apy_weight: float = 0.5,
                 tvl_weight: float = 0.3,
                 stability_weight: float = 0.2):
        """
        Args:
            max_single_allocation: Maximum % of capital in any single pool
            min_allocation: Minimum % to allocate (below this, skip the pool)
            max_pools: Maximum number of pools to split across
            apy_weight: How much to weight raw APY (0-1)
            tvl_weight: How much to weight TVL depth (0-1)
            stability_weight: How much to penalize volatility (0-1)
        """
        self.max_single = max_single_allocation
        self.min_alloc = min_allocation
        self.max_pools = max_pools
        self.w_apy = apy_weight
        self.w_tvl = tvl_weight
        self.w_stability = stability_weight

    def score_pool(self, pool: Dict, all_pools: List[Dict]) -> float:
        """
        Calculate a risk-adjusted score for a single pool.
        
        Args:
            pool: Pool data dict with 'apy', 'tvl_usd', etc.
            all_pools: All candidate pools (for normalization)
            
        Returns:
            Score between 0-100
        """
        if not all_pools:
            return 0

        # Normalize APY (0-1 range relative to peers)
        max_apy = max(p.get('apy', 0) for p in all_pools)
        min_apy = min(p.get('apy', 0) for p in all_pools)
        apy_range = max_apy - min_apy if max_apy != min_apy else 1
        norm_apy = (pool.get('apy', 0) - min_apy) / apy_range

        # Normalize TVL (log scale, higher = better = more liquid)
        tvl = pool.get('tvl_usd', 0) or pool.get('tvlUsd', 0)
        max_tvl = max(p.get('tvl_usd', 0) or p.get('tvlUsd', 0) for p in all_pools)
        norm_tvl = math.log10(max(tvl, 1)) / math.log10(max(max_tvl, 10))

        # Volatility penalty (if available)
        # Use apy_pct1D (1-day change) as a proxy for instability
        apy_change = abs(pool.get('apyPct1D', 0) or 0)
        # Normalize: >50% daily change is very volatile
        vol_penalty = min(apy_change / 50, 1.0)

        # Weighted score
        score = (
            self.w_apy * norm_apy * 100
            + self.w_tvl * norm_tvl * 100
            - self.w_stability * vol_penalty * 100
        )

        return max(0, min(100, score))

    def size_positions(self, 
                       candidate_pools: List[Dict], 
                       total_capital: float,
                       current_pool_id: str = None) -> List[PoolAllocation]:
        """
        Given a list of candidate pools and total capital,
        return sized allocations for each.
        
        Args:
            candidate_pools: List of pool dicts (pre-filtered for eligibility)
            total_capital: Total USD to allocate
            current_pool_id: Pool we're currently in (gets a loyalty bonus)
            
        Returns:
            List of PoolAllocation objects, sorted by allocation descending
        """
        if not candidate_pools:
            return []

        # Score all pools
        scored = []
        for pool in candidate_pools:
            score = self.score_pool(pool, candidate_pools)
            
            # Loyalty bonus: slight preference for current pool (avoid unnecessary moves)
            pool_id = pool.get('pool_id', pool.get('pool', ''))
            if current_pool_id and pool_id == current_pool_id:
                score *= 1.1  # 10% bonus for staying put
            
            scored.append((pool, score))

        # Sort by score descending, take top N
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:self.max_pools]

        # Calculate proportional allocations
        total_score = sum(s for _, s in top)
        if total_score == 0:
            return []

        allocations = []
        remaining_capital = total_capital

        for pool, score in top:
            # Proportional allocation
            raw_pct = score / total_score
            
            # Apply cap
            capped_pct = min(raw_pct, self.max_single)
            
            # Skip if below minimum
            if capped_pct < self.min_alloc:
                continue
            
            alloc_usd = total_capital * capped_pct
            pool_id = pool.get('pool_id', pool.get('pool', ''))
            
            allocations.append(PoolAllocation(
                pool_id=pool_id,
                symbol=pool.get('symbol', 'unknown'),
                chain=pool.get('chain', ''),
                protocol=pool.get('protocol', pool.get('project', '')),
                apy=pool.get('apy', 0),
                tvl_usd=pool.get('tvl_usd', 0) or pool.get('tvlUsd', 0),
                score=round(score, 2),
                allocation_pct=round(capped_pct * 100, 2),
                allocation_usd=round(alloc_usd, 2),
                reason=self._explain(pool, score, capped_pct)
            ))

        # Normalize so total = 100%
        total_allocated = sum(a.allocation_pct for a in allocations)
        if total_allocated > 0 and total_allocated != 100:
            scale = 100 / total_allocated
            for a in allocations:
                a.allocation_pct = round(a.allocation_pct * scale, 2)
                a.allocation_usd = round(total_capital * a.allocation_pct / 100, 2)

        return allocations

    def _explain(self, pool: Dict, score: float, pct: float) -> str:
        """Generate human-readable explanation for the allocation"""
        apy = pool.get('apy', 0)
        tvl = pool.get('tvl_usd', 0) or pool.get('tvlUsd', 0)
        
        reasons = []
        if apy > 20:
            reasons.append(f"High yield ({apy:.1f}%)")
        elif apy > 5:
            reasons.append(f"Moderate yield ({apy:.1f}%)")
        else:
            reasons.append(f"Low yield ({apy:.1f}%)")
        
        if tvl > 100_000_000:
            reasons.append("Deep liquidity")
        elif tvl > 10_000_000:
            reasons.append("Good liquidity")
        else:
            reasons.append("Thin liquidity")
        
        return f"Score {score:.0f}/100. {'. '.join(reasons)}. Allocation: {pct*100:.1f}%"

    def recommend(self, candidate_pools: List[Dict], total_capital: float,
                  current_pool_id: str = None) -> Dict:
        """
        High-level recommendation with summary.
        
        Returns:
            Dict with 'allocations', 'summary', 'diversification_score'
        """
        allocs = self.size_positions(candidate_pools, total_capital, current_pool_id)
        
        if not allocs:
            return {
                "allocations": [],
                "summary": "No suitable pools found for allocation.",
                "diversification_score": 0
            }

        # Diversification score: 1 = all in one pool, 100 = perfectly spread
        if len(allocs) == 1:
            div_score = 10
        else:
            # Herfindahl Index (lower concentration = higher diversification)
            hhi = sum((a.allocation_pct / 100) ** 2 for a in allocs)
            div_score = int((1 - hhi) * 100)

        total_weighted_apy = sum(a.apy * a.allocation_pct / 100 for a in allocs)

        summary = (
            f"Recommended: Split ${total_capital:,.0f} across {len(allocs)} pools. "
            f"Weighted APY: {total_weighted_apy:.2f}%. "
            f"Diversification: {div_score}/100."
        )

        return {
            "allocations": allocs,
            "summary": summary,
            "diversification_score": div_score,
            "weighted_apy": round(total_weighted_apy, 2)
        }


if __name__ == "__main__":
    # Quick test with mock data
    mock_pools = [
        {"pool": "aave-usdc", "symbol": "USDC", "chain": "Ethereum", "project": "aave-v3",
         "apy": 8.5, "tvlUsd": 500_000_000, "stablecoin": True, "apyPct1D": 0.2},
        {"pool": "comp-usdc", "symbol": "USDC", "chain": "Base", "project": "compound-v3",
         "apy": 12.0, "tvlUsd": 200_000_000, "stablecoin": True, "apyPct1D": -1.5},
        {"pool": "curve-3crv", "symbol": "DAI-USDC-USDT", "chain": "Ethereum", "project": "curve-dex",
         "apy": 5.2, "tvlUsd": 800_000_000, "stablecoin": True, "apyPct1D": 0.1},
    ]

    sizer = TradeSizer(max_single_allocation=0.50, max_pools=3)
    result = sizer.recommend(mock_pools, total_capital=100_000)
    
    print(result['summary'])
    print()
    for a in result['allocations']:
        print(f"  {a.symbol} ({a.protocol}): ${a.allocation_usd:,.0f} ({a.allocation_pct}%) | APY: {a.apy}% | Score: {a.score}")
