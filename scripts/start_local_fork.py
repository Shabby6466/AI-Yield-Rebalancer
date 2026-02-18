import os
import subprocess
import time
import requests
import sys
import json
from dotenv import load_dotenv

load_dotenv()

RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")

def is_node_running():
    try:
        response = requests.post(
            RPC_URL,
            json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
            timeout=1
        )
        return response.status_code == 200
    except:
        return False

def start_anvil():
    if is_node_running():
        print(f"Node already running on {RPC_URL}.")
        return
    
    # Try to find anvil in PATH
    import shutil
    anvil_path = shutil.which("anvil")
    if not anvil_path:
        # Fallback for Docker environment
        anvil_path = "/root/.foundry/bin/anvil"

    print(f"Starting Anvil fork of mainnet...")
    fork_url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}" if ALCHEMY_API_KEY else "https://eth.drpc.org"
    
    cmd = [
        anvil_path,
        "--fork-url", fork_url,
        "--port", "8545"
    ]
    
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Anvil started in background.")
    
    # Wait for node to be ready
    for _ in range(10):
        if is_node_running():
            print("Node is ready.")
            return
        time.sleep(1)
    print("Failed to start node.")

def deal_usdc(target_address, amount_usdc):
    """Grant USDC by 'stealing' from a mainnet whale (impersonation)"""
    print(f"💰 Stealing {amount_usdc} USDC for {target_address}...")
    
    WHALE = "0xA9D1e08C7793af67e9d92fe308d5697FB81d3E43" # Coinbase (USDC Whale)
    USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    
    import shutil
    cast_path = shutil.which("cast") or "/root/.foundry/bin/cast"
    
    # 1. Impersonate
    subprocess.run([cast_path, "rpc", "anvil_impersonateAccount", WHALE], stdout=subprocess.DEVNULL)
    
    # 2. Transfer
    amount_raw = amount_usdc * 10**6
    subprocess.run([
        cast_path, "send", USDC, 
        "transfer(address,uint256)", target_address, str(amount_raw),
        "--from", WHALE, "--rpc-url", RPC_URL, "--unlocked"
    ], stdout=subprocess.DEVNULL)
    
    # 3. Stop impersonating
    subprocess.run([cast_path, "rpc", "anvil_stopImpersonatingAccount", WHALE], stdout=subprocess.DEVNULL)
    
    print(f"✅ Stole {amount_usdc} USDC from the whale.")

def setup_roles(hub_addr, vault_addr, keeper_addr):
    """Setup roles between Hub, Vault and Keeper"""
    print("🔐 Setting up Roles...")
    import shutil
    cast_path = shutil.which("cast") or "/root/.foundry/bin/cast"
    PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    
    # Roles
    VAULT_ROLE = "0x8990a427618991206f6e0b7a810d728639556885568855688556885568855688" # Simplified, let's use keccak256("VAULT_ROLE")
    # Actually, let's just use cast call to get them or hardcode the real ones if known
    # VAULT_ROLE = keccak256("VAULT_ROLE")
    # KEEPER_ROLE = keccak256("KEEPER_ROLE")
    
    # We can use cast to compute the role or just hardcode common ones
    # But let's just use the StrategyHub to grant it.
    
    # Grant VAULT_ROLE to Vault on Hub
    # grantRole(bytes32 role, address account)
    # VAULT_ROLE = keccak256("VAULT_ROLE") = 0x98150e7a256d0d29d8920150d1804f32998ae8683e3902f2b3ec3d7b43a99180
    VAULT_ROLE_HASH = "0x98150e7a256d0d29d8920150d1804f32998ae8683e3902f2b3ec3d7b43a99180"
    KEEPER_ROLE_HASH = "0x9c193275726217596c561dd149234b6e5e95e1e0a8b98150e7a256d0d29d892" # placeholder
    # Let's derive them using cast to be safe
    try:
        KEEPER_ROLE_HASH = subprocess.run([cast_path, "keccak", "KEEPER_ROLE"], capture_output=True, text=True).stdout.strip()
        VAULT_ROLE_HASH = subprocess.run([cast_path, "keccak", "VAULT_ROLE"], capture_output=True, text=True).stdout.strip()
    except: pass

    # Grant VAULT_ROLE to Vault on Hub
    subprocess.run([cast_path, "send", hub_addr, "grantRole(bytes32,address)", VAULT_ROLE_HASH, vault_addr, "--private-key", PK, "--rpc-url", RPC_URL], stdout=subprocess.DEVNULL)
    
    # Grant KEEPER_ROLE to Keeper on Hub
    subprocess.run([cast_path, "send", hub_addr, "grantRole(bytes32,address)", KEEPER_ROLE_HASH, keeper_addr, "--private-key", PK, "--rpc-url", RPC_URL], stdout=subprocess.DEVNULL)
    
    # Grant KEEPER_ROLE to Keeper on Vault (Vault.sol uses same role name)
    subprocess.run([cast_path, "send", vault_addr, "grantRole(bytes32,address)", KEEPER_ROLE_HASH, keeper_addr, "--private-key", PK, "--rpc-url", RPC_URL], stdout=subprocess.DEVNULL)
    
    print("✅ Roles configured.")

