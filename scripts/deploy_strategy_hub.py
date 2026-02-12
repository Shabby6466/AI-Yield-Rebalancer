import json
import os
from web3 import Web3
from dotenv import load_dotenv

def deploy():
    # Connect to local Anvil
    w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
    if not w3.is_connected():
        print("Error: Could not connect to Anvil at http://127.0.0.1:8545")
        return

    # Account 0 from Anvil
    deployer = w3.eth.account.from_key("0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
    
    # Load artifact
    with open("contracts/out/StrategyHub.sol/StrategyHub.json", "r") as f:
        artifact = json.load(f)
    
    abi = artifact["abi"]
    bytecode = artifact["bytecode"]["object"]
    
    # Mainnet addresses
    USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
    AUSDC = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
    COMPOUND_COMET = "0xc3d688B66703497DAA19211EEdff47f25384cdc3"
    
    print(f"Deploying StrategyHub from {deployer.address}...")
    
    StrategyHub = w3.eth.contract(abi=abi, bytecode=bytecode)
    
    # Build transaction
    construct_txn = StrategyHub.constructor(
        USDC, AAVE_POOL, AUSDC, COMPOUND_COMET
    ).build_transaction({
        'from': deployer.address,
        'nonce': w3.eth.get_transaction_count(deployer.address),
        'gas': 5000000,
        'gasPrice': w3.to_wei('50', 'gwei')
    })
    
    # Sign and send
    signed_txn = w3.eth.account.sign_transaction(construct_txn, private_key=deployer.key)
    tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
    
    print(f"Transaction sent: {tx_hash.hex()}")
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    address = tx_receipt.contractAddress
    print(f"StrategyHub deployed to: {address}")
    
    # Save address for dashboard
    os.makedirs("contracts", exist_ok=True)
    with open("contracts/deployed_address.txt", "w") as f:
        f.write(address)

if __name__ == "__main__":
    deploy()
