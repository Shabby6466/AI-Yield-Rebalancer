import os
import subprocess
import time
import requests
import sys
from dotenv import load_dotenv

# Path to the base directory
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(BASE_DIR)

from scripts.start_local_fork import start_anvil, deploy_contracts

def kill_anvil():
    """Kills any running anvil process"""
    print("Stopping existing Anvil fork...")
    try:
        # On macOS/Linux
        subprocess.run(["pkill", "-f", "anvil"], check=False)
        time.sleep(2)
        print("Anvil stopped.")
    except Exception as e:
        print(f"Error stopping Anvil: {e}")

def refresh():
    """Refreshes the fork to the latest Mainnet block"""
    print("\n" + "="*50)
    print("🔄 REFRESHING LOCAL MAINNET FORK")
    print("="*50)
    
    # 1. Kill old process
    kill_anvil()
    
    # 2. Start fresh fork (this automatically grabs the latest head)
    start_anvil()
    
    # 3. Redeploy strategy (contracts need to be on the new fork)
    addr = deploy_contracts()
    
    if addr:
        print("\n✅ Fork successfully refreshed to the latest Mainnet block.")
        print(f"📍 New StrategyHub deployed at: {addr}")
    else:
        print("\n❌ Refresh failed during contract deployment.")

if __name__ == "__main__":
    refresh()
