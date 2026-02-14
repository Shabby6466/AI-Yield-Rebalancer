"""
Direct vault rebalance test
"""

import json
import os
import sys
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

load_dotenv()

def load_contract_abi(contract_name):
    """Load contract ABI from artifacts"""
    abi_path = f"artifacts/contracts/{contract_name}.sol/{contract_name.split('/')[-1]}.json"
    with open(abi_path, 'r') as f:
        artifact = json.load(f)
    return artifact['abi']

rpc_url = os.getenv('SEPOLIA_RPC_URL')
w3 = Web3(Web3.HTTPProvider(rpc_url))

private_key = os.getenv('DEPLOYER_PRIVATE_KEY')
account = Account.from_key(private_key)

with open('deployments/sepolia_deployment.json', 'r') as f:
    deployment = json.load(f)

vault_address = deployment['contracts']['YieldVault']
vault_abi = load_contract_abi('core/YieldVault')
vault = w3.eth.contract(address=vault_address, abi=vault_abi)

print(f"Testing direct vault.rebalance() call...")
print(f"Vault: {vault_address}")
print(f"Caller: {account.address}\n")

# Try to call rebalance directly
try:
    # First try to estimate gas to see the error
    gas_estimate = vault.functions.rebalance().estimate_gas({'from': account.address})
    print(f"✅ Gas estimate succeeded: {gas_estimate}")
    print("This means rebalance should work!")
    
except Exception as e:
    error_msg = str(e)
    print(f"❌ Gas estimation failed:")
    print(f"   {error_msg}\n")
    
    # Try to extract the revert reason
    if "execution reverted" in error_msg.lower():
        print("The transaction would revert. Common reasons:")
        print("1. No active pools with APY data")
        print("2. Vault has no funds to rebalance")
        print("3. Strategy manager not properly configured")
        print("4. Pool adapters not working")
        
    # Check vault state
    print("\n📊 Checking vault state...")
    try:
        tvl = vault.functions.totalValueLocked().call()
        print(f"   TVL: {tvl / 10**6:.4f} USDC")
        
        if tvl == 0:
            print("   ⚠️  TVL is 0 - nothing to rebalance!")
    except:
        pass
    
    # Check strategy manager
    strategy_manager_address = deployment['contracts']['StrategyManager']
    strategy_manager_abi = load_contract_abi('strategies/StrategyManager')
    strategy_manager = w3.eth.contract(address=strategy_manager_address, abi=strategy_manager_abi)
    
    print("\n📊 Checking strategy manager...")
    try:
        # Try to get pool count
        pool_count = strategy_manager.functions.poolCount().call()
        print(f"   Pool count: {pool_count}")
        
        if pool_count == 0:
            print("   ⚠️  No pools registered!")
    except Exception as e2:
        print(f"   Could not check pools: {e2}")

print("\n💡 Recommendation:")
print("The issue is likely that we need to update pool APY data first.")
print("Run: python3 src/execution/update_pools.py")
