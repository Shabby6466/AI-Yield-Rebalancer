"""
Withdraw all funds from vault before redeployment
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

print("=" * 60)
print("💸 WITHDRAW ALL FUNDS FROM VAULT")
print("=" * 60)
print()
print(f"Vault: {vault_address}")
print(f"Account: {account.address}")
print()

# Check shares
user_shares = vault.functions.shares(account.address).call()
print(f"Your shares: {user_shares / 10**6:.4f}")

if user_shares == 0:
    print("✅ No shares to withdraw - vault is empty for you")
    sys.exit(0)

# Check USDC balance before
usdc_address = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"
usdc_abi = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    }
]
usdc = w3.eth.contract(address=usdc_address, abi=usdc_abi)
usdc_before = usdc.functions.balanceOf(account.address).call()

print(f"USDC balance before: {usdc_before / 10**6:.4f} USDC")
print()
print(f"🔄 Withdrawing ALL {user_shares / 10**6:.4f} shares...")

# Withdraw all shares
tx = vault.functions.withdraw(user_shares).build_transaction({
    'from': account.address,
    'nonce': w3.eth.get_transaction_count(account.address),
    'gas': 500000,
    'gasPrice': w3.eth.gas_price
})

signed_tx = account.sign_transaction(tx)
tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)

print(f"   Transaction: {tx_hash.hex()}")
print(f"   Waiting for confirmation...")

try:
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    
    if receipt['status'] == 1:
        print(f"✅ Withdrawal successful!")
        print(f"   Gas used: {receipt['gasUsed']}")
        
        # Check balance after
        import time
        time.sleep(2)
        
        usdc_after = usdc.functions.balanceOf(account.address).call()
        usdc_received = usdc_after - usdc_before
        
        print()
        print(f"📊 Results:")
        print(f"   USDC withdrawn: {usdc_received / 10**6:.4f} USDC")
        print(f"   New USDC balance: {usdc_after / 10**6:.4f} USDC")
        print()
        print("✅ Ready to redeploy contracts with 5-minute rebalance frequency!")
        
    else:
        print(f"❌ Withdrawal failed")
        print(f"   Check: https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
        sys.exit(1)
        
except Exception as e:
    print(f"⏳ Transaction may still be pending")
    print(f"   Check: https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
