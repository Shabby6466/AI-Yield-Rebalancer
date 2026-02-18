import logging
import os
from web3 import Web3
from eth_account import Account
from flashbots import flashbot
from eth_account.signers.local import LocalAccount
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class FlashbotsRelayer:
    """
    Relays rebalancing transactions via Flashbots to prevent MEV (sandwich/frontrunning).
    """
    
    def __init__(self, w3: Web3, signer: LocalAccount):
        """
        Args:
            w3: Standard Web3 instance
            signer: The account that will sign the bundle (and pay for gas)
        """
        self.w3 = w3
        self.signer = signer
        self.chain_id = w3.eth.chain_id
        
        # Detect local mode by RPC URL (Anvil forks mainnet so chain_id=1 is not reliable)
        rpc_url = os.getenv("RPC_URL", "http://localhost:8545").lower()
        self.is_local = any(h in rpc_url for h in ["localhost", "127.0.0.1", "anvil", "0.0.0.0"])
        
        if not self.is_local:
            # Only initialize Flashbots on real mainnet
            flashbots_relay_url = os.getenv("FLASHBOTS_RELAY_URL", "https://relay.flashbots.net")
            flashbot(w3, signer, flashbots_relay_url)
            logger.info(f"Flashbots relay initialized for mainnet: {flashbots_relay_url}")
        else:
            logger.info(f"Local network detected (RPC: {rpc_url}). Using direct tx send (no Flashbots).")

    def _send_direct(self, tx: Dict[str, Any]) -> bool:
        """Send a single transaction directly (for local/test networks)."""
        try:
            signed = self.signer.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt['status'] == 1:
                logger.info(f"✅ Direct tx confirmed: {tx_hash.hex()} (block {receipt['blockNumber']})")
                return True
            else:
                logger.error(f"❌ Direct tx reverted: {tx_hash.hex()}")
                return False
        except Exception as e:
            logger.error(f"Direct tx failed: {e}")
            return False

    def send_rebalance_bundle(self, bundle: List[Dict[str, Any]], target_block: int) -> bool:
        """
        Sends a bundle of transactions. Uses Flashbots on mainnet, direct send on local.
        """
        if self.is_local:
            logger.info(f"[Local Mode] Sending {len(bundle)} tx(s) directly...")
            return all(self._send_direct(tx) for tx in bundle)

        logger.info(f"Targeting block {target_block} for Flashbots bundle...")
        flash_bundle = [{"signer": self.signer, "transaction": tx} for tx in bundle]
            
        try:
            simulation = self.w3.flashbots.simulate(flash_bundle, target_block)
            logger.info(f"Bundle simulation successful: {simulation}")
        except Exception as e:
            logger.error(f"Bundle simulation failed: {e}")
            return False
            
        send_result = self.w3.flashbots.send_bundle(flash_bundle, target_block)
        send_result.wait()

        try:
            receipts = send_result.receipts()
            logger.info(f"Bundle included in block {target_block}! Receipts: {len(receipts)}")
            return True
        except Exception:
            logger.warning(f"Bundle was not included in block {target_block}.")
            return False

    def relay_with_retry(self, tx_list, retry_count=3):
        if self.is_local:
            # On local, just send directly — no retry needed
            return self.send_rebalance_bundle(tx_list, 0)
        current_block = self.w3.eth.block_number
        for i in range(1, retry_count + 1):
            target = current_block + i
            success = self.send_rebalance_bundle(tx_list, target)
            if success:
                return True
        return False

if __name__ == "__main__":
    # Integration test snippet
    print("Flashbots Relayer ready for initialization.")
