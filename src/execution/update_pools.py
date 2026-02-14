"""
Update Pool APYs from Live Data

Fetches current APYs from Aave and updates the StrategyManager contract.
"""

import json
import os
from web3 import Web3
from dotenv import load_dotenv
import requests

load_dotenv()

# Load deployment info
def load_deployment(network="sepolia"):
    with open(f"deployments/{network}_deployment.json", "r") as f:
        return json.load(f)

# Connect to network
deployment = load_deployment("sepolia")
w3 = Web3(Web3.HTTPProvider(os.getenv("SEPOLIA_RPC_URL")))
print(f"✓ Connected to Sepolia: {w3.is_connected()}")

# Load contract ABIs
with open("artifacts/contracts/strategies/StrategyManager.sol/StrategyManager.json") as f:
    strategy_manager_abi = json.load(f)["abi"]

with open("artifacts/contracts/adapters/AaveAdapter.sol/AaveAdapter.json") as f:
    aave_adapter_abi = json.load(f)["abi"]

# Contract instances
strategy_manager = w3.eth.contract(
    address=deployment["contracts"]["StrategyManager"],
    abi=strategy_manager_abi
)

aave_adapter = w3.eth.contract(
    address=deployment["contracts"]["AaveAdapter"],
    abi=aave_adapter_abi
)

# Account setup
account = w3.eth.account.from_key(os.getenv("DEPLOYER_PRIVATE_KEY"))
print(f"✓ Account: {account.address}")
print(f"✓ Balance: {w3.from_wei(w3.eth.get_balance(account.address), 'ether')} ETH\n")

# Testnet token addresses (from initialize script)
TOKENS = {
    "USDC": "0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8",
    "DAI": "0xFF34B3d4Aee8ddCd6F9AFFFB6Fe49bD371b8a357",
    "WETH": "0xC558DBdd856501FCd9aaF1E62eae57A9F0629a3c"
}

print("📊 Fetching current APYs from Aave...\n")

# Get APYs from contracts
pool_updates = []

for name, token_address in TOKENS.items():
    try:
        # Get APY from Aave adapter
        apy_ray = aave_adapter.functions.getCurrentAPY(token_address).call()
        
        # Convert from ray (1e27 = 100%) to basis points
        apy_bps = apy_ray // (10 ** 23)  # Convert ray to bps
        
        # Get pool ID
        pool_id = w3.solidity_keccak(
            ['address', 'address'],
            [token_address, deployment["contracts"]["AaveAdapter"]]
        )
        
        # Mock TVL and risk score for testnet
        tvl = 1_000_000 * 10**6  # $1M in USDC decimals
        risk_score = 30  # Low risk
        
        pool_updates.append({
            "name": name,
            "pool_id": pool_id,
            "token": token_address,
            "apy_bps": apy_bps,
            "apy_percent": float(apy_bps) / 100,
            "tvl": tvl,
            "risk_score": risk_score
        })
        
        print(f"✓ {name}: {float(apy_bps)/100:.2f}% APY")
        
    except Exception as e:
        print(f"✗ {name}: Error fetching APY - {e}")

if not pool_updates:
    print("\n❌ No pools to update")
    exit(1)

print(f"\n📤 Updating {len(pool_updates)} pools on-chain...\n")

# Batch update pools
try:
    pool_ids = [p["pool_id"] for p in pool_updates]
    apys = [p["apy_bps"] for p in pool_updates]
    tvls = [p["tvl"] for p in pool_updates]
    risk_scores = [p["risk_score"] for p in pool_updates]
    
    # Build transaction
    tx = strategy_manager.functions.batchUpdatePools(
        pool_ids,
        apys,
        tvls,
        risk_scores
    ).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 500000,
        'gasPrice': w3.eth.gas_price
    })
    
    # Sign and send
    signed_tx = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
    
    print(f"Transaction sent: {tx_hash.hex()}")
    print("Waiting for confirmation...")
    
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    if receipt['status'] == 1:
        print(f"✅ Transaction confirmed! Block: {receipt['blockNumber']}")
        print(f"   Gas used: {receipt['gasUsed']}")
        
        print("\n📊 Updated pool data:")
        for pool in pool_updates:
            print(f"\n{pool['name']}:")
            print(f"  APY: {pool['apy_percent']:.2f}%")
            print(f"  TVL: ${pool['tvl']:,}")
            print(f"  Risk: {pool['risk_score']}/100")
    else:
        print("❌ Transaction failed!")
        
except Exception as e:
    print(f"❌ Error updating pools: {e}")
    import traceback
    traceback.print_exc()

print("\n✨ Pool update complete!")
print("\n📋 Next step: Deposit testnet tokens to vault")
print("   python src/execution/deposit_testnet.py")
