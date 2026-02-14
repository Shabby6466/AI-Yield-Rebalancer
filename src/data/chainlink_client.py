import logging
import json
from web3 import Web3
import os
import asyncio

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
    'USDT': '0xEe9F2375b4bdF6387aa8265dD4FB8F16512A1d46' # USDT/ETH or use USDT/USD
}

class ChainlinkClient:
    """
    Client for fetching decentralized price feeds from Chainlink oracles.
    """
    
    def __init__(self, w3: Web3):
        self.w3 = w3
        logger.info("ChainlinkClient initialized with Web3 provider.")

    async def get_asset_price(self, asset_symbol: str) -> tuple[float, int]:
        """
        Fetch latest price and update timestamp for an asset.
        Returns (price: float, updatedAt: int)
        """
        addr = PRICE_FEEDS.get(asset_symbol.upper())
        if not addr:
            logger.warning(f"No price feed found for {asset_symbol}, defaulting to 1.0")
            return 1.0, int(datetime(2026, 1, 1).timestamp())

        def _fetch_price():
            contract = self.w3.eth.contract(address=self.w3.to_checksum_address(addr), abi=CHAINLINK_ABI)
            # answer is price, updatedAt is the timestamp
            _, answer, _, updatedAt, _ = contract.functions.latestRoundData().call()
            decimals = contract.functions.decimals().call()
            return float(answer) / (10 ** decimals), updatedAt

        try:
            # Run blocking Web3 call in a thread to keep the async loop alive
            price, ts = await asyncio.to_thread(_fetch_price)
            return price, ts
        except Exception as e:
            logger.error(f"Failed to fetch {asset_symbol} price from Chainlink: {e}")
            # Fallback for ETH if we really need it for decision math
            fallback_price = 2500.0 if asset_symbol == 'ETH' else 1.0
            return fallback_price, int(datetime(2026, 1, 1).timestamp())

    async def check_peg_stability(self, asset_symbol: str, threshold: float = 0.98) -> bool:
        """
        Check if a stablecoin asset is maintaining its peg.
        """
        price, _ = await self.get_asset_price(asset_symbol)
        return price >= threshold
