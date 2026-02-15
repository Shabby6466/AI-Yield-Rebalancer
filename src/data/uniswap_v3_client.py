
import logging
import time
from typing import Dict, Optional, Tuple
from web3 import Web3
import os
from eth_abi import decode

logger = logging.getLogger(__name__)

class UniswapV3Client:
    """
    Client for fetching Uniswap V3 pool metrics directly from on-chain.
    Metrics: feeGrowthGlobal, liquidity, sqrtPriceX96.
    """
    
    # Minimal ABI for Uniswap V3 Pool
    POOL_ABI = [
        {"inputs": [], "name": "slot0", "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"}
        ], "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "liquidity", "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}], "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "feeGrowthGlobal0X128", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "feeGrowthGlobal1X128", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "token0", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "token1", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "fee", "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}], "stateMutability": "view", "type": "function"}
    ]

    def __init__(self, w3: Web3):
        self.w3 = w3

    def get_pool_metrics(self, pool_address: str) -> Optional[Dict]:
        """
        Fetch all key metrics for a Uniswap V3 pool.
        """
        try:
            pool_address = Web3.to_checksum_address(pool_address)
            contract = self.w3.eth.contract(address=pool_address, abi=self.POOL_ABI)
            
            # Fetch Slot0 (Price and Tick)
            slot0 = contract.functions.slot0().call()
            sqrtPriceX96 = slot0[0]
            tick = slot0[1]
            
            # Fetch Liquidity
            liquidity = contract.functions.liquidity().call()
            
            # Fetch Fee Growth
            fee_growth0 = contract.functions.feeGrowthGlobal0X128().call()
            fee_growth1 = contract.functions.feeGrowthGlobal1X128().call()
            
            return {
                "pool_address": pool_address,
                "sqrtPriceX96": sqrtPriceX96,
                "tick": tick,
                "liquidity": liquidity,
                "feeGrowthGlobal0X128": fee_growth0,
                "feeGrowthGlobal1X128": fee_growth1,
                "timestamp": int(time.time())
            }
        except Exception as e:
            logger.error(f"Error fetching Uniswap V3 pool metrics: {e}")
            return None

    def calculate_price(self, sqrtPriceX96: int, decimals0: int = 18, decimals1: int = 18) -> float:
        """
        Convert sqrtPriceX96 to a human-readable price (token1 per token0).
        Price = (sqrtPriceX96 / 2^96)^2 * 10^(decimals0 - decimals1)
        """
        price = ((sqrtPriceX96 / (2**96)) ** 2) * (10 ** (decimals0 - decimals1))
        return price
