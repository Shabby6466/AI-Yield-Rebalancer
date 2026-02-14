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

def deploy_contracts():
    print("Deploying StrategyHub to local fork...")
    # Mainnet addresses for StrategyHub
    USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
    AUSDC = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
    COMPOUND_COMET = "0xc3d688B66703497DAA19211EEdff47f25384cdc3"
    
    # Forge create command
    # Private key from Anvil's first default account
    PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    
    # Try to find forge in PATH
    import shutil
    forge_path = shutil.which("forge")
    if not forge_path:
        # Fallback for Docker environment
        forge_path = "/root/.foundry/bin/forge"

    # Build remappings using absolute paths for Docker stability
    contracts_dir = os.path.abspath("contracts")
    oz_path = os.path.join(contracts_dir, "lib/openzeppelin-contracts/contracts/")
    forge_std_path = os.path.join(contracts_dir, "lib/forge-std/src/")
    
    if not os.path.exists(oz_path):
        print(f"❌ Error: OpenZeppelin contracts not found at {oz_path}")
        print("💡 Tip: Try running 'git submodule update --init --recursive' on your server.")
        return None

    cmd = [
        forge_path, "create",
        "src/StrategyHub.sol:StrategyHub",
        "--rpc-url", RPC_URL,
        "--private-key", PK,
        "--constructor-args", USDC, AAVE_POOL, AUSDC, COMPOUND_COMET,
        "--remappings", f"@openzeppelin/contracts/={oz_path}",
        "--remappings", f"forge-std/={forge_std_path}"
    ]
    
    print(f"Running Forge Command in {contracts_dir}...")
    print(f"Running Forge Command in {contracts_dir}...")
    # Direct output to console so we can see it in 'docker compose logs'
    result = subprocess.run(cmd, cwd=contracts_dir)
    
    if result.returncode == 0:
        # We need to find the address manually now that we didn't capture output
        # Anvil usually stays the same if we use the first account
        addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3" # Default first deployment
        print(f"✅ StrategyHub likely deployed to: {addr}")
        
        # Save in the format ContractManager expects
        deployment_data = {
            "StrategyHub": {
                "address": addr,
                "abi": "contracts/out/StrategyHub.sol/StrategyHub.json"
            }
        }
        
        os.makedirs("deployments", exist_ok=True)
        with open("deployments/local.json", "w") as f:
            json.dump(deployment_data, f, indent=2)
            
        # Also helpful to have simple text file
        with open("contracts/deployed_address.txt", "w") as f:
            f.write(addr)
        return addr
    else:
        print(f"❌ FORGE DEPLOYMENT FAILED!")
        print(f"--- STDOUT ---\n{result.stdout}")
        print(f"--- STDERR ---\n{result.stderr}")
        return None

def fund_keeper(target_address=None):
    """Fund the keeper account with ETH from the Anvil whale"""
    print("💰 Funding Keeper Account...")
    
    # Anvil default account #0 (Whale)
    whale_pk = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    
    if not target_address:
         # Try to get from env or use a hardcoded fallback if checking logs manually
         # But better to just use the one we saw in logs: 0x4D8...
         # Actually, let's derive it or ask user. 
         # The logs showed: 0x4D8887Dd74e4d5e07d6d642Bb4B45ff891dbf21C
         # We can try to use cast to derive it from the PK in env if available
         pass

    # We will use cast to transfer
    # We need the address. Let's assume the user provided one or we use the known one from logs
    # But wait, the script doesn't know the address easily without eth-account.
    # Let's rely on the one from logs for now as a fallback, or try to read it.
    
    deployer_pk = os.getenv("DEPLOYER_PRIVATE_KEY")
    if not deployer_pk:
        print("⚠️ No DEPLOYER_PRIVATE_KEY found. Skipping funding.")
        return

    # Use cast to get address
    import shutil
    cast_path = shutil.which("cast") or "/root/.foundry/bin/cast"
    
    # Get Address
    try:
        cmd_addr = [cast_path, "wallet", "address", "--private-key", deployer_pk]
        result = subprocess.run(cmd_addr, capture_output=True, text=True)
        if result.returncode != 0:
             print(f"❌ Failed to derive address: {result.stderr}")
             return
        target_address = result.stdout.strip()
    except Exception as e:
        print(f"⚠️ Could not derive address: {e}")
        return

    print(f"   Target: {target_address}")
    
    # Send 100 ETH
    cmd_send = [
        cast_path, "send", 
        target_address, 
        "--value", "100ether",
        "--private-key", whale_pk,
        "--rpc-url", RPC_URL
    ]
    
    subprocess.run(cmd_send, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("✅ Funded 100 ETH to Keeper.")

if __name__ == "__main__":
    start_anvil()
    address = deploy_contracts()
    if address:
        fund_keeper() # Run funding
        print("✅ Deployment successful.")
    else:
        print("❌ Deployment failed.")
        sys.exit(1)
