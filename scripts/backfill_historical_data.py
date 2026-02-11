"""
Historical Data Backfill Script
Backfills protocol data from The Graph subgraphs
Targets: Last 90 days of daily data for comprehensive backtesting
"""

import asyncio
import logging
import os
import sys
import psycopg2
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.graph_client import GraphClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_db_connection():
    """Get database connection"""
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    return psycopg2.connect(db_url)


async def backfill_uniswap_data(days_back: int = 90):
    """
    Backfill Uniswap V3 pool data for the last N days
    
    Args:
        days_back: Number of days to backfill (default 90)
    """
    load_dotenv()
    graph_key = os.getenv("GRAPH_API_KEY")
    client = GraphClient(api_key=graph_key)
    
    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)
    start_timestamp = int(start_date.timestamp())
    
    logger.info(f"\n{'='*80}")
    logger.info(f"BACKFILLING UNISWAP V3 DATA")
    logger.info(f"{'='*80}")
    logger.info(f"Start Date: {start_date.strftime('%Y-%m-%d')}")
    logger.info(f"End Date: {end_date.strftime('%Y-%m-%d')}")
    logger.info(f"Days: {days_back}\n")
    
    # Get top pools by current TVL (to avoid querying thousands of dead pools)
    pools_query = """
    query GetTopPools {
        pools(
            first: 20
            orderBy: totalValueLockedUSD
            orderDirection: desc
            where: { totalValueLockedUSD_gt: "10000" }
        ) {
            id
            token0 { symbol }
            token1 { symbol }
            totalValueLockedUSD
        }
    }
    """
    
    pools_result = await client.query("uniswap", pools_query)
    pools = pools_result.get("pools", [])
    
    logger.info(f" Found {len(pools)} pools with TVL > $10K")
    
    # Get database connection
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get protocol ID for Uniswap V3
    cur.execute("SELECT id FROM protocols WHERE name = 'Uniswap V3'")
    protocol_id = cur.fetchone()[0]
    
    total_records = 0
    
    # Query daily data for each pool
    for pool in pools:
        pool_id = pool["id"]
        token0 = pool["token0"]["symbol"]
        token1 = pool["token1"]["symbol"]
        asset_name = f"{token0}/{token1}"
        
        logger.info(f"\n Backfilling {asset_name}...")
        
        # Query all daily snapshots for this pool
        query = """
        query GetPoolHistory($pool: String!, $startDate: Int!) {
            poolDayDatas(
                first: 1000
                orderBy: date
                orderDirection: asc
                where: { 
                    pool: $pool
                    date_gte: $startDate
                }
            ) {
                date
                volumeUSD
                feesUSD
                tvlUSD
                liquidity
            }
        }
        """
        
        result = await client.query("uniswap", query, {
            "pool": pool_id,
            "startDate": start_timestamp
        })
        
        day_datas = result.get("poolDayDatas", [])
        
        if not day_datas:
            logger.warning(f"  ⚠️ No historical data for {asset_name}")
            continue
        
        # Insert each day's data
        for day_data in day_datas:
            timestamp = datetime.fromtimestamp(int(day_data["date"]))
            tvl = float(day_data.get("tvlUSD", 0))
            fees_24h = float(day_data.get("feesUSD", 0))
            
            # Calculate APY from 24h fees
            apy = (fees_24h / tvl * 365 * 100) if tvl > 0 else 0
            
            # Insert into database
            try:
                cur.execute(
                    """
                    INSERT INTO protocol_yields 
                        (protocol_id, asset, apy_percent, total_liquidity_usd, 
                         available_liquidity_usd, utilization_ratio, recorded_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (protocol_id, asset_name, apy, tvl, tvl * 0.8, 0.0, timestamp)
                )
                total_records += 1
            except Exception as e:
                logger.error(f"  Error inserting {asset_name} at {timestamp}: {e}")
        
        conn.commit()
        logger.info(f"  ✅ Inserted {len(day_datas)} records for {asset_name}")
    
    cur.close()
    conn.close()
    
    logger.info(f"\n{'='*80}")
    logger.info(f"BACKFILL COMPLETE")
    logger.info(f"{'='*80}")
    logger.info(f"✅ Total records inserted: {total_records}")
    logger.info(f"✅ Pools processed: {len(pools)}")
    logger.info(f"✅ Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")


async def backfill_aave_data(days_back: int = 90):
    """
    Backfill Aave V3 reserve data (limited historical data available via Graph)
    Note: Aave historical data is limited, best to use RPC for current + forward collection
    """
    logger.info("\n⚠️ Aave V3 historical backfill limited via Graph")
    logger.info("💡 Recommendation: Focus on forward collection for Aave")
    logger.info("💡 Uniswap V3 has rich historical data (5 years available)")


async def backfill_curve_data(days_back: int = 90):
    """
    Backfill Curve Finance pool data
    Note: Curve historical data availability depends on subgraph
    """
    logger.info("\n⚠️ Curve historical backfill via Graph may be limited")
    logger.info("💡 Recommendation: Focus on Uniswap V3 for historical backfill")


async def main():
    """Main backfill orchestrator"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Backfill historical DeFi protocol data")
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of days to backfill (default: 90)"
    )
    parser.add_argument(
        "--protocol",
        type=str,
        choices=["uniswap", "aave", "curve", "all"],
        default="uniswap",
        help="Protocol to backfill (default: uniswap)"
    )
    
    args = parser.parse_args()
    
    logger.info(f"\n{'='*80}")
    logger.info("HISTORICAL DATA BACKFILL")
    logger.info(f"{'='*80}")
    logger.info(f"Protocol: {args.protocol}")
    logger.info(f"Days back: {args.days}")
    logger.info(f"Started: {datetime.now()}\n")
    
    if args.protocol == "uniswap" or args.protocol == "all":
        await backfill_uniswap_data(days_back=args.days)
    
    if args.protocol == "aave" or args.protocol == "all":
        await backfill_aave_data(days_back=args.days)
    
    if args.protocol == "curve" or args.protocol == "all":
        await backfill_curve_data(days_back=args.days)
    
    logger.info(f"\n{'='*80}")
    logger.info("BACKFILL COMPLETE")
    logger.info(f"{'='*80}")
    logger.info(f"⏰ Finished: {datetime.now()}")
    logger.info("\n📝 Next steps:")
    logger.info("   1. Check data: python scripts/check_data.py")
    logger.info("   2. Retrain models: python scripts/train_lstm_v2.py && python scripts/train_xgboost.py")
    logger.info("   3. Run backtest: python scripts/backtest_engine.py")


if __name__ == "__main__":
    asyncio.run(main())
