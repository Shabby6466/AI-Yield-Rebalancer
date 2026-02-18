"""
Vampire Attack & Liquidity Filter
Prevents rebalancing if slippage would be too high (>0.1%)
"""
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class LiquidityFilter:
    def __init__(self, max_slippage: float = 0.001, v_attack_threshold: float = 0.15):
        """
        Args:
            max_slippage: Maximum allowable slippage (default 0.1%)
            v_attack_threshold: 0.15 = 15% TVL drop in 24h indicates vampire attack risk
        """
        self.max_slippage = max_slippage
        self.v_attack_threshold = v_attack_threshold

    def is_safe(self, pool_data: Dict, trade_size_usd: float, tvl_history: Optional[list] = None) -> bool:
        tvl = float(pool_data.get('tvlUsd', 0))
        project = pool_data.get('project', '').lower()
        
        if tvl < trade_size_usd: # Basic sanity check
            return False

        # 1. Smart Price Impact Calculation
        if 'uniswap-v3' in project:
            # Uni V3 Stablecoin pools are ~20-50x more efficient than V2
            # We assume a 20x multiplier for safety in stablecoin concentrated ranges
            effective_liquidity = tvl * 20 
            impact = trade_size_usd / effective_liquidity
        elif 'aave' in project or 'compound' in project:
            # Lending: No slippage, but depositing >2% of TVL significantly dilutes APY
            impact = trade_size_usd / tvl
            if impact > 0.02: 
                logger.warning(f"Dilution Risk: Trade is {impact:.2%} of lending pool.")
                return False
            impact = 0 # Set to 0 to pass the slippage check
        else:
            # Default XY=K assumption
            impact = trade_size_usd / (tvl * 0.5)

        if impact > self.max_slippage:
            logger.warning(f"Liquidity Risk: Est. Impact {impact:.4%} > {self.max_slippage:.4%}")
            return False

        # 2. Rate-of-Change Vampire Attack Detection
        if tvl_history and len(tvl_history) >= 2:
            last_tvl = tvl_history[-1]
            # Use the most recent data point to detect sharp drops
            tvl_drop = (tvl - last_tvl) / last_tvl
            if tvl_drop < -self.v_attack_threshold:
                logger.error(f"Vampire/Exit detected! TVL dropped {abs(tvl_drop):.2%} recently.")
                return False

        return True