"""Check transaction status on Sepolia"""
import os
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

tx_hash = "0x436ead50f4b00b17fa95c318f003a8511d181b75f3d2ca3b1c887124beb10721"

rpc_url = os.getenv('SEPOLIA_RPC_URL')
w3 = Web3(Web3.HTTPProvider(rpc_url))

print(f"Checking transaction: {tx_hash}")
print(f"Etherscan: https://sepolia.etherscan.io/tx/{tx_hash}\n")

try:
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    
    if receipt:
        print(f"✅ Transaction found!")
        print(f"   Status: {'✅ SUCCESS' if receipt['status'] == 1 else '❌ FAILED'}")
        print(f"   Block: {receipt['blockNumber']}")
        print(f"   Gas used: {receipt['gasUsed']}")
        
        if receipt['status'] == 1:
            print(f"\n🎉 DEPOSIT SUCCESSFUL!")
            print(f"\nYou can now:")
            print(f"   1. Check your vault balance")
            print(f"   2. Test withdrawal")
            print(f"   3. Test rebalancing")
        else:
            print(f"\n❌ Transaction reverted")
    else:
        print("⏳ Transaction pending...")
        
        tx = w3.eth.get_transaction(tx_hash)
        if tx:
            print(f"   Gas price: {tx['gasPrice']}")
            print(f"   Nonce: {tx['nonce']}")
            print("\nWait a bit longer and check Etherscan")
        
except Exception as e:
    print(f"❌ Error: {e}")
    print("\nTransaction may still be pending. Check Etherscan:")
    print(f"https://sepolia.etherscan.io/tx/{tx_hash}")
