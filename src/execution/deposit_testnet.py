"""
Testnet Deposit Script
Deposits tokens into YieldVault on Sepolia testnet
"""

import json
import os
import sys
from web3 import Web3
from eth_account import Account
from decimal import Decimal

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

def load_contract_abi(contract_name):
    """Load contract ABI from artifacts"""
    abi_path = f"artifacts/contracts/{contract_name}.sol/{contract_name.split('/')[-1]}.json"
    with open(abi_path, 'r') as f:
        artifact = json.load(f)
    return artifact['abi']

def load_deployment(network="base_sepolia"):
    """Load deployment addresses"""
    with open(f'deployments/{network}_deployment.json', 'r') as f:
        return json.load(f)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Deposit tokens into YieldVault')
    parser.add_argument('--network', default='base_sepolia', help='Network (sepolia or base_sepolia)')
    parser.add_argument('--asset', required=True, help='Asset symbol (WETH, USDC, etc.)')
    parser.add_argument('--amount', type=float, required=True, help='Amount to deposit')
    args = parser.parse_args()
    
    print("=" * 60)
    print("🏦 TESTNET VAULT DEPOSIT")
    print("=" * 60)
    print()
    
    # Configuration
    network = args.network
    token_symbol = args.asset
    deposit_amount = args.amount
    
    # Connect to network
    rpc_env_var = 'BASE_SEPOLIA_RPC_URL' if network == 'base_sepolia' else 'SEPOLIA_RPC_URL'
    rpc_url = os.getenv(rpc_env_var)
    if not rpc_url:
        print(f"❌ Error: {rpc_env_var} not found in .env")
        sys.exit(1)
    
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    if not w3.is_connected():
        print("❌ Error: Could not connect to Sepolia")
        sys.exit(1)
    
    print(f"✅ Connected to Sepolia")
    print(f"   Chain ID: {w3.eth.chain_id}")
    print()
    
    # Load private key
    private_key = os.getenv('DEPLOYER_PRIVATE_KEY')
    if not private_key:
        print("❌ Error: DEPLOYER_PRIVATE_KEY not found in .env")
        sys.exit(1)
    
    account = Account.from_key(private_key)
    print(f"📍 Using account: {account.address}")
    
    # Check ETH balance
    eth_balance = w3.eth.get_balance(account.address)
    print(f"   ETH Balance: {w3.from_wei(eth_balance, 'ether'):.4f} ETH")
    print()
    
    # Load deployment info
    deployment = load_deployment(network)
    vault_address = deployment['contracts']['YieldVault']
    
    # Token addresses
    if network == 'base_sepolia':
        token_addresses = {
            'WETH': '0x4200000000000000000000000000000000000006',
            'USDC': '0x036CbD53842c5426634e7929541eC2318f3dCF7e',
        }
    else:  # sepolia
        token_addresses = {
            'USDC': '0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238',
            'DAI': '0xFF34B3d4Aee8ddCd6F9AFFFB6Fe49bD371b8a357',
            'WETH': '0xC558DBdd856501FCd9aaF1E62eae57A9F0629a3c'
        }
    
    token_decimals = {
        'USDC': 6,
        'DAI': 18,
        'WETH': 18
    }
    
    if token_symbol not in token_addresses:
        print(f"❌ Error: Unknown token {token_symbol}")
        sys.exit(1)
    
    token_address = token_addresses[token_symbol]
    decimals = token_decimals[token_symbol]
    amount_wei = int(deposit_amount * (10 ** decimals))
    
    print(f"🪙 Token: {token_symbol}")
    print(f"   Address: {token_address}")
    print(f"   Amount: {deposit_amount} {token_symbol}")
    print()
    
    # Load contracts
    vault_abi = load_contract_abi('core/YieldVault')
    token_abi = [
        {
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function"
        },
        {
            "constant": False,
            "inputs": [
                {"name": "_spender", "type": "address"},
                {"name": "_value", "type": "uint256"}
            ],
            "name": "approve",
            "outputs": [{"name": "", "type": "bool"}],
            "type": "function"
        },
        {
            "constant": True,
            "inputs": [
                {"name": "_owner", "type": "address"},
                {"name": "_spender", "type": "address"}
            ],
            "name": "allowance",
            "outputs": [{"name": "", "type": "uint256"}],
            "type": "function"
        }
    ]
    
    vault = w3.eth.contract(address=vault_address, abi=vault_abi)
    token = w3.eth.contract(address=token_address, abi=token_abi)
    
    # Check token balance
    print("=" * 60)
    print("STEP 1: Check Token Balance")
    print("=" * 60)
    
    token_balance = token.functions.balanceOf(account.address).call()
    token_balance_formatted = token_balance / (10 ** decimals)
    
    print(f"Your {token_symbol} balance: {token_balance_formatted:.4f} {token_symbol}")
    
    if token_balance < amount_wei:
        print(f"\n❌ Error: Insufficient {token_symbol} balance")
        print(f"   Required: {deposit_amount} {token_symbol}")
        print(f"   Available: {token_balance_formatted:.4f} {token_symbol}")
        print(f"\n💡 Get testnet {token_symbol} from:")
        print(f"   https://staging.aave.com/faucet/")
        sys.exit(1)
    
    print(f"✅ Sufficient balance!")
    print()
    
    # Check current allowance
    print("=" * 60)
    print("STEP 2: Approve Token Spending")
    print("=" * 60)
    
    current_allowance = token.functions.allowance(account.address, vault_address).call()
    print(f"Current allowance: {current_allowance / (10 ** decimals):.4f} {token_symbol}")
    
    if current_allowance < amount_wei:
        print(f"Approving {deposit_amount} {token_symbol} for vault...")
        
        # Build approval transaction
        approve_tx = token.functions.approve(
            vault_address,
            amount_wei
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 100000,
            'gasPrice': w3.eth.gas_price
        })
        
        # Sign and send
        signed_approve = account.sign_transaction(approve_tx)
        approve_hash = w3.eth.send_raw_transaction(signed_approve.rawTransaction)
        
        print(f"   Transaction hash: {approve_hash.hex()}")
        print(f"   Waiting for confirmation...")
        
        approve_receipt = w3.eth.wait_for_transaction_receipt(approve_hash)
        
        if approve_receipt['status'] == 1:
            print(f"✅ Approval confirmed!")
        else:
            print(f"❌ Approval failed!")
            sys.exit(1)
    else:
        print(f"✅ Already approved!")
    
    print()
    
    # Check vault shares before deposit
    print("=" * 60)
    print("STEP 3: Deposit into Vault")
    print("=" * 60)
    
    # Note: Vault is ERC-4626 compliant but we'll check shares after deposit
    print(f"\nDepositing {deposit_amount} {token_symbol} into vault...")
    
    # Get gas price with buffer and pending nonce
    gas_price = w3.eth.gas_price
    gas_price_buffered = int(gas_price * 1.2)  # 20% buffer
    nonce = w3.eth.get_transaction_count(account.address, 'pending')  # Use pending nonce
    
    deposit_tx = vault.functions.deposit(
        token_address,
        amount_wei
    ).build_transaction({
        'from': account.address,
        'nonce': nonce,
        'gas': 500000,
        'gasPrice': gas_price_buffered
    })
    
    # Sign and send
    signed_deposit = account.sign_transaction(deposit_tx)
    deposit_hash = w3.eth.send_raw_transaction(signed_deposit.rawTransaction)
    
    print(f"   Transaction hash: {deposit_hash.hex()}")
    print(f"   Etherscan: https://sepolia.etherscan.io/tx/{deposit_hash.hex()}")
    print(f"   Waiting for confirmation...")
    
    deposit_receipt = w3.eth.wait_for_transaction_receipt(deposit_hash)
    
    if deposit_receipt['status'] != 1:
        print(f"❌ Deposit failed!")
        print(f"   Gas used: {deposit_receipt['gasUsed']}")
        sys.exit(1)
    
    print(f"✅ Deposit confirmed!")
    print(f"   Block: {deposit_receipt['blockNumber']}")
    print(f"   Gas used: {deposit_receipt['gasUsed']}")
    
    gas_cost_eth = deposit_receipt['gasUsed'] * w3.eth.gas_price / 10**18
    print(f"   Gas cost: {gas_cost_eth:.6f} ETH")
    print()
    
    # Check vault shares after deposit
    print("=" * 60)
    print("STEP 4: Verify Deposit")
    print("=" * 60)
    
    # Get user deposits from vault
    try:
        user_deposit = vault.functions.userDeposits(account.address, token_address).call()
        print(f"Your deposit in vault: {user_deposit / (10 ** decimals):.4f} {token_symbol}")
    except Exception as e:
        print(f"Note: Could not read userDeposits (may need different method)")
    
    # Check TVL
    try:
        tvl = vault.functions.totalValueLocked().call()
        print(f"Total vault TVL: {tvl / (10 ** decimals):.4f} USDC")
    except:
        pass
    
    # Check token balance after
    token_balance_after = token.functions.balanceOf(account.address).call()
    token_balance_after_formatted = token_balance_after / (10 ** decimals)
    
    print(f"\n{token_symbol} balance after: {token_balance_after_formatted:.4f} {token_symbol}")
    print(f"Amount deposited: {(token_balance - token_balance_after) / (10 ** decimals):.4f} {token_symbol}")
    
    print()
    print("=" * 60)
    print("✅ DEPOSIT SUCCESSFUL!")
    print("=" * 60)
    print()
    print(f"📊 Summary:")
    print(f"   Deposited: {deposit_amount} {token_symbol}")
    print(f"   Transaction: https://sepolia.etherscan.io/tx/{deposit_hash.hex()}")
    print(f"   Vault: https://sepolia.etherscan.io/address/{vault_address}")
    print()
    print("💡 Next steps:")
    print("   1. View your position on the dashboard")
    print("   2. Test withdrawal: python3 src/execution/withdraw_testnet.py")
    print("   3. Test rebalancing: python3 src/execution/rebalance_testnet.py")
    print()

if __name__ == '__main__':
    main()
