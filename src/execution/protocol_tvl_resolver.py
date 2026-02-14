"""
Protocol-Specific TVL Resolver
Handles modern DeFi protocols with indirect accounting (ERC-4626 vaults, Aave, etc.)
"""

import logging
from web3 import Web3
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class ProtocolTVLResolver:
    """Resolves TVL for various DeFi protocols using protocol-specific methods"""
    
    def __init__(self, w3: Web3):
        self.w3 = w3
        
        # ERC-4626 Vault Standard (Yearn, Ethena, etc.)
        self.ERC4626_ABI = [
            {"inputs": [], "name": "totalAssets", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
            {"inputs": [], "name": "asset", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
            {"inputs": [], "name": "decimals", "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"}
        ]
        
        self.ERC20_ABI = [
            {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
            {"constant": True, "inputs": [{" name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "totalSupply", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
        ]
        
        # Known Vault Registry
        self.KNOWN_VAULTS = {
            # Ethena sUSDe (ERC-4626)
            '0x9d39a5de30e57443bff2a8307a4256c8797a3497': {'type': 'erc4626', 'name': 'Ethena sUSDe'},
            # USP - Universal Savings Protocol (ERC-4626)
            '0x098697ba3fee4ea76294c5d6a466a4e3b3e95fe6': {'type': 'erc4626', 'name': 'USP'},
            # Yearn vaults (examples)
            '0xa354f35829ae975e850e23e9615b11da1b3dc4de': {'type': 'erc4626', 'name': 'Yearn USDC'},
        }
    
    def resolve_tvl(self, pool_address: str, asset_address: str, decimals: int) -> Tuple[float, str]:
        """
        Resolve TVL using protocol-specific methods
        
        Returns:
            (tvl_scaled, method_used)
        """
        pool_address = Web3.to_checksum_address(pool_address)
        asset_address = Web3.to_checksum_address(asset_address)
        token_contract = self.w3.eth.contract(address=asset_address, abi=self.ERC20_ABI)
        
        tvl_scaled = 0.0
        method = "unknown"
        
        # 1. Check Known Vault Registry First
        pool_lower = pool_address.lower()
        if pool_lower in self.KNOWN_VAULTS:
            vault_info = self.KNOWN_VAULTS[pool_lower]
            logger.info(f"Detected Known Vault: {vault_info['name']} (Type: {vault_info['type']})")
            
            if vault_info['type'] == 'erc4626':
                tvl_scaled, method = self._try_erc4626(pool_address, decimals)
                if tvl_scaled > 0:
                    return tvl_scaled, method
        
        # 2. Try ERC-4626 totalAssets() for unknown vaults (auto-detection)
        if tvl_scaled == 0:
            tvl_scaled, method = self._try_erc4626(pool_address, decimals)
            if tvl_scaled > 0:
                logger.info(f"Auto-Detected ERC-4626 Vault: {pool_address[:8]} | TVL: ${tvl_scaled:,.2f}")
                return tvl_scaled, method
        
        # 3. Try totalSupply() for share-based tokens
        if tvl_scaled == 0:
            try:
                pool_contract = self.w3.eth.contract(address=pool_address, abi=self.ERC20_ABI)
                total_supply_raw = pool_contract.functions.totalSupply().call()
                tvl_scaled = float(total_supply_raw) / (10 ** decimals)
                method = "totalSupply() [share token]"
                if tvl_scaled > 0:
                    return tvl_scaled, method
            except:
                pass
        
        # 4. Fallback: Generic balanceOf (least accurate for vaults)
        if tvl_scaled == 0:
            try:
                tvl_raw = token_contract.functions.balanceOf(pool_address).call()
                tvl_scaled = float(tvl_raw) / (10 ** decimals)
                method = "balanceOf() [fallback]"
            except Exception as e:
                logger.error(f"All TVL methods failed: {e}")
        
        return tvl_scaled, method
    
    def _try_erc4626(self, pool_address: str, default_decimals: int) -> Tuple[float, str]:
        """Try to fetch TVL using ERC-4626 totalAssets()"""
        try:
            vault_contract = self.w3.eth.contract(address=pool_address, abi=self.ERC4626_ABI)
            total_assets_raw = vault_contract.functions.totalAssets().call()
            
            # Get underlying asset for correct decimals
            decimals = default_decimals
            try:
                underlying_asset = vault_contract.functions.asset().call()
                underlying_contract = self.w3.eth.contract(address=underlying_asset, abi=self.ERC20_ABI)
                decimals = underlying_contract.functions.decimals().call()
            except:
                pass
            
            tvl_scaled = float(total_assets_raw) / (10 ** decimals)
            method = "ERC-4626 totalAssets()"
            return tvl_scaled, method
        except:
            return 0.0, "unknown"
