#!/usr/bin/env python3
"""
Allocate funds from YieldVault → StrategyHub.
The Vault holds the USDC; this calls allocateToStrategy() to forward it to the Hub.
"""
import os, sys
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

RPC    = os.getenv("RPC_URL", "http://anvil:8545")
VAULT  = os.getenv("VAULT_CONTRACT_ADDRESS")
HUB    = os.getenv("STRATEGY_HUB_ADDRESS")
KEY    = os.getenv("KEEPER_PRIVATE_KEY")
USDC   = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

w3      = Web3(Web3.HTTPProvider(RPC))
keeper  = w3.eth.account.from_key(KEY)

print(f"RPC:    {RPC}  connected={w3.is_connected()}")
print(f"Vault:  {VAULT}")
print(f"Hub:    {HUB}")
print(f"Keeper: {keeper.address}")

ERC20_ABI = [{"name":"balanceOf","type":"function","inputs":[{"type":"address"}],"outputs":[{"type":"uint256"}]}]
VAULT_ABI = [
    {"name":"allocateToStrategy","type":"function","inputs":[{"name":"amount","type":"uint256"}],"outputs":[]},
    {"name":"totalAllocated","type":"function","inputs":[],"outputs":[{"type":"uint256"}]},
    {"name":"getIdleAssets","type":"function","inputs":[],"outputs":[{"type":"uint256"}]},
]

usdc  = w3.eth.contract(address=USDC, abi=ERC20_ABI)
vault = w3.eth.contract(address=Web3.to_checksum_address(VAULT), abi=VAULT_ABI)

vault_usdc = usdc.functions.balanceOf(Web3.to_checksum_address(VAULT)).call()
hub_usdc   = usdc.functions.balanceOf(Web3.to_checksum_address(HUB)).call()
idle       = vault.functions.getIdleAssets().call()
allocated  = vault.functions.totalAllocated().call()

print(f"\nVault USDC balance:  {vault_usdc/1e6:.2f}")
print(f"Hub USDC balance:    {hub_usdc/1e6:.2f}")
print(f"Vault idle assets:   {idle/1e6:.2f}")
print(f"Vault totalAllocated:{allocated/1e6:.2f}")

if idle == 0:
    print("\n✅ No idle funds to allocate.")
    sys.exit(0)

print(f"\n🚀 Allocating {idle/1e6:.2f} USDC from Vault → Hub...")

tx = vault.functions.allocateToStrategy(idle).build_transaction({
    "from":  keeper.address,
    "nonce": w3.eth.get_transaction_count(keeper.address),
    "gas":   200000,
    "gasPrice": w3.eth.gas_price,
})
signed  = keeper.sign_transaction(tx)
raw     = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
tx_hash = w3.eth.send_raw_transaction(raw)
receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

if receipt["status"] == 1:
    hub_after = usdc.functions.balanceOf(Web3.to_checksum_address(HUB)).call()
    print(f"✅ Allocation successful! Tx: {tx_hash.hex()}")
    print(f"   Hub USDC balance now: {hub_after/1e6:.2f}")
else:
    print(f"❌ Transaction reverted: {tx_hash.hex()}")
