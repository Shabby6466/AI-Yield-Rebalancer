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
    return str(cast)

def run_cast(args):
    cast = get_cast_path()
    # Ensure all args are strings
    cmd_args = [str(arg) for arg in args]
    cmd = [cast] + cmd_args + ["--rpc-url", str(RPC_URL), "--private-key", str(KEEPER_PK)]
    
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

def check_and_fund(keeper_addr, min_needed=50000):
    cast = get_cast_path()
    
    # 0. Get current balance
    # cast call $USDC "balanceOf(address)(uint256)" $KEEPER
    cmd = [cast, "call", USDC_ADDR, "balanceOf(address)(uint256)", keeper_addr, "--rpc-url", RPC_URL]
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    current_bal = 0
    if res.returncode == 0:
        try:
            current_bal = int(res.stdout.strip())
        except ValueError:
            current_bal = 0
            
    print(f"📊 Current Keeper Balance: {current_bal / 10**6:,.2f} USDC")
    
    needed_wei = min_needed * 10**6
    if current_bal >= needed_wei:
        print("✅ Balance sufficient.")
        return

    print("⚠️ Balance insufficient. Stealing from whale...")
    
    WHALE = "0xA9D1e08C7793af67e9d92fe308d5697FB81d3E43" # Coinbase (USDC Whale)
    amount_to_steal = needed_wei * 2 # Steal double ensuring buffer
    
    # 1. Impersonate
    subprocess.run([cast, "rpc", "anvil_impersonateAccount", WHALE, "--rpc-url", RPC_URL], stdout=subprocess.DEVNULL)
    
    # 2. Transfer
    subprocess.run([
        cast, "send", USDC_ADDR, 
        "transfer(address,uint256)", keeper_addr, str(amount_to_steal),
        "--from", WHALE, "--rpc-url", RPC_URL, "--unlocked"
    ], stdout=subprocess.DEVNULL)
    
    # 3. Stop impersonating
    subprocess.run([cast, "rpc", "anvil_stopImpersonatingAccount", WHALE, "--rpc-url", RPC_URL], stdout=subprocess.DEVNULL)
    
    print(f"✅ Stole {amount_to_steal / 10**6:,.2f} USDC from the whale.")


    
    if not KEEPER_PK:
        print("❌ Missing KEEPER_PRIVATE_KEY")
        sys.exit(1)

    if not VAULT_ADDR:
        print("❌ Missing VAULT_CONTRACT_ADDRESS")
        # Try to find it in .env manually if load_dotenv failed?
        if os.path.exists(".env"):
             print("🔎 Checking .env file content...")
             with open(".env") as f:
                 for line in f:
                     if "VAULT_CONTRACT_ADDRESS" in line:
                         print(f"   found: {line.strip()}")
        sys.exit(1)

    cast = get_cast_path()
    keeper_addr = subprocess.run([cast, "wallet", "address", "--private-key", KEEPER_PK], capture_output=True, text=True).stdout.strip()
    
    print(f"💰 Funding Vault: {VAULT_ADDR}")
    print(f"   From Keeper:   {keeper_addr}")
    print(f"   RPC URL:       {RPC_URL}")

    
    # 0. Check & Fund
    check_and_fund(keeper_addr)
    
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
