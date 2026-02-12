import json
from web3 import Web3

def seed():
    w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
    
    with open("contracts/deployed_address.txt", "r") as f:
        hub_address = f.read().strip()
    
    USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    USDC_WHALE = "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"
    
    # ABI for ERC20 transfer
    erc20_abi = [
        {"inputs":[{"name":"to","type":"address"},{"name":"value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
        {"inputs":[{"name":"spender","type":"address"},{"name":"value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
        {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
    ]
    
    usdc_contract = w3.eth.contract(address=USDC, abi=erc20_abi)
    
    print(f"Impersonating USDC whale {USDC_WHALE} to seed Hub...")
    
    # Anvil specific: impersonate account
    w3.provider.make_request("anvil_impersonateAccount", [USDC_WHALE])
    
    amount = 500_000 * 10**6 # 500k USDC
    
    # Send USDC from whale to Hub
    tx_hash = usdc_contract.functions.transfer(hub_address, amount).transact({'from': USDC_WHALE})
    print(f"Sent 500k USDC to Hub. TX: {tx_hash.hex()}")
    
    # Stop impersonating
    w3.provider.make_request("anvil_stopImpersonatingAccount", [USDC_WHALE])
    
    # Now call deposit() on the hub to deploy funds
    # Account 0 is the owner/keeper
    owner = w3.eth.account.from_key("0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
    
    # Load Hub ABI
    with open("contracts/out/StrategyHub.sol/StrategyHub.json", "r") as f:
        artifact = json.load(f)
    
    hub_contract = w3.eth.contract(address=hub_address, abi=artifact["abi"])
    
    # Approving hub to spend its own USDC (sent to it) isn't right, hub needs to transfer from owner or just supply
    # Actually, StrategyHub.deposit(amount) transfers from msg.sender.
    # So let's send USDC to the owner first, then have owner deposit.
    
    w3.provider.make_request("anvil_impersonateAccount", [USDC_WHALE])
    usdc_contract.functions.transfer(owner.address, amount).transact({'from': USDC_WHALE})
    w3.provider.make_request("anvil_stopImpersonatingAccount", [USDC_WHALE])
    
    print(f"Sent 500k USDC to Owner {owner.address}")
    
    # Owner deposits to Hub
    usdc_contract.functions.approve(hub_address, amount).transact({'from': owner.address})
    hub_contract.functions.deposit(amount).transact({'from': owner.address})
    
    print("Owner deposited 500k USDC to Hub. Funds should be deployed to Aave/Compound.")
    
    bals = hub_contract.functions.getBalances().call()
    print(f"Hub Status: Aave=${bals[0]/1e6:,.2f}, Comp=${bals[1]/1e6:,.2f}, Total=${bals[3]/1e6:,.2f}")

if __name__ == "__main__":
    seed()
