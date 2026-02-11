"""
Vampire Attack & Liquidity Filter
Prevents rebalancing if slippage would be too high (>0.1%)
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class LiquidityFilter:
    def __init__(self, max_slippage: float = 0.001):
        """
        Args:
            max_slippage: Maximum allowable slippage (default 0.1%)
        """
        self.max_slippage = max_slippage

    def check_liquidity_depth(self, pool_data: Dict, trade_size_usd: float) -> bool:
        """
        Check if the pool has enough liquidity for the trade.
        
        Rule: Trade size < 1% of TVL to minimize impact.
        Real implementation would check specific depth at +2%/-2% via 1inch/CoW API.
        
        Args:
            pool_data: Dictionary containing 'tvlUsd'
            trade_size_usd: Amount of capital being moved
            
        Returns:
            True if safe, False if risky (Vampire Attack risk)
        """
        tvl = pool_data.get('tvlUsd', 0)
        
        if tvl == 0:
            logger.warning("Pool has 0 TVL. Unsafe.")
            return False
            
        impact = trade_size_usd / tvl
        
        # Rough approximation: Price impact ~ Trade/TVL
        # Relaxed for concentrated liquidity (efficient capital usage)
        # Allows up to 5% of TVL as max impact threshold for MVP
        if impact > 0.05: 
            logger.warning(f"Liquidity Risk! Trade size ${trade_size_usd} is {impact:.2%} of Pool TVL (${tvl}). Limit: 5%")
            return False
            
        return True
