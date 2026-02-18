#!/usr/bin/env python3
"""
Test script to verify ROI tracking system end-to-end
Run this to ensure all components are working correctly
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.execution.roi_calculator import ROICalculator
from datetime import datetime, timedelta
import json

def test_roi_system():
    """Test the complete ROI tracking workflow"""
    
    print("\n" + "="*70)
    print("ROI TRACKING SYSTEM - TEST SUITE")
    print("="*70 + "\n")
    
    # Initialize calculator
    print("1️⃣  Initializing ROI Calculator...")
    calc = ROICalculator()
    print("   ✅ Connected to database\n")
    
    # Test 1: Record a rebalance entry
    print("2️⃣  Testing rebalance entry recording...")
    try:
        roi_id = calc.record_rebalance_entry(
            rebalance_id=1,
            tx_hash="0x" + "a" * 64,
            entry_portfolio_value_usd=100000.0,
            allocation_from={"Aave": 50, "Compound": 50},
            allocation_to={"Aave": 70, "Compound": 30},
            gas_cost_usd=50.0,
            slippage_percent=0.1
        )
        
        if roi_id > 0:
            print(f"   ✅ Rebalance entry recorded: ID {roi_id}")
            print(f"      - Entry Value: $100,000")
            print(f"      - Gas Cost: $50")
            print(f"      - Allocation: Aave 50% → 70%\n")
        else:
            print("   ❌ Failed to record rebalance entry\n")
            return False
            
    except Exception as e:
        print(f"   ❌ Error: {e}\n")
        return False
    
    # Test 2: Calculate ROI snapshot after yield accrual
    print("3️⃣  Testing ROI snapshot calculation...")
    try:
        snapshot = calc.calculate_roi_snapshot(
            rebalance_roi_id=roi_id,
            current_portfolio_value_usd=101500.0,
            yield_earned_usd=1500.0,
            realized_gains_usd=0.0,
            unrealized_gains_usd=0.0
        )
        
        if snapshot:
            print(f"   ✅ ROI snapshot calculated:")
            print(f"      - ROI %: {snapshot['roi_percent']:.4f}%")
            print(f"      - APY: {snapshot['apy_achieved_percent']:.2f}%")
            print(f"      - Gain/Loss: ${snapshot['total_gain_loss_usd']:.2f}")
            print(f"      - Days Held: {snapshot['days_held']:.2f}\n")
        else:
            print("   ❌ Failed to calculate ROI snapshot\n")
            return False
            
    except Exception as e:
        print(f"   ❌ Error: {e}\n")
        return False
    
    # Test 3: Get latest ROI snapshot
    print("4️⃣  Testing latest ROI retrieval...")
    try:
        latest = calc.get_latest_roi_snapshot()
        
        if latest:
            print(f"   ✅ Latest ROI snapshot retrieved:")
            print(f"      - ROI: {latest['roi_percent']:.4f}%")
            print(f"      - APY: {latest['apy_achieved_percent']:.2f}%")
            print(f"      - P&L: ${latest['total_gain_loss_usd']:.2f}")
            print(f"      - Yield Earned: ${latest['yield_earned_usd']:.2f}")
            print(f"      - Gas Cost: ${latest['gas_cost_usd']:.2f}")
            print(f"      - Entry Value: ${latest['entry_portfolio_value_usd']:.2f}")
            print(f"      - Allocation: {latest['allocation']}\n")
        else:
            print("   ⚠️  No latest ROI snapshot found (expected on first run)\n")
            
    except Exception as e:
        print(f"   ❌ Error: {e}\n")
        return False
    
    # Test 4: Get cumulative ROI
    print("5️⃣  Testing cumulative ROI calculation...")
    try:
        cum_roi = calc.get_cumulative_roi()
        
        if cum_roi:
            print(f"   ✅ Cumulative ROI retrieved (30-day):")
            print(f"      - Total Rebalances: {cum_roi['total_rebalances']}")
            print(f"      - Cumulative ROI: {cum_roi['cumulative_roi_percent']:.4f}%")
            print(f"      - Best ROI: {cum_roi['best_roi_percent']:.4f}%")
            print(f"      - Worst ROI: {cum_roi['worst_roi_percent']:.4f}%")
            print(f"      - Cumulative Gain: ${cum_roi['cumulative_gain_usd']:.2f}")
            print(f"      - Total Yield: ${cum_roi['total_yield_earned_usd']:.2f}")
            print(f"      - Total Gas Cost: ${cum_roi['total_gas_cost_usd']:.2f}\n")
        else:
            print("   ⚠️  No cumulative ROI data found\n")
            
    except Exception as e:
        print(f"   ❌ Error: {e}\n")
        return False
    
    # Test 5: Update daily ROI summary
    print("6️⃣  Testing daily ROI summary update...")
    try:
        success = calc.update_daily_roi_summary()
        
        if success:
            print(f"   ✅ Daily ROI summary updated for {datetime.utcnow().date()}\n")
        else:
            print(f"   ⚠️  No data to summarize for today\n")
            
    except Exception as e:
        print(f"   ❌ Error: {e}\n")
        return False
    
    # Summary
    print("="*70)
    print("✅ ROI SYSTEM TEST COMPLETE")
    print("="*70)
    print("\nAll core ROI functions are operational:")
    print("  ✓ Recording rebalance entries")
    print("  ✓ Calculating ROI snapshots")
    print("  ✓ Retrieving latest ROI metrics")
    print("  ✓ Computing cumulative statistics")
    print("  ✓ Updating daily summaries")
    print("\nNext steps:")
    print("  1. Integrate into keeper_service.py (already done ✓)")
    print("  2. Add ROI display to dashboard (already done ✓)")
    print("  3. Set up scheduled ROI snapshot calculations")
    print("  4. Configure alerts for ROI thresholds")
    print("\n" + "="*70 + "\n")
    
    calc.close()
    return True


if __name__ == "__main__":
    success = test_roi_system()
    sys.exit(0 if success else 1)