def update_env(hub_addr, vault_addr):
    """Update .env file with new addresses"""
    print(f"📝 Updating .env...")
    
    # 1. Define all keys we want to ensure exist in .env
    required_keys = [
        "DATABASE_URL",
        "RPC_URL",
        "ETHEREUM_RPC_URL",
        "BASE_RPC_URL",
        "NETWORK",
        "ALCHEMY_API_KEY",
        "KEEPER_PRIVATE_KEY",
        "DEPLOYER_PRIVATE_KEY",
        "DUNE_API_KEY",
        "REBALANCE_INTERVAL",
        "VAULT_CONTRACT_ADDRESS",
        "STRATEGY_HUB_ADDRESS"
    ]
    
    # 2. Read existing .env (or create empty)
    env_map = {}
    try:
        with open(".env", "r") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    env_map[k] = v
    except FileNotFoundError:
        print("⚠️ .env file not found. Creating new one.")

    # 3. Update with new contract addresses
    env_map["VAULT_CONTRACT_ADDRESS"] = vault_addr
    env_map["STRATEGY_HUB_ADDRESS"] = hub_addr
    
    # 4. Fill in other missing keys from current process Environment (passed by Docker)
    # PRIORITIZE environment variables over what's in the file, to ensure sync with host
    for key in required_keys:
        val = os.getenv(key)
        if val:
            env_map[key] = val
        elif key not in env_map:
            # Set sensible defaults for local dev if missing AND not in env
            if key == "NETWORK": env_map[key] = "local"
            if key == "RPC_URL": env_map[key] = "http://anvil:8545"
            if key == "ETHEREUM_RPC_URL": env_map[key] = "http://anvil:8545"
            if key == "BASE_RPC_URL": env_map[key] = "http://anvil:8545"
    
    # 5. Write back to file
    try:
        with open(".env", "w") as f:
            for k, v in env_map.items():
                f.write(f"{k}={v}\n")
    except Exception as e:
        print(f"⚠️ Failed to update .env: {e}")

def deploy_contracts(keeper_addr):
    print("🚀 Deploying Smart Contracts via Forge Script...")
    PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    
    import shutil
    import re
    forge_path = shutil.which("forge") or "/root/.foundry/bin/forge"
    contracts_dir = os.path.abspath("contracts")

    # Run forge script
    cmd = [
        forge_path, "script", "scripts/Deploy.s.sol:Deploy",
        "--rpc-url", RPC_URL, "--broadcast", "--force", "--slow"
    ]
    
    env = os.environ.copy()
    env["PRIVATE_KEY"] = PK
    env["KEEPER_ADDRESS"] = keeper_addr
    
    res = subprocess.run(cmd, cwd=contracts_dir, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        print(f"❌ Deployment Script Failed: {res.stderr}")
        return None, None
    
    # Combined output
    full_output = res.stdout + res.stderr
    
    # In Deploy.s.sol: Hub is 1st, Vault is 2nd
    hub_match = re.search(r"StrategyHub.*? (0x[a-fA-F0-9]{40})", full_output, re.S | re.I)
    vault_match = re.search(r"YieldVault.*? (0x[a-fA-F0-9]{40})", full_output, re.S | re.I)
    
    hub_addr = hub_match.group(1) if hub_match else None
    vault_addr = vault_match.group(1) if vault_match else None
    
    if not hub_addr or not vault_addr:
        print(f"DEBUG FULL OUTPUT: {full_output}")
        return None, None
    
    print(f"✅ StrategyHub: {hub_addr}")
    print(f"✅ YieldVault:   {vault_addr}")
    
    # Save deployments
    deployment_data = {
        "StrategyHub": {"address": hub_addr, "abi": "contracts/out/StrategyHub.sol/StrategyHub.json"},
        "YieldVault": {"address": vault_addr, "abi": "contracts/out/Vault.sol/YieldVault.json"},
        "StrategyManager": {"address": hub_addr, "abi": "contracts/out/StrategyHub.sol/StrategyHub.json"}
    }
    
    os.makedirs("deployments", exist_ok=True)
    with open("deployments/local.json", "w") as f:
        json.dump(deployment_data, f, indent=2)
    
    # Also write to simple text file for RebalancerService local pick-up
    # Do this BEFORE update_env to ensure it happens even if .env fails
    with open("contracts/deployed_address.txt", "w") as f:
        f.write(hub_addr)
        
    update_env(hub_addr, vault_addr)
        
    return hub_addr, vault_addr

def fund_keeper():
    """Derive keeper address and fund it"""
    deployer_pk = os.getenv("KEEPER_PRIVATE_KEY")
    if not deployer_pk:
        print("⚠️ No KEEPER_PRIVATE_KEY")
        return None
    
    import shutil
    cast_path = shutil.which("cast") or "/root/.foundry/bin/cast"
    
    # Derive address
    res = subprocess.run([cast_path, "wallet", "address", "--private-key", deployer_pk], capture_output=True, text=True)
    keeper_addr = res.stdout.strip()
    
    # 1. ETH (100)
    print("   Funding Keeper with ETH...")
    whale_pk = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    subprocess.run([cast_path, "send", keeper_addr, "--value", "100ether", "--private-key", whale_pk, "--rpc-url", RPC_URL], stdout=subprocess.DEVNULL)
    
    # 2. USDC (100k)
    deal_usdc(keeper_addr, 100_000)
    
    return keeper_addr

if __name__ == "__main__":
    start_anvil()
    keeper = fund_keeper()
    if keeper:
        hub, vault = deploy_contracts(keeper)
        if hub and vault:
            print("\n✨ FORK READY FOR AI REBALANCER ✨")
            print(f"   Hub:   {hub}")
            print(f"   Vault: {vault}")
            print(f"   Bot:   {keeper}")
        else:
            print("❌ Contract setup failed.")
            sys.exit(1)
    else:
        print("❌ Funding failed.")
        sys.exit(1)
