"""
Live RPC Data Collectors for Aave V3 and Curve

Uses Web3.py with standard ABIs to fetch real-time yield data
from smart contracts for ML inference.
"""

from web3 import Web3
from web3.exceptions import ContractLogicError
import os
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_batch
from datetime import datetime
import time
from typing import List, Dict, Optional

load_dotenv()


class AaveV3Collector:
    """
    Collects real-time data from Aave V3 using the UiPoolDataProvider contract.
    This contract provides all reserve data in a single call.
    """
    
    # Aave V3 UiPoolDataProvider on Ethereum Mainnet
    UI_DATA_PROVIDER = "0x91c0eA31b49B69Ea18607702c5d9aCcf53D29160"
    
    # Aave V3 LendingPoolAddressesProvider
    LENDING_POOL_PROVIDER = "0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e"
    
    # Simplified ABI - use direct interface instead
    # We'll query each asset's aToken directly
    ATOKEN_ABI = [
        {
            "inputs": [],
            "name": "UNDERLYING_ASSET_ADDRESS",
            "outputs": [{"internalType": "address", "name": "", "type": "address"}],
            "stateMutability": "view",
            "type": "function"
        }
    ]
    
    # Pool Data Provider ABI - simpler approach
    POOL_DATA_PROVIDER_ABI = [{
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getReserveData",
        "outputs": [
            {"internalType": "uint256", "name": "availableLiquidity", "type": "uint256"},
            {"internalType": "uint256", "name": "totalStableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "totalVariableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "liquidityRate", "type": "uint256"},
            {"internalType": "uint256", "name": "variableBorrowRate", "type": "uint256"},
            {"internalType": "uint256", "name": "stableBorrowRate", "type": "uint256"},
            {"internalType": "uint256", "name": "averageStableBorrowRate", "type": "uint256"},
            {"internalType": "uint256", "name": "liquidityIndex", "type": "uint256"},
            {"internalType": "uint256", "name": "variableBorrowIndex", "type": "uint256"},
            {"internalType": "uint40", "name": "lastUpdateTimestamp", "type": "uint40"}
        ],
        "stateMutability": "view",
        "type": "function"
    }]
    
    # Pool Data Provider address
    POOL_DATA_PROVIDER = "0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3"
    
    def __init__(self, rpc_url: Optional[str] = None):
        """Initialize with Web3 connection."""
        if rpc_url is None:
            rpc_url = os.getenv('ALCHEMY_RPC_URL')
            if not rpc_url:
                raise ValueError("ALCHEMY_RPC_URL not set in environment")
        
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to Ethereum node")
        
        self.pool_data_provider = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.POOL_DATA_PROVIDER),
            abi=self.POOL_DATA_PROVIDER_ABI
        )
        
        print(f"✓ Connected to Ethereum (Block: {self.w3.eth.block_number:,})")
    
    def get_live_data(self) -> List[Dict]:
        """
        Fetch live data for all Aave V3 reserves.
        
        Returns:
            List of dicts with: symbol, address, apy_percent, tvl_usd, utilization_rate
        """
        print("\n Fetching Aave V3 data...")
        
        # Manually specify the assets we want to track
        # Format: (symbol, address, decimals)
        assets_to_track = [
            ('USDC', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', 6),
            ('USDT', '0xdAC17F958D2ee523a2206206994597C13D831ec7', 6),
            ('DAI', '0x6B175474E89094C44Da98b954EedeAC495271d0F', 18),
            ('WETH', '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2', 18),
            ('WBTC', '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599', 8),
        ]
        
        results = []
        current_block = self.w3.eth.block_number
        
        for symbol, address, decimals in assets_to_track:
            try:
                address = Web3.to_checksum_address(address)
                
                # Get reserve data
                reserve_data = self.pool_data_provider.functions.getReserveData(address).call()
                
                available_liquidity = reserve_data[0]
                total_stable_debt = reserve_data[1]
                total_variable_debt = reserve_data[2]
                liquidity_rate = reserve_data[3]  # Ray units (1e27)
                
                # Convert liquidity rate to APY
                RAY = 10**27
                supply_apy = (liquidity_rate / RAY) * 100
                
                # Calculate total supply
                total_supply = available_liquidity + total_stable_debt + total_variable_debt
                total_supply_scaled = total_supply / (10 ** decimals)
                
                # Calculate utilization
                if total_supply > 0:
                    total_borrowed = total_stable_debt + total_variable_debt
                    utilization = (total_borrowed / total_supply) * 100
                else:
                    utilization = 0
                
                results.append({
                    "symbol": symbol,
                    "address": address,
                    "apy_percent": round(supply_apy, 4),
                    "tvl_native": total_supply_scaled,
                    "utilization_rate": round(utilization, 4),
                    "block_number": current_block,
                    "decimals": decimals
                })
                
                print(f"  {symbol:6s} | APY: {supply_apy:6.2f}% | Util: {utilization:5.1f}%")
                
            except Exception as e:
                print(f"  Error fetching {symbol}: {str(e)}")
                continue
        
        return results


class CurveCollector:
    """
    Collects real-time data from Curve pools.
    """
    
    # Curve Registry on Ethereum Mainnet
    CURVE_REGISTRY = "0x90E00ACe148ca3b23Ac1bC8C240C2a7Dd9c2d7f5"
    
    # Simplified ABI for pool queries
    REGISTRY_ABI = [
        {
            "name": "pool_count",
            "outputs": [{"type": "uint256", "name": ""}],
            "inputs": [],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "name": "pool_list",
            "outputs": [{"type": "address", "name": ""}],
            "inputs": [{"type": "uint256", "name": "arg0"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "name": "get_pool_name",
            "outputs": [{"type": "string", "name": ""}],
            "inputs": [{"type": "address", "name": "_pool"}],
            "stateMutability": "view",
            "type": "function"
        }
    ]
    
    # Generic pool ABI for virtual price
    POOL_ABI = [
        {
            "name": "get_virtual_price",
            "outputs": [{"type": "uint256", "name": ""}],
            "inputs": [],
            "stateMutability": "view",
            "type": "function"
        }
    ]
    
    def __init__(self, rpc_url: Optional[str] = None):
        """Initialize with Web3 connection."""
        if rpc_url is None:
            rpc_url = os.getenv('ALCHEMY_RPC_URL')
            if not rpc_url:
                raise ValueError("ALCHEMY_RPC_URL not set in environment")
        
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to Ethereum node")
        
        self.registry = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.CURVE_REGISTRY),
            abi=self.REGISTRY_ABI
        )
        
        print(f"✓ Connected to Ethereum for Curve (Block: {self.w3.eth.block_number:,})")
    
    def get_live_data(self, pool_addresses: List[str]) -> List[Dict]:
        """
        Fetch live data for specified Curve pools.
        
        Args:
            pool_addresses: List of Curve pool addresses to query
            
        Returns:
            List of dicts with: symbol, address, virtual_price, block_number
        """
        print("\n Fetching Curve data...")
        
        results = []
        current_block = self.w3.eth.block_number
        
        for pool_address in pool_addresses:
            try:
                pool_address = Web3.to_checksum_address(pool_address)
                
                # Get pool name
                try:
                    pool_name = self.registry.functions.get_pool_name(pool_address).call()
                except:
                    pool_name = pool_address[:10]
                
                # Get virtual price
                pool_contract = self.w3.eth.contract(
                    address=pool_address,
                    abi=self.POOL_ABI
                )
                
                virtual_price = pool_contract.functions.get_virtual_price().call()
                virtual_price_scaled = virtual_price / 10**18
                
                results.append({
                    "symbol": pool_name,
                    "address": pool_address,
                    "virtual_price": virtual_price_scaled,
                    "block_number": current_block
                })
                
                print(f"  {pool_name:20s} | Virtual Price: {virtual_price_scaled:.6f}")
                
            except Exception as e:
                print(f"  Error fetching pool {pool_address[:10]}: {str(e)}")
                continue
        
        return results


class LiveDataCollector:
    """
    Main collector that coordinates Aave and Curve data collection
    and stores to the database.
    """
    
    def __init__(self):
        """Initialize collectors and database connection."""
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': os.getenv('DB_PORT', '5432'),
            'database': os.getenv('DB_NAME', 'defi_yield_db'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD', 'postgres')
        }
        
        self.aave_collector = AaveV3Collector()
        self.curve_collector = CurveCollector()
    
    def get_asset_id(self, address: str, protocol: str) -> Optional[int]:
        """Get asset ID from database."""
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            
            cur.execute(
                "SELECT id FROM assets WHERE LOWER(address) = LOWER(%s) AND protocol = %s",
                (address, protocol)
            )
            result = cur.fetchone()
            
            cur.close()
            conn.close()
            
            return result[0] if result else None
            
        except Exception as e:
            print(f"Error fetching asset ID: {str(e)}")
            return None
    
    def store_data(self, protocol: str, data: List[Dict]):
        """
        Store collected data in the database.
        
        Args:
            protocol: Protocol name ('aave_v3', 'curve')
            data: List of data dicts
        """
        if not data:
            return
        
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            
            current_time = datetime.utcnow()
            records = []
            
            for item in data:
                asset_id = self.get_asset_id(item['address'], protocol)
                
                if not asset_id:
                    print(f"  ⚠ Asset {item.get('symbol', item['address'])} not in database, skipping")
                    continue
                
                records.append((
                    current_time,
                    asset_id,
                    item.get('apy_percent'),
                    item.get('tvl_native'),  # Will be NULL for now
                    None,  # volume_24h_usd
                    item.get('utilization_rate'),
                    None,  # volatility_24h
                    item.get('block_number')
                ))
            
            if records:
                insert_query = """
                    INSERT INTO yield_metrics 
                    (time, asset_id, apy_percent, tvl_usd, volume_24h_usd,
                     utilization_rate, volatility_24h, block_number)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (time, asset_id) DO NOTHING
                """
                
                execute_batch(cur, insert_query, records)
                conn.commit()
                
                print(f"  ✓ Stored {len(records)} records for {protocol}")
            
            cur.close()
            conn.close()
            
        except Exception as e:
            print(f"Error storing data: {str(e)}")
            if 'conn' in locals():
                conn.rollback()
                conn.close()
    
    def collect_and_store(self):
        """Collect data from all sources and store in database."""
        print("\n" + "="*60)
        print(f"Live Data Collection - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print("="*60)
        
        # Collect Aave V3 data
        aave_data = self.aave_collector.get_live_data()
        self.store_data('aave_v3', aave_data)
        
        # Collect Curve data for known pools
        curve_pools = [
            '0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7',  # 3pool
            '0xDC24316b9AE028F1497c275EB9192a3Ea0f67022',  # stETH
            '0xd632f22692FaC7611d2AA1C0D552930D43CAEd3B',  # FRAX
        ]
        curve_data = self.curve_collector.get_live_data(curve_pools)
        self.store_data('curve', curve_data)
        
        print("\n✓ Collection complete")


def main():
    """Main entry point for live data collection."""
    collector = LiveDataCollector()
    collector.collect_and_store()


if __name__ == "__main__":
    main()
