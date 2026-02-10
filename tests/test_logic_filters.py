"""
Integration Test for Logic Filters
Verifies that all 3 filters work correctly with mock data.
"""
import pytest
from src.ml.mean_reversion import MeanReversionFilter
from src.risk.liquidity import LiquidityFilter
from src.optimizer.gas import GasOptimizer

def test_mean_reversion():
    """Test Mean Reversion Logic"""
    filter = MeanReversionFilter(spike_threshold=2.0)
    
    historical = [0.04, 0.041, 0.042, 0.039, 0.040] # Mean ~0.04 (4%)
    
    # CASE 1: Normal trend (4.2% APY) -> Z-score < 2 -> False
    assert filter.is_spike(0.042, historical) == False
    
    # CASE 2: Huge spike (15% APY) -> Should trigger Mean Reversion
    assert filter.is_spike(0.15, historical) == True
    print("✅ Mean Reversion Filter passed.")

def test_liquidity_filter():
    """Test Vampire Attack Logic"""
    filter = LiquidityFilter(max_slippage=0.001) # 0.1%
    
    pool_data = {'tvlUsd': 1_000_000} # $1M TVL
    
    # CASE 1: Small trade ($1,000) -> 0.1% impact -> SAFE
    assert filter.check_liquidity_depth(pool_data, 1_000) == True
    
    # CASE 2: Large trade ($200,000) -> 20% impact -> RISKY (Vampire Attack risk)
    assert filter.check_liquidity_depth(pool_data, 200_000) == False
    print("✅ Liquidity Filter passed.")

def test_gas_optimizer():
    """Test Gas Awareness"""
    optimizer = GasOptimizer(risk_multiplier=10.0)
    
    capital = 100_000 # $100k portfolio
    current_apy = 0.05 # 5%
    
    # CASE 1: Profitable Move
    # Gain: +3% APY -> $3k/year -> $250/month
    # Cost: Assume $50 gas
    # Ratio: 250 / 50 = 5x (Wait... our threshold is 10x!)
    # Let's adjust inputs to pass
    
    # Gain: +10% APY -> $10k/year -> $833/month
    # Ratio: 833 / 50 = 16x -> PASS
    assert optimizer.should_rebalance(current_apy, 0.15, capital) == True
    
    # CASE 2: Unprofitable Move
    # Gain: +0.5% APY -> $500/year -> $41/month
    # Cost: $50 gas
    # Ratio: < 1x -> FAIL
    assert optimizer.should_rebalance(current_apy, 0.055, capital) == False
    print("✅ Gas Optimizer passed.")

if __name__ == "__main__":
    test_mean_reversion()
    test_liquidity_filter()
    test_gas_optimizer()
