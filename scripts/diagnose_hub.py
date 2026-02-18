#!/usr/bin/env python3
"""Diagnose why StrategyHub.rebalance() is reverting."""
import os, sys
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

RPC = os.getenv("RPC_URL", "http://anvil:8545")
HUB = os.getenv("STRATEGY_HUB_ADDRESS")
KEEPER_KEY = os.getenv("KEEPER_PRIVATE_KEY")

w3 = Web3(Web3.HTTPProvider(RPC))
keeper = w3.eth.account.from_key(KEEPER_KEY)

print(f"RPC:    {RPC}  connected={w3.is_connected()}")
print(f"Hub:    {HUB}")
print(f"Keeper: {keeper.address}")
print()

MINIMAL_ABI = [
    {"name": "hasRole",              "type": "function", "inputs": [{"type":"bytes32"},{"type":"address"}], "outputs": [{"type":"bool"}]},
    {"name": "KEEPER_ROLE",          "type": "function", "inputs": [], "outputs": [{"type":"bytes32"}]},
    {"name": "minRebalanceInterval", "type": "function", "inputs": [], "outputs": [{"type":"uint256"}]},
    {"name": "lastRebalanceTime",    "type": "function", "inputs": [], "outputs": [{"type":"uint256"}]},
    {"name": "paused",               "type": "function", "inputs": [], "outputs": [{"type":"bool"}]},
    {"name": "totalValue",           "type": "function", "inputs": [], "outputs": [{"type":"uint256"}]},
    {"name": "aaveAllocationBps",    "type": "function", "inputs": [], "outputs": [{"type":"uint256"}]},
    {"name": "compoundAllocationBps","type": "function", "inputs": [], "outputs": [{"type":"uint256"}]},
]

hub = w3.eth.contract(address=Web3.to_checksum_address(HUB), abi=MINIMAL_ABI)

keeper_role = hub.functions.KEEPER_ROLE().call()
has_role    = hub.functions.hasRole(keeper_role, keeper.address).call()
interval    = hub.functions.minRebalanceInterval().call()
last_time   = hub.functions.lastRebalanceTime().call()
paused      = hub.functions.paused().call()
total_val   = hub.functions.totalValue().call()
aave_bps    = hub.functions.aaveAllocationBps().call()
comp_bps    = hub.functions.compoundAllocationBps().call()

import time
now = int(time.time())
next_allowed = last_time + interval

print(f"KEEPER_ROLE:           {keeper_role.hex()}")
print(f"Keeper has role:       {has_role}  {'✅' if has_role else '❌ MISSING ROLE - THIS IS THE BUG'}")
print(f"Contract paused:       {paused}  {'❌ PAUSED' if paused else '✅'}")
print(f"Total value (USDC):    {total_val / 1e6:.2f}")
print(f"Current allocation:    Aave={aave_bps}bps  Compound={comp_bps}bps")
print(f"minRebalanceInterval:  {interval}s ({interval/3600:.1f}h)")
print(f"lastRebalanceTime:     {last_time}")
print(f"Next rebalance at:     {next_allowed}  (now={now})")
if now < next_allowed:
    print(f"  ❌ TOO SOON — need to wait {next_allowed - now}s more")
else:
    print(f"  ✅ Timing OK")
