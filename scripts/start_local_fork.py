import os
import subprocess
import time
import requests
import sys
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

    cmd = [
        forge_path, "create",
        "src/StrategyHub.sol:StrategyHub",
        "--rpc-url", RPC_URL,
        "--private-key", PK,
        "--constructor-args", USDC, AAVE_POOL, AUSDC, COMPOUND_COMET,
        "--remappings", "@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/",
        "--remappings", "forge-std/=lib/forge-std/src/"
    ]
    
    result = subprocess.run(cmd, cwd="contracts", capture_output=True, text=True)
    if result.returncode == 0:
        # Extract address from output
        for line in result.stdout.split("\n"):
            if "Deployed to:" in line:
                addr = line.split("Deployed to:")[1].strip()
                print(f"StrategyHub deployed to: {addr}")
                # Save to a local file for the dashboard to read
                with open("contracts/deployed_address.txt", "w") as f:
                    f.write(addr)
                return addr
    else:
        print(f"Deployment failed: {result.stderr}")
        return None

if __name__ == "__main__":
    start_anvil()
    deploy_contracts()
