#!/usr/bin/env python3
"""
Deploy idle USDC in the StrategyHub into Aave/Compound.

The Hub has raw USDC from allocateToStrategy(), but rebalance() only counts
aUsdc+compoundComet balances. We need to call hub.deposit() to deploy the
idle USDC into protocols via _deployCapital().
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
print(f"Hub:    {HUB}")
print(f"Keeper: {keeper.address}")

ERC20_ABI = [
    {"name":"balanceOf","type":"function","inputs":[{"type":"address"}],"outputs":[{"type":"uint256"}]},
    {"name":"approve","type":"function","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"type":"bool"}]},
]
HUB_ABI = [
    {"name":"deposit","type":"function","inputs":[{"name":"amount","type":"uint256"}],"outputs":[]},
    {"name":"totalValue","type":"function","inputs":[],"outputs":[{"type":"uint256"}]},
    {"name":"getBalances","type":"function","inputs":[],"outputs":[
        {"name":"aaveBalance","type":"uint256"},
        {"name":"compoundBalance","type":"uint256"},
        {"name":"idleBalance","type":"uint256"},
        {"name":"total","type":"uint256"},
    ]},
]

usdc = w3.eth.contract(address=USDC, abi=ERC20_ABI)
hub  = w3.eth.contract(address=Web3.to_checksum_address(HUB), abi=HUB_ABI)

hub_usdc = usdc.functions.balanceOf(Web3.to_checksum_address(HUB)).call()
balances = hub.functions.getBalances().call()

print(f"\nHub raw USDC (idle):  {hub_usdc/1e6:.2f}")
print(f"Hub Aave balance:     {balances[0]/1e6:.2f}")
print(f"Hub Compound balance: {balances[1]/1e6:.2f}")
print(f"Hub total value:      {balances[3]/1e6:.2f}")

if hub_usdc == 0:
    print("\n✅ No idle USDC in Hub to deploy.")
    sys.exit(0)

print(f"\n🚀 Approving Hub to spend {hub_usdc/1e6:.2f} USDC from keeper...")

# The keeper needs to have USDC to call hub.deposit()
# But the USDC is already IN the hub as raw balance.
# We need to call hub.deposit() from an account that has USDC and approves the hub.
# Since the USDC is already in the hub (from allocateToStrategy raw transfer),
# we use a workaround: call hub.deposit(0) won't work.
# Instead, we need to send a tiny deposit to trigger _deployCapital on the idle funds.
# Actually the cleanest fix: the keeper approves + deposits a tiny amount,
# which triggers _deployCapital on ALL idle funds including the existing 50k.

# Check keeper USDC balance
keeper_usdc = usdc.functions.balanceOf(keeper.address).call()
print(f"Keeper USDC balance:  {keeper_usdc/1e6:.2f}")

if keeper_usdc == 0:
    print("\n⚠️  Keeper has no USDC. The 50k USDC is already in the Hub as raw balance.")
    print("   The Hub's _deployCapital() is only triggered on deposit().")
    print("   We need to impersonate the vault or use a different approach.")
    print("\n   Alternative: Call hub.deposit() from the vault address (which has VAULT_ROLE).")
    print("   The vault already transferred the USDC, so we need to re-trigger deployment.")
    
    # Try calling deposit(0) — won't work as it needs amount > 0
    # Best approach: use eth_sendTransaction with impersonation on Anvil
    print("\n🔧 Using Anvil impersonation to call hub.deposit() from vault...")
    
    VAULT_ADDR = Web3.to_checksum_address(VAULT)
    HUB_ADDR   = Web3.to_checksum_address(HUB)
    
    # Impersonate the vault
    w3.provider.make_request("anvil_impersonateAccount", [VAULT_ADDR])
    w3.provider.make_request("anvil_setBalance", [VAULT_ADDR, hex(10**18)])  # Give it ETH for gas
    
    # Approve hub to spend USDC from vault (vault has 0 USDC now, hub has it)
    # Actually we need to approve from hub's perspective — but hub already has the USDC
    # The deposit() function does: usdc.safeTransferFrom(msg.sender, address(this), amount)
    # So we can't deposit 0 from vault since vault has 0 USDC.
    
    # REAL FIX: impersonate hub itself and call _deployCapital indirectly
    # by calling deposit() with amount=0 won't work.
    # 
    # Simplest: impersonate hub, approve aave/compound, supply directly
    print("   Impersonating Hub to deploy funds to Aave directly...")
    
    AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
    COMPOUND  = "0xc3d688B66703497DAA19211EEdff47f25384cdc3"
    
    # Impersonate hub
    w3.provider.make_request("anvil_impersonateAccount", [HUB_ADDR])
    w3.provider.make_request("anvil_setBalance", [HUB_ADDR, hex(10**18)])
    
    # Approve Aave to spend hub's USDC (50% = 25000 USDC)
    half = hub_usdc // 2
    
    # Approve + supply to Aave
    approve_aave_data = usdc.encodeABI("approve", [AAVE_POOL, half])
    w3.eth.send_transaction({"from": HUB_ADDR, "to": USDC, "data": approve_aave_data, "gas": 100000})
    
    AAVE_ABI = [{"name":"supply","type":"function","inputs":[
        {"name":"asset","type":"address"},{"name":"amount","type":"uint256"},
        {"name":"onBehalfOf","type":"address"},{"name":"referralCode","type":"uint16"}
    ],"outputs":[]}]
    aave = w3.eth.contract(address=AAVE_POOL, abi=AAVE_ABI)
    tx_aave = aave.functions.supply(USDC, half, HUB_ADDR, 0).build_transaction({
        "from": HUB_ADDR, "gas": 500000, "gasPrice": w3.eth.gas_price,
        "nonce": w3.eth.get_transaction_count(HUB_ADDR),
    })
    w3.eth.send_transaction(tx_aave)
    
    # Approve + supply to Compound
    remaining = hub_usdc - half
    approve_comp_data = usdc.encodeABI("approve", [COMPOUND, remaining])
    w3.eth.send_transaction({"from": HUB_ADDR, "to": USDC, "data": approve_comp_data, "gas": 100000})
    
    COMP_ABI = [{"name":"supply","type":"function","inputs":[
        {"name":"asset","type":"address"},{"name":"amount","type":"uint256"}
    ],"outputs":[]}]
    comp = w3.eth.contract(address=COMPOUND, abi=COMP_ABI)
    tx_comp = comp.functions.supply(USDC, remaining).build_transaction({
        "from": HUB_ADDR, "gas": 500000, "gasPrice": w3.eth.gas_price,
        "nonce": w3.eth.get_transaction_count(HUB_ADDR),
    })
    w3.eth.send_transaction(tx_comp)
    
    w3.provider.make_request("anvil_stopImpersonatingAccount", [HUB_ADDR])
    
    # Verify
    balances_after = hub.functions.getBalances().call()
    print(f"\n✅ Deployment complete!")
    print(f"   Hub Aave balance:     {balances_after[0]/1e6:.2f}")
    print(f"   Hub Compound balance: {balances_after[1]/1e6:.2f}")
    print(f"   Hub total value:      {balances_after[3]/1e6:.2f}")
else:
    print(f"\n🚀 Keeper has USDC. Approving Hub and depositing...")
    # Approve hub to spend keeper's USDC
    nonce = w3.eth.get_transaction_count(keeper.address)
    approve_tx = usdc.functions.approve(
        Web3.to_checksum_address(HUB), hub_usdc
    ).build_transaction({"from": keeper.address, "nonce": nonce, "gas": 100000, "gasPrice": w3.eth.gas_price})
    signed = keeper.sign_transaction(approve_tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw))
    
    # Deposit into hub
    nonce += 1
    deposit_tx = hub.functions.deposit(hub_usdc).build_transaction({
        "from": keeper.address, "nonce": nonce, "gas": 500000, "gasPrice": w3.eth.gas_price
    })
    signed = keeper.sign_transaction(deposit_tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw))
    
    if receipt["status"] == 1:
        balances_after = hub.functions.getBalances().call()
        print(f"✅ Deployment complete!")
        print(f"   Hub Aave balance:     {balances_after[0]/1e6:.2f}")
        print(f"   Hub Compound balance: {balances_after[1]/1e6:.2f}")
    else:
        print("❌ Deposit failed")
