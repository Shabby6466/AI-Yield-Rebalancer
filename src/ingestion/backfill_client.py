"""
DefiLlama Backfill Client

Fetches 1+ year of historical APY and TVL data for Aave and Curve pools
from DefiLlama's free API for ML model training.
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import psycopg2
from psycopg2.extras import execute_batch
import os
from dotenv import load_dotenv
from typing import Optional, List, Dict

load_dotenv()


class DefiLlamaBackfiller:
    """Client for fetching historical yield data from DefiLlama API."""
    
    BASE_URL = "https://yields.llama.fi"
    
    def __init__(self):
        """Initialize the backfiller with database connection."""
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': os.getenv('DB_PORT', '5432'),
            'database': os.getenv('DB_NAME', 'defi_yield_db'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD', 'postgres')
        }
    
    def get_pool_history(self, pool_id: str) -> pd.DataFrame:
        """
        Fetches historical APY and TVL for a specific pool.
        
        Args:
            pool_id: DefiLlama's unique UUID for the pool (e.g., Aave USDC)
            
        Returns:
            DataFrame with columns: time, apy_percent, tvl_usd
        """
        url = f"{self.BASE_URL}/chart/{pool_id}"
        print(f"Fetching history for pool {pool_id}...")
        
        try:
            response = requests.get(url, timeout=30)
            
            if response.status_code != 200:
                print(f"Error fetching {pool_id}: {response.text}")
                return pd.DataFrame()
            
            data = response.json()
            
            # The API returns: {'status': 'success', 'data': [...]}
            if 'data' not in data:
                print(f"No data found for pool {pool_id}")
                return pd.DataFrame()
            
            # Convert to DataFrame
            df = pd.DataFrame(data['data'])
            
            if df.empty:
                print(f"Empty data for pool {pool_id}")
                return df
            
            # Normalize columns for our database
            df['time'] = pd.to_datetime(df['timestamp'])
            df['apy_percent'] = df['apy']
            df['tvl_usd'] = df['tvlUsd']
            df['volume_24h_usd'] = df.get('volumeUsd1d')
            df['apy_base'] = df.get('apyBase')
            df['apy_reward'] = df.get('apyReward')
            
            # Keep what we need
            df = df[['time', 'apy_percent', 'tvl_usd', 'volume_24h_usd']].copy()
            
            # Remove any rows with missing APY
            df = df.dropna(subset=['apy_percent'])
            
            print(f"  ✓ Fetched {len(df)} records from {df['time'].min()} to {df['time'].max()}")
            return df
            
        except Exception as e:
            print(f"Exception fetching pool {pool_id}: {str(e)}")
            return pd.DataFrame()
    
    def find_pool_id(self, protocol_slug: str, symbol: str, chain: str = 'Ethereum') -> Optional[str]:
        """
        Find the DefiLlama pool UUID for a given protocol and asset.
        
        Args:
            protocol_slug: Protocol identifier (e.g., 'aave-v3', 'curve-dex')
            symbol: Asset symbol (e.g., 'USDC', '3pool')
            chain: Blockchain name (default: 'Ethereum')
            
        Returns:
            Pool UUID string or None if not found
        """
        url = f"{self.BASE_URL}/pools"
        print(f"Searching for {protocol_slug} {symbol} on {chain}...")
        
        try:
            response = requests.get(url, timeout=30)
            data = response.json()
            
            if 'data' not in data:
                print("No pools data returned from API")
                return None
            
            # Filter for the protocol, symbol, and chain
            results = [
                p for p in data['data']
                if p.get('project', '').lower() == protocol_slug.lower()
                and symbol.upper() in p.get('symbol', '').upper()
                and p.get('chain', '').lower() == chain.lower()
            ]
            
            if not results:
                print(f"  ✗ No pools found for {protocol_slug} {symbol}")
                return None
            
            # Sort by TVL to get the main pool
            results.sort(key=lambda x: x.get('tvlUsd', 0), reverse=True)
            
            pool_id = results[0].get('pool')
            tvl = results[0].get('tvlUsd', 0)
            apy = results[0].get('apy', 0)
            
            print(f"  ✓ Found pool: {pool_id}")
            print(f"    TVL: ${tvl:,.0f} | APY: {apy:.2f}%")
            
            return pool_id
            
        except Exception as e:
            print(f"Exception searching for pool: {str(e)}")
            return None
    
    def get_asset_id(self, symbol: str, protocol: str) -> Optional[int]:
        """
        Get asset ID from the assets table.
        
        Args:
            symbol: Asset symbol
            protocol: Protocol name
            
        Returns:
            Asset ID or None if not found
        """
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
            print(f"Error fetching asset ID: {str(e)}")
            return None
    
    def insert_yield_data(self, asset_id: int, df: pd.DataFrame, batch_size: int = 1000):
        """
        Insert yield data into the database.
        
        Args:
            asset_id: ID from the assets table
            df: DataFrame with columns: time, apy_percent, tvl_usd
            batch_size: Number of rows to insert per batch
        """
        if df.empty:
            print("No data to insert")
            return
        
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            
            # Prepare data for insertion
            records = [
                (
                    row['time'],
                    asset_id,
                    float(row['apy_percent']),
                    float(row['tvl_usd']) if pd.notna(row['tvl_usd']) else None,
                    float(row['volume_24h_usd']) if pd.notna(row.get('volume_24h_usd')) else None,  # volume_24h_usd
                    None,  # utilization_rate (Aave-specific, not in historical API)
                    None,  # volatility_24h (would need price data)
                    None   # block_number (not provided)
                )
                for _, row in df.iterrows()
            ]
            
            # Insert in batches with ON CONFLICT DO NOTHING
            insert_query = """
                INSERT INTO yield_metrics 
                (time, asset_id, apy_percent, tvl_usd, volume_24h_usd, 
                 utilization_rate, volatility_24h, block_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (time, asset_id) DO NOTHING
            """
            
            execute_batch(cur, insert_query, records, page_size=batch_size)
            conn.commit()
            
            inserted_count = cur.rowcount
            print(f"  ✓ Inserted {inserted_count} new records")
            
            cur.close()
            conn.close()
            
        except Exception as e:
            print(f"Error inserting data: {str(e)}")
            if 'conn' in locals():
                conn.rollback()
                conn.close()
    
    def backfill_asset(self, symbol: str, protocol: str, protocol_slug: str):
        """
        Complete backfill workflow for a single asset.
        
        Args:
            symbol: Asset symbol (e.g., 'USDC')
            protocol: Protocol name in our DB (e.g., 'aave_v3')
            protocol_slug: DefiLlama protocol slug (e.g., 'aave-v3')
        """
        print(f"\n{'='*60}")
        print(f"Backfilling {protocol} - {symbol}")
        print(f"{'='*60}")
        
        # 1. Get asset ID from database
        asset_id = self.get_asset_id(symbol, protocol)
        if not asset_id:
            print(f"  ✗ Asset {symbol} not found in database for {protocol}")
            return
        
        print(f"  Asset ID: {asset_id}")
        
        # 2. Find DefiLlama pool ID
        pool_id = self.find_pool_id(protocol_slug, symbol)
        if not pool_id:
            return
        
        # 3. Fetch historical data
        df = self.get_pool_history(pool_id)
        if df.empty:
            return
        
        # 4. Insert into database
        self.insert_yield_data(asset_id, df)
        
        # Small delay to be nice to the API
        time.sleep(1)
    
    def backfill_all(self):
        """
        Backfill all configured assets from Aave V3 and Curve.
        """
        print("\n" + "="*60)
        print("Starting Complete Backfill")
        print("="*60)
        
        # Aave V3 assets
        aave_assets = [
            ('USDC', 'aave_v3', 'aave-v3'),
            ('USDT', 'aave_v3', 'aave-v3'),
            ('DAI', 'aave_v3', 'aave-v3'),
            ('WETH', 'aave_v3', 'aave-v3'),
            ('WBTC', 'aave_v3', 'aave-v3'),
        ]
        
        # Curve pools
        curve_assets = [
            ('3pool', 'curve', 'curve-dex'),
            ('stETH', 'curve', 'curve-dex'),
            ('FRAX', 'curve', 'curve-dex'),
        ]
        
        # Uniswap V3 pools
        uniswap_assets = [
            ('USDC/WETH', 'uniswap_v3', 'uniswap-v3'),
            ('WBTC/WETH', 'uniswap_v3', 'uniswap-v3'),
            ('USDC/USDT', 'uniswap_v3', 'uniswap-v3'),
            ('DAI/USDC', 'uniswap_v3', 'uniswap-v3'),
        ]
        
        all_assets = aave_assets + curve_assets + uniswap_assets
        
        for i, (symbol, protocol, slug) in enumerate(all_assets, 1):
            print(f"\n[{i}/{len(all_assets)}]")
            self.backfill_asset(symbol, protocol, slug)
        
        print("\n" + "="*60)
        print("Backfill Complete!")
        print("="*60)
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print a summary of the data in the database."""
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            
            # Total records
            cur.execute("SELECT COUNT(*) FROM yield_metrics")
            total_records = cur.fetchone()[0]
            
            # Date range
            cur.execute("""
                SELECT MIN(time), MAX(time) 
                FROM yield_metrics
            """)
            min_date, max_date = cur.fetchone()
            
            # Records per protocol
            cur.execute("""
                SELECT a.protocol, COUNT(*) as count
                FROM yield_metrics ym
                JOIN assets a ON ym.asset_id = a.id
                GROUP BY a.protocol
                ORDER BY count DESC
            """)
            protocol_counts = cur.fetchall()
            
            print("\n Database Summary:")
            print(f"  Total Records: {total_records:,}")
            print(f"  Date Range: {min_date} to {max_date}")
            print(f"  Duration: {(max_date - min_date).days} days")
            print("\n  Records by Protocol:")
            for protocol, count in protocol_counts:
                print(f"    {protocol}: {count:,}")
            
            cur.close()
            conn.close()
            
        except Exception as e:
            print(f"Error generating summary: {str(e)}")


def main():
    """Main entry point for backfilling."""
    backfiller = DefiLlamaBackfiller()
    
    # Run complete backfill
    backfiller.backfill_all()


if __name__ == "__main__":
    main()
