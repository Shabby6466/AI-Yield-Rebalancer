"""
Add supported asset to YieldVault
Must be run by vault owner
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

def main():
    print("=" * 60)
    print("Adding Supported Asset to Vault")
    print("=" * 60)
    print()
    
    # Connect to Sepolia
    rpc_url = os.getenv('SEPOLIA_RPC_URL')
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    if not w3.is_connected():
        print("❌ Error: Could not connect to Sepolia")
        sys.exit(1)
    
    print(f"✅ Connected to Sepolia")
    
    # Load account
    private_key = os.getenv('DEPLOYER_PRIVATE_KEY')
    account = Account.from_key(private_key)
    print(f"📍 Using account: {account.address}")
    print()
    
    # Load deployment
    with open('deployments/sepolia_deployment.json', 'r') as f:
        deployment = json.load(f)
    
    vault_address = deployment['contracts']['YieldVault']
    usdc_address = '0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238'
    
    print(f"Vault: {vault_address}")
    print(f"USDC: {usdc_address}")
    print()
    
    # Load vault contract
    vault_abi = load_contract_abi('core/YieldVault')
    vault = w3.eth.contract(address=vault_address, abi=vault_abi)
    
    # Add supported asset
    print("🔄 Adding USDC as supported asset...")
    
    tx = vault.functions.addSupportedAsset(usdc_address).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 200000,
        'gasPrice': w3.eth.gas_price
    })
    
    # Sign and send
    signed_tx = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
    
    print(f"   Transaction hash: {tx_hash.hex()}")
    print(f"   Waiting for confirmation...")
    
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    if receipt['status'] == 1:
        print(f"✅ Asset added successfully!")
        print()
        print("=" * 60)
        print("✅ SETUP COMPLETE!")
        print("=" * 60)
        print()
        print("Now you can deposit USDC into the vault:")
        print("   python3 src/execution/deposit_testnet.py")
        print()
    else:
        print(f"❌ Transaction failed!")
        print(f"   Check: https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
        sys.exit(1)

if __name__ == '__main__':
    main()
