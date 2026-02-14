import logging
import json
from web3 import Web3
import os

logger = logging.getLogger(__name__)

# Standard Aggregator Proxy ABI (minimal for latestRoundData)
CHAINLINK_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Common Mainnet Price Feed Addresses
PRICE_FEEDS = {
    'ETH': '0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419',
    'USDC': '0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6',
    'DAI': '0xAed0c38402a5d19df6E4c03F4E2DceD6e29c1ee9',
    'USDT': '0x3E7d1eA13978982C1014e396656711580f4f954b' # USDT/ETH or use USDT/USD
}

class ChainlinkClient:
    """
    Client for fetching decentralized price feeds from Chainlink oracles.
    """
    
    def __init__(self, w3: Web3):
        self.w3 = w3
        logger.info("ChainlinkClient initialized with Web3 provider.")

    def get_asset_price(self, asset_symbol: str) -> float:
        """
        Fetch latest price for an asset (e.g., 'ETH', 'USDC').
        """
        addr = PRICE_FEEDS.get(asset_symbol.upper())
        if not addr:
            logger.warning(f"No price feed found for {asset_symbol}, defaulting to 1.0")
            return 1.0

        try:
            contract = self.w3.eth.contract(address=self.w3.to_checksum_address(addr), abi=CHAINLINK_ABI)
            # answer is the price
            _, answer, _, _, _ = contract.functions.latestRoundData().call()
            decimals = contract.functions.decimals().call()
            
            price = float(answer) / (10 ** decimals)
            return price
        except Exception as e:
            logger.error(f"Failed to fetch {asset_symbol} price from Chainlink: {e}")
            # Fallback for ETH if we really need it for decision math
            return 2500.0 if asset_symbol == 'ETH' else 1.0

    def check_peg_stability(self, asset_symbol: str, threshold: float = 0.98) -> bool:
        """
        Check if a stablecoin asset is maintaining its peg.
        """
        price = self.get_asset_price(asset_symbol)
        return price >= threshold
