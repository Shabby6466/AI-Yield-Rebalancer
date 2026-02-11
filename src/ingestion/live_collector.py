"""
Live Data Collector using DefiLlama API

Fetches the latest data from DefiLlama for Aave and Curve pools.
Much more reliable than RPC calls for production use.
"""

import requests
import psycopg2
from psycopg2.extras import execute_batch
from datetime import datetime
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional

load_dotenv()


class LiveCollector:
    """Collects current yield data using DefiLlama API."""
    
    BASE_URL = "https://yields.llama.fi"
    
    # Mapping of our asset symbols to DefiLlama pool IDs
    # These were found using the backfill_client.py find_pool_id() method
    POOL_MAPPINGS = {
        # Aave V3
        ('USDC', 'aave_v3'): 'aa70268e-4b52-42bf-a116-608b370f9501',
        ('USDT', 'aave_v3'): 'f981a304-bb6c-45b8-b0c5-fd2f515ad23a',
        ('DAI', 'aave_v3'): '3665ee7e-6c5d-49d9-abb7-c47ab5d9d4ac',
        ('WETH', 'aave_v3'): 'e880e828-ca59-4ec6-8d4f-27182a4dc23d',
        ('WBTC', 'aave_v3'): '7e382157-b1bc-406d-b17b-facba43b716e',
        
        # Curve
        ('stETH', 'curve'): '57d30b9c-fc66-4ac2-b666-69ad5f410cce',
        ('FRAX', 'curve'): '12ca9565-0369-404e-b209-631305e4012a',
        
        # Uniswap V3 (top pools by TVL)
        ('USDC/WETH', 'uniswap_v3'): '665dc8bc-c79d-4800-97f7-304bf368e547',
        ('WBTC/WETH', 'uniswap_v3'): 'c5599b3a-ea73-4017-a867-72eb971301d1',
        ('USDC/USDT', 'uniswap_v3'): 'e737d721-f45c-40f0-9793-9f56261862b9',
        ('DAI/USDC', 'uniswap_v3'): 'a86ee795-54d9-4812-9148-b312967cefe5',
    }
    
    def __init__(self):
        """Initialize with database connection."""
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': os.getenv('DB_PORT', '5432'),
            'database': os.getenv('DB_NAME', 'defi_yield_db'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD', 'postgres')
        }
    
    def get_pool_latest(self, pool_id: str) -> Optional[Dict]:
        """
        Get the latest data point for a pool.
        Uses the main /pools endpoint which has current data + volume.
        
        Args:
            pool_id: DefiLlama pool UUID
            
        Returns:
            Dict with apy, tvl, volume, volatility, etc or None
        """
        url = f"{self.BASE_URL}/pools"
        
        try:
            response = requests.get(url, timeout=30)
            
            if response.status_code != 200:
                print(f"  ✗ Error fetching pools: {response.status_code}")
                return None
            
            data = response.json()
            
            if 'data' not in data:
                return None
            
            # Find our specific pool
            pool = next((p for p in data['data'] if p.get('pool') == pool_id), None)
            
            if not pool:
                print(f"  ✗ Pool {pool_id[:8]} not found in API")
                return None
            
            # Extract all available fields with proper handling
            result = {
                'apy': pool.get('apy', 0),
                'tvl': pool.get('tvlUsd', 0),
                'volume_24h': pool.get('volumeUsd1d'),  # 24h volume
                'volume_7d': pool.get('volumeUsd7d'),   # 7d volume
                'apy_base': pool.get('apyBase'),         # Base APY
                'apy_reward': pool.get('apyReward'),     # Reward APY
                'apy_mean30d': pool.get('apyMean30d'),   # 30d average
                'il7d': pool.get('il7d'),                # 7d impermanent loss
                'mu': pool.get('mu'),                    # Mean return
                'sigma': pool.get('sigma'),              # Volatility/std dev
                'count': pool.get('count'),              # Data point count
            }
            
            return result
            
        except Exception as e:
            print(f"  ✗ Exception: {str(e)}")
            return None
    
    def get_asset_id(self, symbol: str, protocol: str) -> Optional[int]:
        """Get asset ID from database."""
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            
            cur.execute(
                "SELECT id FROM assets WHERE symbol = %s AND protocol = %s",
                (symbol, protocol)
            )
            result = cur.fetchone()
            
            cur.close()
            conn.close()
            
            return result[0] if result else None
            
        except Exception as e:
            print(f"  ✗ Error fetching asset ID: {str(e)}")
            return None
    
    def store_data(self, records: List[tuple]):
        """
        Store collected data in database.
        
        Args:
            records: List of tuples (time, asset_id, apy, tvl, ...)
        """
        if not records:
            print("  ⚠ No records to store")
            return
        
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            
            insert_query = """
                INSERT INTO yield_metrics 
                (time, asset_id, apy_percent, tvl_usd, volume_24h_usd,
                 utilization_rate, volatility_24h, block_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (time, asset_id) DO UPDATE SET
                    apy_percent = EXCLUDED.apy_percent,
                    tvl_usd = EXCLUDED.tvl_usd,
                    volume_24h_usd = EXCLUDED.volume_24h_usd,
                    utilization_rate = EXCLUDED.utilization_rate,
                    volatility_24h = EXCLUDED.volatility_24h,
                    block_number = EXCLUDED.block_number
            """
            
            execute_batch(cur, insert_query, records, page_size=100)
            conn.commit()
            
            print(f"  ✓ Stored/updated {len(records)} records")
            
            cur.close()
            conn.close()
            
        except Exception as e:
            print(f"  ✗ Error storing data: {str(e)}")
            if 'conn' in locals():
                conn.rollback()
                conn.close()
    
    def collect_and_store(self):
        """Main collection workflow."""
        print("\n Collecting latest yields from DefiLlama...")
        
        current_time = datetime.utcnow()
        records = []
        
        for (symbol, protocol), pool_id in self.POOL_MAPPINGS.items():
            print(f"\n  {protocol:10s} | {symbol:6s}...", end=" ")
            
            # Get asset ID
            asset_id = self.get_asset_id(symbol, protocol)
            if not asset_id:
                print("✗ Not in database")
                continue
            
            # Fetch latest data
            data = self.get_pool_latest(pool_id)
            if not data:
                print("✗ Failed to fetch")
                continue
            
            # Add to records with all available fields
            # Calculate volatility from sigma if available
            volatility_24h = None
            if data.get('sigma'):
                # Sigma is annualized volatility, convert to daily
                volatility_24h = float(data['sigma']) / (365 ** 0.5)
            
            records.append((
                current_time,
                asset_id,
                float(data['apy']),
                float(data['tvl']) if data.get('tvl') else None,
                float(data['volume_24h']) if data.get('volume_24h') else None,
                None,  # utilization_rate (Aave-specific, not in API)
                volatility_24h,  # Calculated from sigma
                None   # block_number (not provided by DefiLlama)
            ))
            
            # Format volume for display
            vol_str = f"${data.get('volume_24h', 0):,.0f}" if data.get('volume_24h') else "N/A"
            print(f"✓ APY: {data['apy']:5.2f}% | TVL: ${data.get('tvl', 0):,.0f} | Vol: {vol_str}")
        
        # Store all records
        if records:
            print()
            self.store_data(records)
            print("\n✅ Live collection complete")
        else:
            print("\n⚠ No data collected")


def main():
    """Main entry point."""
    collector = LiveCollector()
    collector.collect_and_store()


if __name__ == "__main__":
    main()
