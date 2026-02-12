import sys
import os
import time
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.execution.sim_control import SimController
from web3 import Web3

def run_simulation_demo():
    sim = SimController()
    if not sim.is_connected():
        print("Anvil not running. Start it first.")
        return

    # Load Hub Address
    with open("contracts/deployed_address.txt", "r") as f:
        hub_addr = f.read().strip()
    
    hub = sim.get_hub_contract(hub_addr)
    
    print(f"--- 1. Creating Base Snapshot (Balance: $1M) ---")
    base_snapshot = sim.create_snapshot()
    bals = hub.functions.getBalances().call()
    print(f"Initial Total Value: ${bals[3]/1e6:,.2f}")

    print(f"\n--- 2. Scenario A: 30-Day Yield Wait ---")
    sim.jump_forward(30 * 24 * 3600)
    bals_30d = hub.functions.getBalances().call()
    print(f"Total Value after 30 days: ${bals_30d[3]/1e6:,.2f}")
    print(f"Yield: ${ (bals_30d[3] - bals[3])/1e6:,.2f}")

    print(f"\n--- 3. Reverting to Base Snapshot ---")
    sim.revert_to_snapshot(base_snapshot)
    bals_revert = hub.functions.getBalances().call()
    print(f"Total Value after revert: ${bals_revert[3]/1e6:,.2f}")

    print(f"\n--- 4. Scenario B: 1-Year (Extreme Long) Wait ---")
    # Refresh snapshot because revert consumes it
    sim.create_snapshot() 
    sim.jump_forward(365 * 24 * 3600)
    bals_1y = hub.functions.getBalances().call()
    print(f"Total Value after 1 year: ${bals_1y[3]/1e6:,.2f}")
    print(f"Yield: ${ (bals_1y[3] - bals[3])/1e6:,.2f}")

    print(f"\nSimulation demo complete.")

if __name__ == "__main__":
    run_simulation_demo()
