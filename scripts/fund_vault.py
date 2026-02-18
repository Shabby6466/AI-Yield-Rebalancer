import os
import subprocess
import sys
from dotenv import load_dotenv

# Load .env
load_dotenv()

# Constants
RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
KEEPER_PK = os.getenv("KEEPER_PRIVATE_KEY")
VAULT_ADDR = os.getenv("VAULT_CONTRACT_ADDRESS")
USDC_ADDR = os.getenv("USDC_ADDRESS", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")

def get_cast_path():
    import shutil
    cast = shutil.which("cast")
    if not cast:
        cast = "/root/.foundry/bin/cast"  # Docker default
    return cast

def run_cast(args):
    cast = get_cast_path()
    cmd = [cast] + args + ["--rpc-url", RPC_URL, "--private-key", KEEPER_PK]
    
    print(f"Running: {' '.join(cmd)}")  # Debug (careful with PK not printed)
    # Mask PK in print
    cmd_print = list(cmd)
    if "--private-key" in cmd_print:
        idx = cmd_print.index("--private-key")
        cmd_print[idx+1] = "****"
    print(f"Running: {' '.join(cmd_print)}")

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"❌ Error: {res.stderr}")
        return False
    print(f"✅ Success: {res.stdout.strip()}")
    return True

def main():
    if not KEEPER_PK or not VAULT_ADDR:
        print("❌ Missing environment variables (KEEPER_PRIVATE_KEY or VAULT_CONTRACT_ADDRESS)")
        # Try to load from deployed_address.txt if env var missing
        try:
            with open("contracts/deployed_address.txt", "r") as f:
                # Vault address isn't directly in here in simple format, it's just one addr?
                # Ah, deployed_address.txt only has Hub usually.
                # Let's rely on .env which we just fixed.
                pass 
        except: pass
        sys.exit(1)

    print(f"💰 Funding Vault {VAULT_ADDR} from Keeper...")
    
    # 1. Approve Vault to spend USDC
    # approve(address spender, uint256 amount)
    print("\n1️⃣  Approving Vault to spend USDC...")
    amount = 50000 * 10**6  # 50k USDC
    run_cast(["send", USDC_ADDR, "approve(address,uint256)", VAULT_ADDR, str(amount)])
    
    # 2. Deposit into Vault
    # deposit(uint256 assets, address receiver)
    print("\n2️⃣  Depositing 50k USDC into Vault...")
    # Receiver is Keeper itself for the shares
    keeper_addr = subprocess.run([get_cast_path(), "wallet", "address", "--private-key", KEEPER_PK], capture_output=True, text=True).stdout.strip()
    
    run_cast(["send", VAULT_ADDR, "deposit(uint256,address)", str(amount), keeper_addr])
    
    print("\n✨ valid deposit complete! The rebalancer should verify this shortly.")

if __name__ == "__main__":
    main()
