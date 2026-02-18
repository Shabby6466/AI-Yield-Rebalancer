
import asyncio
import os
import logging
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import execute_values
from src.data.defillama_client import DefiLlamaClient
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def backfill_history(days: int = 60):
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not found.")
        return

    client = DefiLlamaClient()
    conn = psycopg2.connect(db_url)
    
    try:
        # 1. Get target pools (High TVL Stablecoin pools)
        logger.info("Fetching top stablecoin pools...")
        pools = await client.fetch_stablecoin_pools(min_tvl=5_000_000)
        logger.info(f"Found {len(pools)} candidate pools.")
        
        from datetime import timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        total_inserted = 0
        
        # 2. Iterate and fetch history
        for i, pool in enumerate(pools):
            pool_id = pool['pool']
            symbol = pool['symbol']
            project = pool['project']
            
            logger.info(f"[{i+1}/{len(pools)}] Fetching history for {symbol} ({project})...")
            
            try:
                history = await client.fetch_historical_yield(pool_id)
                
                # Prepare batch
                records = []
                for point in history:
                    # Parse date. Expected format depends on API, usually ISO or timestamp
                    # API usually returns 'date': "2024-03-20T00:00:00.000Z" or similar
                    try:
                        # Handle timestamp key
                        ts = point.get('timestamp')
                        if not ts:
                            # Fallback if structure changes
                            ts = point.get('date')
                            
                        if isinstance(ts, str):
                            # format: 2024-03-20T00:00:00.000Z
                            # robustly handle potential Z or missing TZ
                            if 'Z' in ts:
                                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                            else:
                                dt = datetime.fromisoformat(ts)
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                        else:
                            # Unix timestamp
                            dt = datetime.fromtimestamp(ts, timezone.utc) if isinstance(ts, (int, float)) else datetime.now(timezone.utc)
                        
                        # Ensure dt is aware before comparison
                        if dt.tzinfo is None:
                             dt = dt.replace(tzinfo=timezone.utc)

                        # DEBUG: Check for naive/aware mismatch
                        if dt.tzinfo is None or cutoff.tzinfo is None:
                            logger.error(f"TZ ERROR: dt={dt} (tz={dt.tzinfo}), cutoff={cutoff} (tz={cutoff.tzinfo})")
                        
                        if dt < cutoff:
                            continue
                            
                        records.append((
                            dt,
                            pool_id,
                            project,
                            symbol,
                            float(point.get('apy', 0)),
                            float(point.get('apyBase', 0) or 0),
                            float(point.get('apyReward', 0) or 0),
                            float(point.get('tvlUsd', 0)),
                            point.get('ilRisk', 'no'),
                            False # is_outlier assume false for history
                        ))
                    except Exception as e:
                        logger.warning(f"Error parsing point for {symbol}: {e}")
                        continue
                
                if records:
                    with conn.cursor() as cur:
                        query = """
                            INSERT INTO yield_snapshots 
                            (time, pool_id, protocol, symbol, apy_total, apy_base, apy_reward, tvl_usd, il_risk, is_outlier)
                            VALUES %s
                            ON CONFLICT (time, pool_id) DO NOTHING;
                        """
                        execute_values(cur, query, records)
                    conn.commit()
                    total_inserted += len(records)
                    logger.info(f" -> Inserted {len(records)} days of data.")
                else:
                    logger.info(" -> No recent data found.")
                    
                # Rate limit gentleness
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Failed to process {symbol}: {e}")
                
        logger.info(f"Backfill complete! Total records inserted: {total_inserted}")
        
    finally:
        conn.close()
        await client.close()

if __name__ == "__main__":
    asyncio.run(backfill_history())
