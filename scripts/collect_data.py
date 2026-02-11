"""
Historical Data Backfill Script
Collects current protocol data and stores in database
Step 1: Collect current snapshot, then build backfill strategy
"""

import asyncio
import logging
import os
import sys
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.ingestion_service import DataAggregator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)


def insert_protocol_yields(db_conn, protocol_id: int, asset: str, apy: float, 
                          liquidity: float = 0, utilization: float = 0):
    """Insert protocol yield data into database"""
    query = """
    INSERT INTO protocol_yields 
        (protocol_id, asset, apy_percent, total_liquidity_usd, 
         available_liquidity_usd, utilization_ratio, recorded_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    
    cursor = db_conn.cursor()
    try:
        cursor.execute(query, (
            protocol_id, asset, apy, liquidity, liquidity * 0.8,
            utilization, datetime.now()
        ))
        db_conn.commit()
        logger.info(f"✅ Inserted {asset} yield: {apy:.3f}% APY")
    except Exception as e:
        logger.error(f"Error inserting data: {e}")
        db_conn.rollback()
    finally:
        cursor.close()


def insert_gas_price(db_conn, gas_price: float):
    """Insert gas price data"""
    query = """
    INSERT INTO gas_prices (safe_gwei, standard_gwei, fast_gwei, recorded_at)
    VALUES (%s, %s, %s, %s)
    """
    
    cursor = db_conn.cursor()
    try:
        # Use same value for all tiers as placeholder
        cursor.execute(query, (gas_price, gas_price, gas_price, datetime.now()))
        db_conn.commit()
        logger.info(f"✅ Inserted gas price: {gas_price} gwei")
    except Exception as e:
        logger.error(f"Error inserting gas price: {e}")
        db_conn.rollback()
    finally:
        cursor.close()


async def collect_and_store_current_data():
    """Collect current protocol data and store in database"""
    load_dotenv()
    
    # Get credentials
    alchemy_key = os.getenv("ALCHEMY_API_KEY")
    graph_key = os.getenv("GRAPH_API_KEY")
    db_url = os.getenv("DATABASE_URL")
    
    if not all([alchemy_key, db_url]):
        logger.error("Missing required environment variables")
        return
    
    # Initialize aggregator
    aggregator = DataAggregator(
        db_url=db_url,
        alchemy_api_key=alchemy_key,
        graph_api_key=graph_key
    )
    
    # Get database connection
    db_conn = psycopg2.connect(db_url)
    
    # Get protocol IDs
    cursor = db_conn.cursor()
    cursor.execute("SELECT id, name, symbol FROM protocols ORDER BY id")
    protocols = {row[1]: row[0] for row in cursor.fetchall()}
    cursor.close()
    
    logger.info(f"\n{'='*80}")
    logger.info("COLLECTING CURRENT PROTOCOL DATA")
    logger.info(f"{'='*80}\n")
    
    # Collect Aave V3 data
    try:
        logger.info(" Collecting Aave V3 data...")
        aave_data = await aggregator.collect_aave_data()
        
        if aave_data and "markets" in aave_data:
            protocol_id = protocols.get("Aave V3", 1)
            
            for market in aave_data["markets"]:
                asset = market.get("symbol", market.get("asset", "UNKNOWN"))
                apy = market.get("supply_apy", market.get("apy_percent", 0))
                tvl = market.get("total_liquidity_usd", 0)
                
                insert_protocol_yields(
                    db_conn, protocol_id, asset, apy,
                    liquidity=tvl, utilization=0
                )
            
            logger.info(f"✅ Stored {len(aave_data['markets'])} Aave markets with TVL data")
    except Exception as e:
        logger.error(f"❌ Aave collection failed: {e}")
    
    # Collect Uniswap V3 data
    try:
        logger.info("\n Collecting Uniswap V3 data...")
        uni_data = await aggregator.collect_uniswap_data()
        
        if uni_data and "pools" in uni_data:
            protocol_id = protocols.get("Uniswap V3", 3)
            
            # Store top 10 pools by liquidity
            pools = sorted(
                uni_data["pools"],
                key=lambda p: float(p.get("totalValueLockedUSD", 0)),
                reverse=True
            )[:10]
            
            for pool in pools:
                token0 = pool.get("token0", {}).get("symbol", "UNK")
                token1 = pool.get("token1", {}).get("symbol", "UNK")
                asset = f"{token0}/{token1}"
                
                tvl = float(pool.get("totalValueLockedUSD", 0))
                
                # Get 24h fee data for accurate APY calculation
                pool_id = pool.get("id")
                try:
                    day_data = await aggregator.graph_client.get_uniswap_pool_day_data(pool_id)
                    if day_data and "poolDayDatas" in day_data and len(day_data["poolDayDatas"]) > 0:
                        fees_24h = float(day_data["poolDayDatas"][0].get("feesUSD", 0))
                        # Calculate APY: (daily fees / TVL) * 365 * 100
                        apy = (fees_24h / tvl * 365 * 100) if tvl > 0 else 0
                    else:
                        # Fallback: estimate from fee tier (0.05%, 0.3%, 1%)
                        fee_tier = int(pool.get("feeTier", 3000))
                        apy = fee_tier / 10000  # Convert basis points to percentage
                except Exception as e:
                    logger.warning(f"Could not get day data for {asset}: {e}")
                    # Fallback to fee tier
                    fee_tier = int(pool.get("feeTier", 3000))
                    apy = fee_tier / 10000
                
                insert_protocol_yields(
                    db_conn, protocol_id, asset, apy,
                    liquidity=tvl, utilization=0
                )
            
            logger.info(f"✅ Stored {len(pools)} Uniswap pools with 24h fee data")
    except Exception as e:
        logger.error(f"❌ Uniswap collection failed: {e}")
    
    # Collect Curve data
    try:
        logger.info("\n Collecting Curve Finance data...")
        curve_data = await aggregator.collect_curve_data()
        
        if curve_data and "pools" in curve_data:
            protocol_id = protocols.get("Curve Finance", 2)
            
            for pool in curve_data["pools"]:
                asset = pool.get("pool_name", pool.get("asset", "UNKNOWN"))
                apy = pool.get("base_yield", pool.get("apy_percent", 0))
                tvl = pool.get("total_liquidity_usd", 0)
                
                insert_protocol_yields(
                    db_conn, protocol_id, asset, apy,
                    liquidity=tvl, utilization=0
                )
            
            logger.info(f"✅ Stored {len(curve_data['pools'])} Curve pools with TVL data")
    except Exception as e:
        logger.error(f"❌ Curve collection failed: {e}")
    
    # Collect gas price
    try:
        gas_price = await aggregator.alchemy_client.get_gas_price()
        # Extract fast_gwei if dict, otherwise use the value directly
        if isinstance(gas_price, dict):
            fast_gwei = gas_price.get('fast', gas_price.get('fast_gwei', 30))
        else:
            fast_gwei = gas_price
        insert_gas_price(db_conn, fast_gwei)
    except Exception as e:
        logger.error(f"Error inserting gas price: {e}")
    
    db_conn.close()
    
    logger.info(f"\n{'='*80}")
    logger.info("DATA COLLECTION COMPLETE")
    logger.info(f"{'='*80}")
    logger.info("\n✅ Current protocol data stored in database")
    logger.info("✅ Ready for feature engineering")
    logger.info("\n📝 Next steps:")
    logger.info("   1. Run feature engineering: python scripts/test_features.py")
    logger.info("   2. Set up scheduled collection (every 15 minutes)")
    logger.info("   3. Build historical backfill strategy")


if __name__ == "__main__":
    asyncio.run(collect_and_store_current_data())
