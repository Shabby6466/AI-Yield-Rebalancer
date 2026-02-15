
import logging
from typing import List, Dict, Optional
from web3 import Web3
from decimal import Decimal

logger = logging.getLogger(__name__)

class UniswapV3PositionManager:
    """
    Client for querying Uniswap V3 positions via NonfungiblePositionManager.
    """
    
    # NonfungiblePositionManager ABI (Partial)
    ABI = [
        {
            "inputs": [{"internalType": "address", "name": "owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [
                {"internalType": "address", "name": "owner", "type": "address"},
                {"internalType": "uint256", "name": "index", "type": "uint256"}
            ],
            "name": "tokenOfOwnerByIndex",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
            "name": "positions",
            "outputs": [
                {"internalType": "uint96", "name": "nonce", "type": "uint96"},
                {"internalType": "address", "name": "operator", "type": "address"},
                {"internalType": "address", "name": "token0", "type": "address"},
                {"internalType": "address", "name": "token1", "type": "address"},
                {"internalType": "uint24", "name": "fee", "type": "uint24"},
                {"internalType": "int24", "name": "tickLower", "type": "int24"},
                {"internalType": "int24", "name": "tickUpper", "type": "int24"},
                {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
                {"internalType": "uint256", "name": "feeGrowthInside0LastX128", "type": "uint256"},
                {"internalType": "uint256", "name": "feeGrowthInside1LastX128", "type": "uint256"},
                {"internalType": "uint128", "name": "tokensOwed0", "type": "uint128"},
                {"internalType": "uint128", "name": "tokensOwed1", "type": "uint128"}
            ],
            "stateMutability": "view",
            "type": "function"
        }
    ]

    # Ethereum Mainnet Address
    ADDRESS = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"

    def __init__(self, w3: Web3):
        self.w3 = w3
        self.contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(self.ADDRESS),
            abi=self.ABI
        )

    def get_user_positions(self, user_address: str) -> List[Dict]:
        """Fetch all Uniswap V3 positions for a user."""
        positions = []
        try:
            count = self.contract.functions.balanceOf(user_address).call()
            for i in range(count):
                token_id = self.contract.functions.tokenOfOwnerByIndex(user_address, i).call()
                pos_data = self.contract.functions.positions(token_id).call()
                
                positions.append({
                    "tokenId": token_id,
                    "token0": pos_data[2],
                    "token1": pos_data[3],
                    "fee": pos_data[4],
                    "tickLower": pos_data[5],
                    "tickUpper": pos_data[6],
                    "liquidity": pos_data[7],
                    "tokensOwed0": pos_data[10],
                    "tokensOwed1": pos_data[11]
                })
        except Exception as e:
            logger.error(f"Error fetching V3 positions: {e}")
            
        return positions
