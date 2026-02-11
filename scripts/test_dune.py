"""
Test Dune Analytics Integration for Aave V3 and Curve Finance
"""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.dune_client import DuneClient
from src.data.dune_queries import AaveV3Queries, CurveFinanceQueries

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s"
)
logger = logging.getLogger(__name__)


async def test_dune_client():
    """Test Dune Analytics queries for Aave and Curve"""
    load_dotenv()
    
    api_key = os.getenv("DUNE_API_KEY")
    if not api_key:
        logger.error("❌ DUNE_API_KEY not found in .env")
        return
    
    logger.info("\n" + "="*80)
    logger.info("TESTING DUNE ANALYTICS CLIENT")
    logger.info("="*80 + "\n")
    
    client = DuneClient(api_key)
    
    # Test 1: Try to execute a SQL query directly (may require paid plan)
    logger.info(" Testing Dune API with Aave V3 query...")
    try:
        aave_query = AaveV3Queries.get_market_rates()
        logger.info("   Query SQL length: {} chars".format(len(aave_query)))
        
        result = await client.execute_query_sql(aave_query)
        
        if "execution_id" in result:
            execution_id = result["execution_id"]
            logger.info(f"✅ Query submitted! Execution ID: {execution_id}")
            
            # Wait for execution
            logger.info("⏳ Waiting for query execution (5 seconds)...")
            await asyncio.sleep(5)
            
            # Get results
            query_result = await client.get_query_result(execution_id)
            
            if "result" in query_result and "rows" in query_result["result"]:
                rows = query_result["result"]["rows"]
                logger.info(f"✅ Aave V3 data retrieved: {len(rows)} markets")
                
                # Show top 3 markets
                for i, row in enumerate(rows[:3]):
                    symbol = row.get("asset_symbol", "Unknown")
                    apy = row.get("supply_apy", 0)
                    tvl = row.get("total_liquidity", 0)
                    logger.info(f"   {i+1}. {symbol}: {apy:.2f}% APY, ${tvl:,.0f} liquidity")
                    
            elif "error" in query_result:
                logger.warning(f"⚠️  Query error: {query_result['error']}")
            elif "state" in query_result:
                logger.info(f"   Query state: {query_result['state']}")
                
        elif "error" in result:
            logger.warning(f"⚠️  API returned error: {result['error']}")
            if "paid" in str(result.get("error", "")).lower() or "credits" in str(result.get("error", "")).lower():
                logger.info("\n💡 Direct SQL execution requires Dune paid plan.")
                logger.info("   Alternative approach: Create queries at dune.com")
                
    except Exception as e:
        logger.error(f"❌ Query execution error: {e}")
    
    # Test 2: Try Curve query
    logger.info("\n Testing Curve Finance query...")
    try:
        curve_query = CurveFinanceQueries.get_pool_apys()
        logger.info("   Query SQL length: {} chars".format(len(curve_query)))
        
        result = await client.execute_query_sql(curve_query)
        
        if "execution_id" in result:
            logger.info(f"✅ Query submitted! Execution ID: {result['execution_id']}")
        elif "error" in result:
            logger.warning(f"⚠️  API returned: {result.get('status')} - Limited by plan")
            
    except Exception as e:
        logger.error(f"❌ Curve query error: {e}")
    
    # Summary
    logger.info("\n" + "="*80)
    logger.info("DUNE ANALYTICS SETUP GUIDE")
    logger.info("="*80)
    logger.info("\n📝 To use Dune for Aave V3 and Curve data:")
    logger.info("\n   OPTION 1: Create Saved Queries (Free/Paid)")
    logger.info("   -" + "-"*50)
    logger.info("   1. Go to https://dune.com/queries")
    logger.info("   2. Create new query with SQL from src/data/dune_queries.py")
    logger.info("   3. Save query and note the Query ID")
    logger.info("   4. Add to .env: DUNE_AAVE_QUERY_ID=123456")
    logger.info("   5. Use: await client.get_latest_results(query_id)")
    logger.info("\n   OPTION 2: Direct SQL Execution (Paid Plan Only)")
    logger.info("   -" + "-"*50)
    logger.info("   • Use: await client.execute_query_sql(sql_string)")
    logger.info("   • Requires Dune API credits")
    logger.info("\n   RECOMMENDED: Option 1 with scheduled query refreshes")


if __name__ == "__main__":
    asyncio.run(test_dune_client())
