import logging
import math
from typing import List, Dict, Tuple, Optional
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
    score: float           
    allocation_pct: float  
    allocation_usd: float  
    reason: str

class TradeSizer:
    def __init__(self, 
                 max_single_allocation: float = 0.40,  
                 min_allocation: float = 0.10,         
                 max_pools: int = 4,                   
                 apy_weight: float = 0.4,
                 tvl_weight: float = 0.4,              
                 stability_weight: float = 0.2):
        self.max_single = max_single_allocation
        self.min_alloc = min_allocation
        self.max_pools = max_pools
        self.w_apy = apy_weight
        self.w_tvl = tvl_weight
        self.w_stability = stability_weight

    def score_pool(self, pool: Dict, all_pools: List[Dict]) -> float:
        """Calculate a risk-adjusted score (0-100)"""
        if not all_pools: return 0

        # 1. Normalize APY
        max_apy = max(p.get('apy', 0) for p in all_pools)
        min_apy = min(p.get('apy', 0) for p in all_pools)
        apy_range = max_apy - min_apy if max_apy != min_apy else 1
        norm_apy = (pool.get('apy', 0) - min_apy) / apy_range

        # 2. Normalize TVL (Log scale)
        tvl = pool.get('tvlUsd', 0) or pool.get('tvl_usd', 1)
        max_tvl = max(p.get('tvlUsd', 1) or p.get('tvl_usd', 1) for p in all_pools)
        norm_tvl = math.log10(max(tvl, 1)) / math.log10(max(max_tvl, 10))

        # 3. Volatility Penalty
        apy_change = abs(pool.get('apyPct1D', 0) or 0)
        vol_penalty = min(apy_change / 50, 1.0)

        score = (self.w_apy * norm_apy * 100 + 
                 self.w_tvl * norm_tvl * 100 - 
                 self.w_stability * vol_penalty * 100)
        
        return max(0, min(100, score))

    def _explain(self, pool: Dict, score: float, pct: float) -> str:
        """Generates the reasoning for the AI decision"""
        factors = []
        if pool.get('apy', 0) > 10: factors.append("Aggressive Yield")
        if (pool.get('tvlUsd', 0) or 0) > 100_000_000: factors.append("Institutional Liquidity")
        if abs(pool.get('apyPct1D', 0) or 0) < 1: factors.append("Stable Returns")
        
        return f"Score {score:.1f}. Factors: {', '.join(factors)}. Target: {pct*100:.1f}% weight."

    def size_positions(self, candidate_pools: List[Dict], total_capital: float, current_pool_id: Optional[str] = None) -> List[PoolAllocation]:
        """Core logic for determining weights"""
        if not candidate_pools: return []

        scored_pools = []
        for p in candidate_pools:
            score = self.score_pool(p, candidate_pools)
            p_id = p.get('pool')
            if current_pool_id and p_id == current_pool_id:
                score *= 1.15 # Loyalty bonus
            scored_pools.append({'data': p, 'score': score})

        scored_pools.sort(key=lambda x: x['score'], reverse=True)
        
        final_allocs = []
        allocated_pct = 0.0
        seen_assets = set()
        seen_protocols = set()

        for item in scored_pools:
            if len(final_allocs) >= self.max_pools or allocated_pct >= 1.0: break
            
            p = item['data']
            score = item['score']
            symbol = p.get('symbol', '').upper()
            protocol = p.get('project', '').lower()

            # Diversification Penalties
            for asset in ["USDC", "USDT", "DAI"]:
                if asset in symbol and asset in seen_assets:
                    score *= 0.7 
            if protocol in seen_protocols:
                score *= 0.8

            target_pct = min(self.max_single, score / 100)
            if target_pct < self.min_alloc: continue

            # Ensure we don't over-allocate
            if allocated_pct + target_pct > 1.0:
                target_pct = 1.0 - allocated_pct

            final_allocs.append(PoolAllocation(
                pool_id=p.get('pool'),
                symbol=symbol,
                chain=p.get('chain'),
                protocol=protocol,
                apy=p.get('apy'),
                tvl_usd=p.get('tvlUsd'),
                score=round(score, 2),
                allocation_pct=round(target_pct * 100, 2),
                allocation_usd=round(total_capital * target_pct, 2),
                reason=self._explain(p, score, target_pct)
            ))
            
            allocated_pct += target_pct
            for asset in ["USDC", "USDT", "DAI"]:
                if asset in symbol: seen_assets.add(asset)
            seen_protocols.add(protocol)

        return final_allocs

    def recommend(self, candidate_pools: List[Dict], total_capital: float, current_pool_id: Optional[str] = None) -> Dict:
        """Orchestrates the sizing and returns a summary for the API"""
        allocs = self.size_positions(candidate_pools, total_capital, current_pool_id)
        
        if not allocs:
            return {"status": "HOLD", "allocations": [], "summary": "No pools passed safety filters."}

        total_weighted_apy = sum(a.apy * a.allocation_pct / 100 for a in allocs)
        
        # Calculate Diversification (HHI Index)
        # Higher index = more concentrated (worse)
        hhi = sum((a.allocation_pct / 100) ** 2 for a in allocs)
        div_score = int((1 - hhi) * 100)

        return {
            "status": "REBALANCE",
            "weighted_apy": round(total_weighted_apy, 2),
            "diversification_score": div_score,
            "allocations": allocs,
            "summary": f"Split ${total_capital:,.0f} across {len(allocs)} pools. Div Score: {div_score}/100."
        }

if __name__ == "__main__":
    # Diagnostic Test
    mock_data = [
        {"pool": "1", "symbol": "USDC", "chain": "Base", "project": "aave-v3", "apy": 12.0, "tvlUsd": 200_000_000},
        {"pool": "2", "symbol": "USDC", "chain": "Ethereum", "project": "aave-v3", "apy": 11.5, "tvlUsd": 500_000_000},
        {"pool": "3", "symbol": "DAI", "chain": "Ethereum", "project": "maker", "apy": 5.0, "tvlUsd": 1_000_000_000}
    ]
    sizer = TradeSizer()
    rec = sizer.recommend(mock_data, 100000)
    print(rec['summary'])
    for a in rec['allocations']:
        print(f"-> {a.symbol} on {a.protocol}: {a.allocation_pct}% (${a.allocation_usd:,.0f})")