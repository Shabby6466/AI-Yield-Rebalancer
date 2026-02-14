"""
Testnet Rebalancing Script
Triggers manual rebalance to allocate vault funds to yield protocols
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

def load_deployment():
    """Load deployment addresses"""
    with open('deployments/sepolia_deployment.json', 'r') as f:
        return json.load(f)

def main():
    print("=" * 60)
    print("⚖️  TESTNET VAULT REBALANCING")
    print("=" * 60)
    print()
    
    # Connect to Sepolia
    rpc_url = os.getenv('SEPOLIA_RPC_URL')
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    if not w3.is_connected():
        print("❌ Error: Could not connect to Sepolia")
        sys.exit(1)
    
    print(f"✅ Connected to Sepolia")
    print(f"   Chain ID: {w3.eth.chain_id}")
    print()
    
    # Load private key
    private_key = os.getenv('DEPLOYER_PRIVATE_KEY')
    account = Account.from_key(private_key)
    print(f"📍 Using account: {account.address}")
    
    eth_balance = w3.eth.get_balance(account.address)
    print(f"   ETH Balance: {w3.from_wei(eth_balance, 'ether'):.4f} ETH")
    print()
    
    # Load deployment
    deployment = load_deployment()
    vault_address = deployment['contracts']['YieldVault']
    rebalance_executor_address = deployment['contracts']['RebalanceExecutor']
    strategy_manager_address = deployment['contracts']['StrategyManager']
    
    print(f"🏦 Vault: {vault_address}")
    print(f"⚖️  RebalanceExecutor: {rebalance_executor_address}")
    print(f"🎯 StrategyManager: {strategy_manager_address}")
    print()
    
    # Load contracts
    vault_abi = load_contract_abi('core/YieldVault')
    rebalance_executor_abi = load_contract_abi('core/RebalanceExecutor')
    strategy_manager_abi = load_contract_abi('strategies/StrategyManager')
    
    vault = w3.eth.contract(address=vault_address, abi=vault_abi)
    rebalance_executor = w3.eth.contract(address=rebalance_executor_address, abi=rebalance_executor_abi)
    strategy_manager = w3.eth.contract(address=strategy_manager_address, abi=strategy_manager_abi)
    
    # Check vault status
    print("=" * 60)
    print("STEP 1: Check Vault Status")
    print("=" * 60)
    
    try:
        tvl = vault.functions.totalValueLocked().call()
        print(f"Total Value Locked: {tvl / 10**6:.4f} USDC")
        
        if tvl == 0:
            print("\n❌ Vault is empty. Deposit first:")
            print("   python3 src/execution/deposit_testnet.py")
            sys.exit(1)
        
        total_shares = vault.functions.totalShares().call()
        print(f"Total Shares: {total_shares / 10**6:.4f}")
        
        last_rebalance = vault.functions.lastRebalanceTime().call()
        print(f"Last Rebalance: Block timestamp {last_rebalance}")
        
    except Exception as e:
        print(f"⚠️  Could not read all vault data: {e}")
    
    print()
    
    # Check if rebalancing is needed
    print("=" * 60)
    print("STEP 2: Check Rebalance Conditions")
    print("=" * 60)
    
    try:
        should_rebalance, reason = rebalance_executor.functions.shouldRebalance().call()
        
        print(f"Should rebalance: {'✅ YES' if should_rebalance else '❌ NO'}")
        print(f"Reason: {reason}")
        print()
        
        if not should_rebalance:
            print("💡 Conditions not met for automatic rebalance")
            print("   Using forceRebalance to execute manually...")
            print()
        
    except Exception as e:
        print(f"Note: Could not check shouldRebalance: {e}")
        print("Will use forceRebalance instead...")
        print()
    
    # Get optimal allocation from strategy manager
    print("=" * 60)
    print("STEP 3: Calculate Optimal Allocation")
    print("=" * 60)
    
    try:
        # Set strategy type (3 = Optimized ML)
        current_strategy = strategy_manager.functions.currentStrategyType().call()
        print(f"Current strategy type: {current_strategy}")
        
        # Get optimal allocation
        allocation = strategy_manager.functions.calculateOptimalAllocation().call()
        
        print(f"\n📊 Optimal Allocation:")
        if len(allocation[0]) > 0:
            for i, (pool_id, percentage) in enumerate(zip(allocation[0], allocation[1])):
                print(f"   Pool {i+1}: {percentage / 100:.2f}%")
        else:
            print("   No pools available or allocation returned empty")
        
    except Exception as e:
        print(f"⚠️  Could not calculate allocation: {e}")
        print("   This is normal if no pools have APY data yet")
    
    print()
    
    # Execute rebalance
    print("=" * 60)
    print("STEP 4: Execute Rebalance")
    print("=" * 60)
    print("🔄 Calling vault.rebalance() directly with allocation...")
    print()
    
    # Call vault rebalance directly with proper parameters
    # The rebalance function expects arrays of pool addresses and allocations
    # For now, allocate 100% to USDC/Aave
    usdc_address = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"
    aave_adapter = deployment['contracts']['AaveAdapter']
    
    # Simple allocation: 100% to USDC via Aave adapter
    pools = [aave_adapter]  # Array of protocol adapter addresses
    allocations = [10000]   # 100% in basis points
    
    print(f"Allocation plan:")
    print(f"   Pool: Aave Adapter ({aave_adapter})")
    print(f"   Allocation: 100%")
    print()
    
    try:
        rebalance_tx = vault.functions.rebalance(
            pools,
            allocations
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 800000,
            'gasPrice': w3.eth.gas_price
        })
        
        # Sign and send
        signed_tx = account.sign_transaction(rebalance_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(f"   Transaction hash: {tx_hash.hex()}")
        print(f"   Etherscan: https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
        print(f"   Waiting for confirmation...")
        
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
            
            if receipt['status'] != 1:
                print(f"\n❌ Rebalance failed!")
                print(f"   Gas used: {receipt['gasUsed']}")
                print(f"   Check transaction on Etherscan for details")
                sys.exit(1)
            
            print(f"\n✅ Rebalance confirmed!")
            print(f"   Block: {receipt['blockNumber']}")
            print(f"   Gas used: {receipt['gasUsed']}")
            
            gas_cost_eth = receipt['gasUsed'] * w3.eth.gas_price / 10**18
            print(f"   Gas cost: {gas_cost_eth:.6f} ETH")
            
            # Check if gas cost is within target (<0.01 ETH)
            if gas_cost_eth < 0.01:
                print(f"   ✅ Gas cost within target (<0.01 ETH)")
            else:
                print(f"   ⚠️  Gas cost exceeds target (>0.01 ETH)")
            
        except Exception as e:
            print(f"\n⏳ Transaction submitted but confirmation timed out")
            print(f"   Check: https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
            sys.exit(0)
        
    except Exception as e:
        print(f"❌ Error executing rebalance: {e}")
        sys.exit(1)
    
    print()
    
    # Check vault status after rebalance
    print("=" * 60)
    print("STEP 5: Verify Rebalance")
    print("=" * 60)
    
    import time
    time.sleep(2)
    
    try:
        tvl_after = vault.functions.totalValueLocked().call()
        print(f"TVL after rebalance: {tvl_after / 10**6:.4f} USDC")
        
        last_rebalance_after = vault.functions.lastRebalanceTime().call()
        print(f"Last rebalance timestamp: {last_rebalance_after}")
        
    except Exception as e:
        print(f"Note: Could not read post-rebalance data: {e}")
    
    print()
    print("=" * 60)
    print("✅ REBALANCE SUCCESSFUL!")
    print("=" * 60)
    print()
    print(f"📊 Summary:")
    print(f"   Transaction: https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
    print(f"   Gas used: {receipt['gasUsed']}")
    print(f"   Gas cost: {gas_cost_eth:.6f} ETH")
    print()
    print("💡 Next steps:")
    print("   1. Check vault allocations on Etherscan")
    print("   2. Monitor Aave position")
    print("   3. Test another deposit/withdrawal cycle")
    print("   4. Update pool APYs: python3 src/execution/update_pools.py")
    print("   5. Run validation: python3 tests/strategy_validator.py")
    print()

if __name__ == '__main__':
    main()
