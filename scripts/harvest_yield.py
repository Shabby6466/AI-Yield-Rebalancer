#!/usr/bin/env python3
"""
Harvest yield from Aave/Compound on the local Anvil fork.

How it works:
  1. Fast-forward Anvil time by N days (default: 30)
  2. Mine a block so interest accrues in aToken/cToken balances
  3. Read Hub's totalValue() before and after to compute yield earned
  4. Update StateStore with total_yield_earned and net_roi_pct

Usage:
  python scripts/harvest_yield.py           # simulate 30 days
  python scripts/harvest_yield.py --days 7  # simulate 7 days
"""
import os, sys, argparse, json, time
from pathlib import Path
from web3 import Web3
from dotenv import load_dotenv

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.core.state_store import StateStore

load_dotenv()

RPC = os.getenv("RPC_URL", "http://anvil:8545")
HUB = os.getenv("STRATEGY_HUB_ADDRESS")
KEY = os.getenv("KEEPER_PRIVATE_KEY")

HUB_ABI = [
    {"name": "getBalances", "type": "function", "inputs": [], "outputs": [
        {"name": "aaveBalance",     "type": "uint256"},
        {"name": "compoundBalance", "type": "uint256"},
        {"name": "idleBalance",     "type": "uint256"},
        {"name": "total",           "type": "uint256"},
    ]},
    {"name": "totalValue", "type": "function", "inputs": [], "outputs": [{"type": "uint256"}]},
]


def main():
    parser = argparse.ArgumentParser(description="Harvest yield on Anvil fork")
    parser.add_argument("--days", type=float, default=30.0, help="Days to fast-forward (default: 30)")
    args = parser.parse_args()

    w3 = Web3(Web3.HTTPProvider(RPC))
    if not w3.is_connected():
        print(f"❌ Cannot connect to RPC: {RPC}")
        sys.exit(1)

    hub = w3.eth.contract(address=Web3.to_checksum_address(HUB), abi=HUB_ABI)
    state = StateStore()

    # ── Snapshot BEFORE ──────────────────────────────────────────────────────
    balances_before = hub.functions.getBalances().call()
    total_before = balances_before[3]  # total in USDC (6 decimals)

    print(f"{'='*55}")
    print(f"  Yield Harvest Simulation  ({args.days:.0f} days)")
    print(f"{'='*55}")
    print(f"  RPC:    {RPC}")
    print(f"  Hub:    {HUB}")
    print(f"  Block:  {w3.eth.block_number}")
    print()
    print(f"  BEFORE:")
    print(f"    Aave:     ${balances_before[0]/1e6:>12,.4f}")
    print(f"    Compound: ${balances_before[1]/1e6:>12,.4f}")
    print(f"    Idle:     ${balances_before[2]/1e6:>12,.4f}")
    print(f"    Total:    ${total_before/1e6:>12,.4f}")
    print()

    # ── Fast-forward time ────────────────────────────────────────────────────
    seconds = int(args.days * 86400)
    print(f"  ⏩ Fast-forwarding {args.days:.0f} days ({seconds:,}s)...")

    # Increase EVM time
    w3.provider.make_request("evm_increaseTime", [seconds])
    # Mine 100 blocks so interest accrues (aTokens update per-block)
    w3.provider.make_request("anvil_mine", [100])

    print(f"  ✅ Now at block {w3.eth.block_number}")
    print()

    # ── Snapshot AFTER ───────────────────────────────────────────────────────
    balances_after = hub.functions.getBalances().call()
    total_after = balances_after[3]

    print(f"  AFTER:")
    print(f"    Aave:     ${balances_after[0]/1e6:>12,.4f}")
    print(f"    Compound: ${balances_after[1]/1e6:>12,.4f}")
    print(f"    Idle:     ${balances_after[2]/1e6:>12,.4f}")
    print(f"    Total:    ${total_after/1e6:>12,.4f}")
    print()

    # ── Compute yield ────────────────────────────────────────────────────────
    yield_earned_raw = max(0, total_after - total_before)
    yield_earned_usd = yield_earned_raw / 1e6

    # Load existing state for initial_capital
    current_state = state.load_state()
    initial_capital = current_state.get("initial_capital", 0.0)
    if initial_capital == 0.0:
        # Bootstrap: treat current total as initial capital if not set
        initial_capital = total_before / 1e6
        print(f"  ℹ️  initial_capital not set — bootstrapping to ${initial_capital:,.2f}")

    prev_yield = current_state.get("total_yield_earned", 0.0)
    new_total_yield = prev_yield + yield_earned_usd

    net_roi_pct = 0.0
    if initial_capital > 0:
        net_roi_pct = (new_total_yield / initial_capital) * 100.0

    apy_simulated = 0.0
    if initial_capital > 0 and args.days > 0:
        apy_simulated = (yield_earned_usd / initial_capital) * (365.0 / args.days) * 100.0

    print(f"  📈 YIELD RESULTS:")
    print(f"    Yield this harvest: ${yield_earned_usd:>10,.4f}")
    print(f"    Total yield earned: ${new_total_yield:>10,.4f}")
    print(f"    Initial capital:    ${initial_capital:>10,.2f}")
    print(f"    Net ROI:            {net_roi_pct:>9.4f}%")
    print(f"    Simulated APY:      {apy_simulated:>9.2f}%")
    print()

    # ── Persist to StateStore ────────────────────────────────────────────────
    state.update_state(
        initial_capital=initial_capital,
        total_yield_earned=round(new_total_yield, 6),
        net_roi_pct=round(net_roi_pct, 6),
        last_harvest_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        last_harvest_yield=round(yield_earned_usd, 6),
        current_total_value=round(total_after / 1e6, 6),
    )

    print(f"  ✅ State updated. Net ROI: {net_roi_pct:.4f}%")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
