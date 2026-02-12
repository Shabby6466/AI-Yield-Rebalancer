import json
import time
from web3 import Web3

class SimController:
    """
    Advanced Simulation Controller for the Local Mainnet Fork (Anvil).
    Enables snapshots, time warping, and state manipulation for AI testing.
    """
    def __init__(self, rpc_url="http://127.0.0.1:8545"):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.snapshot_id = None

    def is_connected(self):
        return self.w3.is_connected()

    # --- 1. Snapshots ---
    def create_snapshot(self):
        """Save the current state of the blockchain."""
        self.snapshot_id = self.w3.provider.make_request("evm_snapshot", [])["result"]
        return self.snapshot_id

    def revert_to_snapshot(self, snapshot_id=None):
        """Restore the blockchain to a previously saved state."""
        sid = snapshot_id or self.snapshot_id
        if sid is None:
            raise ValueError("No snapshot ID provided or recorded.")
        
        result = self.w3.provider.make_request("evm_revert", [sid])["result"]
        # Important: After revert, the snapshot ID is consumed, so we refresh if needed
        return result

    # --- 2. Precise Time Warping ---
    def jump_forward(self, seconds):
        """Advance the blockchain time and mine a block to lock it in."""
        self.w3.provider.make_request("evm_increaseTime", [seconds])
        self.w3.provider.make_request("evm_mine", [])
        return self.w3.eth.get_block('latest').timestamp

    # --- 3. Oracle & State Manipulation (Black Swan) ---
    def set_storage_at(self, address, slot, value):
        """
        Directly manipulate contract storage. 
        Useful for 'Black Swan' events like forcing oracle prices.
        """
        # Value must be 32 bytes hex
        if isinstance(value, int):
            value = hex(value).zfill(66)
        
        return self.w3.provider.make_request("anvil_setStorageAt", [address, slot, value])

    def set_balance(self, address, eth_amount):
        """Force a specific ETH balance for an account."""
        wei_amount = hex(Web3.to_wei(eth_amount, 'ether'))
        return self.w3.provider.make_request("anvil_setBalance", [address, wei_amount])

    # --- 4. Hub Interaction Context ---
    def get_hub_contract(self, address, abi_path="contracts/out/StrategyHub.sol/StrategyHub.json"):
        with open(abi_path, "r") as f:
            artifact = json.load(f)
        return self.w3.eth.contract(address=address, abi=artifact["abi"])

    def impersonate_and_execute(self, target_address, contract_fn, from_address):
        """Execute a transaction by impersonating a specific address."""
        self.w3.provider.make_request("anvil_impersonateAccount", [from_address])
        
        # Note: This is a simplified wrapper. Real usage requires building the tx.
        # But for simulations, we can often just use transact() if it's an anvil account
        tx_hash = contract_fn.transact({'from': from_address})
        
        self.w3.provider.make_request("anvil_stopImpersonatingAccount", [from_address])
        return tx_hash
