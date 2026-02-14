"""
Testnet Withdrawal Script
Withdraws tokens from YieldVault on Sepolia testnet
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

def load_deployment():
    """Load deployment addresses"""
    with open('deployments/sepolia_deployment.json', 'r') as f:
        return json.load(f)

def main():
    print("=" * 60)
    print("💸 TESTNET VAULT WITHDRAWAL")
    print("=" * 60)
    print()
    
    # Configuration
    withdraw_percentage = 50  # Withdraw 50% of shares (change as needed)
    
    # Connect to Sepolia
    rpc_url = os.getenv('SEPOLIA_RPC_URL')
    if not rpc_url:
        print("❌ Error: SEPOLIA_RPC_URL not found in .env")
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
    deployment = load_deployment()
    vault_address = deployment['contracts']['YieldVault']
    
    # Token addresses
    token_addresses = {
        'USDC': '0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238',
    }
    
    token_decimals = {
        'USDC': 6,
    }
    
    print(f"🏦 Vault: {vault_address}")
    print()
    
    # Load vault contract
    vault_abi = load_contract_abi('core/YieldVault')
    vault = w3.eth.contract(address=vault_address, abi=vault_abi)
    
    # Check user's shares
    print("=" * 60)
    print("STEP 1: Check Your Vault Position")
    print("=" * 60)
    
    try:
        user_shares = vault.functions.shares(account.address).call()
        print(f"Your vault shares: {user_shares / 10**6:.4f}")
        
        if user_shares == 0:
            print("\n❌ Error: You have no shares in the vault")
            print("   Deposit first: python3 src/execution/deposit_testnet.py")
            sys.exit(1)
        
    except Exception as e:
        print(f"❌ Error checking shares: {e}")
        sys.exit(1)
    
    # Check vault TVL and deposits
    try:
        tvl = vault.functions.totalValueLocked().call()
        print(f"Total vault TVL: {tvl / 10**6:.4f} USDC")
        
        total_shares = vault.functions.totalShares().call()
        print(f"Total vault shares: {total_shares / 10**6:.4f}")
        
        # Check user deposit for USDC
        usdc_address = token_addresses['USDC']
        user_deposit = vault.functions.userDeposits(account.address, usdc_address).call()
        print(f"Your USDC deposit: {user_deposit / 10**6:.4f} USDC")
        
    except Exception as e:
        print(f"Note: Could not read all vault data: {e}")
    
    print()
    
    # Calculate shares to withdraw
    shares_to_withdraw = (user_shares * withdraw_percentage) // 100
    
    print("=" * 60)
    print("STEP 2: Withdraw from Vault")
    print("=" * 60)
    print(f"Withdrawing: {withdraw_percentage}% of shares")
    print(f"Shares to burn: {shares_to_withdraw / 10**6:.4f}")
    print()
    
    # Check token balances before withdrawal
    usdc_address = token_addresses['USDC']
    token_abi = [
        {
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function"
        }
    ]
    
    usdc = w3.eth.contract(address=usdc_address, abi=token_abi)
    usdc_balance_before = usdc.functions.balanceOf(account.address).call()
    
    print(f"USDC balance before withdrawal: {usdc_balance_before / 10**6:.4f} USDC")
    print()
    
    # Build withdrawal transaction
    print("🔄 Processing withdrawal...")
    
    withdraw_tx = vault.functions.withdraw(
        shares_to_withdraw
    ).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 500000,
        'gasPrice': w3.eth.gas_price
    })
    
    # Sign and send
    signed_withdraw = account.sign_transaction(withdraw_tx)
    withdraw_hash = w3.eth.send_raw_transaction(signed_withdraw.rawTransaction)
    
    print(f"   Transaction hash: {withdraw_hash.hex()}")
    print(f"   Etherscan: https://sepolia.etherscan.io/tx/{withdraw_hash.hex()}")
    print(f"   Waiting for confirmation...")
    
    try:
        withdraw_receipt = w3.eth.wait_for_transaction_receipt(withdraw_hash, timeout=180)
        
        if withdraw_receipt['status'] != 1:
            print(f"❌ Withdrawal failed!")
            print(f"   Gas used: {withdraw_receipt['gasUsed']}")
            sys.exit(1)
        
        print(f"✅ Withdrawal confirmed!")
        print(f"   Block: {withdraw_receipt['blockNumber']}")
        print(f"   Gas used: {withdraw_receipt['gasUsed']}")
        
        gas_cost_eth = withdraw_receipt['gasUsed'] * w3.eth.gas_price / 10**18
        print(f"   Gas cost: {gas_cost_eth:.6f} ETH")
        
    except Exception as e:
        print(f"⏳ Transaction submitted but confirmation timed out")
        print(f"   Check status: https://sepolia.etherscan.io/tx/{withdraw_hash.hex()}")
        print(f"   Run: python3 src/execution/check_tx.py")
        sys.exit(0)
    
    print()
    
    # Check balances after withdrawal
    print("=" * 60)
    print("STEP 3: Verify Withdrawal")
    print("=" * 60)
    
    # Wait a moment for state to update
    import time
    time.sleep(2)
    
    # Check shares after
    user_shares_after = vault.functions.shares(account.address).call()
    shares_burned = user_shares - user_shares_after
    
    print(f"Shares after: {user_shares_after / 10**6:.4f}")
    print(f"Shares burned: {shares_burned / 10**6:.4f}")
    
    # Check USDC balance after
    usdc_balance_after = usdc.functions.balanceOf(account.address).call()
    usdc_received = usdc_balance_after - usdc_balance_before
    
    print(f"\nUSDC balance after: {usdc_balance_after / 10**6:.4f} USDC")
    print(f"USDC received: {usdc_received / 10**6:.4f} USDC")
    
    # Check remaining vault position
    try:
        tvl_after = vault.functions.totalValueLocked().call()
        print(f"\nVault TVL after: {tvl_after / 10**6:.4f} USDC")
    except:
        pass
    
    print()
    print("=" * 60)
    print("✅ WITHDRAWAL SUCCESSFUL!")
    print("=" * 60)
    print()
    print(f"📊 Summary:")
    print(f"   Shares burned: {shares_burned / 10**6:.4f}")
    print(f"   USDC received: {usdc_received / 10**6:.4f} USDC")
    print(f"   Remaining shares: {user_shares_after / 10**6:.4f}")
    print(f"   Transaction: https://sepolia.etherscan.io/tx/{withdraw_hash.hex()}")
    print()
    print("💡 Next steps:")
    print("   1. Deposit more: python3 src/execution/deposit_testnet.py")
    print("   2. Test rebalancing: python3 src/execution/rebalance_testnet.py")
    print("   3. Check vault on Etherscan")
    print()

if __name__ == '__main__':
    main()
