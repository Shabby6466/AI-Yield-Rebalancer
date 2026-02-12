"""
Dune Analytics API Client
Query pre-computed metrics for DeFi protocols
"""

import logging
from typing import Dict, List, Optional, Any
import httpx
import asyncio
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class DuneClient:
    """Client for Dune Analytics API"""

    def __init__(self, api_key: str):
        """
        Initialize Dune Analytics client

        Args:
            api_key: Dune API key
        """
        self.api_key = api_key
        self.base_url = "https://api.dune.com/api/v1"
        self.timeout = httpx.Timeout(60.0)
        self.headers = {
            "X-Dune-API-Key": api_key,
            "Content-Type": "application/json",
        }
    
    async def execute_query_sql(
        self,
        query_sql: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a custom SQL query directly
        """
        url = f"{self.base_url}/query/execute"
        payload = {
            "query_sql": query_sql,
            "parameters": params or []
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url, json=payload, headers=self.headers
                )
                
                if response.status_code != 200:
                    logger.error(f"Dune API error: {response.status_code} - {response.text}")
                    return {"error": response.text, "status": response.status_code}
                
                execution_uuid = response.json().get("execution_id")
                return await self.poll_query_result(execution_uuid)
        except Exception as e:
            logger.error(f"Error executing Dune SQL query: {e}")
            return {"error": str(e)}

    async def poll_query_result(self, execution_uuid: str, poll_interval: int = 5) -> Dict[str, Any]:
        """Poll for results until the query execution is complete."""
        while True:
            result = await self.get_query_result(execution_uuid)
            state = result.get("state")
            
            if state == "QUERY_STATE_COMPLETED":
                return result
            elif state in ["QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"]:
                logger.error(f"Dune query {execution_uuid} failed with state: {state}")
                return result
            
            logger.info(f"Query {execution_uuid} is in state {state}. Retrying in {poll_interval}s...")
            await asyncio.sleep(poll_interval)

    async def get_query_result(self, execution_uuid: str) -> Dict[str, Any]:
        """Get results from a query execution (non-polling)."""
        url = f"{self.base_url}/execution/{execution_uuid}/results"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=self.headers)
            return response.json()

    async def get_aave_protocol_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Fetch real Aave metrics using SQL."""
        from src.data.dune_queries import AaveV3Queries
        sql = AaveV3Queries.get_market_rates()
        return await self.execute_query_sql(sql)

    async def get_uniswap_pool_analytics(
        self,
        pool_address: str,
        days: int = 7,
    ) -> Dict[str, Any]:
        """
        Get Uniswap pool volume, fees, and liquidity

        Args:
            pool_address: Pool contract address
            days: Number of days to analyze

        Returns:
            Pool analytics (volume, APY, fees)
        """
        logger.info(f"Fetching Uniswap pool {pool_address} analytics")

        return {
            "pool": pool_address,
            "days": days,
            "metrics": [],
        }

    async def get_curve_pool_apy(
        self,
        pool_name: str,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Get Curve pool APY and historical rates

        Args:
            pool_name: Curve pool name (e.g., '3pool', 'fraxusdc')
            days: Historical data days

        Returns:
            APY data and trends
        """
        logger.info(f"Fetching Curve {pool_name} APY")

        return {
            "pool": pool_name,
            "days": days,
            "apy_history": [],
        }

    async def get_protocol_tvl_comparison(
        self,
        protocols: List[str],
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Compare TVL across multiple protocols

        Args:
            protocols: List of protocol names
            days: Historical data days

        Returns:
            TVL comparison data
        """
        logger.info(f"Comparing TVL for {protocols}")

        return {
            "protocols": protocols,
            "days": days,
            "tvl_data": [],
        }

    async def get_whale_deposits(
        self,
        protocol: str,
        min_amount_usd: float = 1_000_000,
        days: int = 7,
    ) -> List[Dict[str, Any]]:
        """
        Get large deposits (whale activity)

        Args:
            protocol: Protocol name
            min_amount_usd: Minimum deposit amount
            days: Time window

        Returns:
            List of large deposits
        """
        logger.info(f"Fetching whale deposits for {protocol}")

        return []

    async def get_tvl_trends(
        self,
        protocol: str,
        days: int = 90,
    ) -> Dict[str, Any]:
        """
        Get TVL trends and anomalies

        Args:
            protocol: Protocol name
            days: Historical data days

        Returns:
            TVL trend data
        """
        logger.info(f"Fetching TVL trends for {protocol}")

        return {
            "protocol": protocol,
            "days": days,
            "trends": [],
        }

    async def get_volatility_metrics(
        self,
        assets: List[str],
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Get APY volatility metrics

        Args:
            assets: List of asset symbols
            days: Historical data days

        Returns:
            Volatility metrics
        """
        logger.info(f"Calculating volatility for {assets}")

        return {
            "assets": assets,
            "days": days,
            "volatility": {},
        }


class DuneQueryBuilder:
    """Build custom SQL queries for Dune"""

    @staticmethod
    def aave_reserves_query(limit: int = 100) -> str:
        """Query Aave reserves with APY and liquidity"""
        return f"""
        SELECT
            reserve_symbol,
            reserve_address,
            borrow_rate_stable,
            borrow_rate_variable,
            supply_rate,
            total_borrows,
            available_liquidity,
            liquidity_rate,
            (total_borrows / (total_borrows + available_liquidity)) as utilization,
            evt_block_time
        FROM aave_v3."Reserve"
        WHERE blockchain = 'ethereum'
        ORDER BY available_liquidity DESC
        LIMIT {limit}
        """

    @staticmethod
    def uniswap_pools_query(limit: int = 100) -> str:
        """Query Uniswap V3 pools with volumes and fees"""
        return f"""
        SELECT
            pool,
            token0_symbol,
            token1_symbol,
            fee,
            liquidity,
            sqrt_price_x96,
            volume_token0_24h,
            volume_token1_24h,
            fees_earned_token0_24h,
            fees_earned_token1_24h,
            tvl_eth,
            tvl_usd
        FROM uniswap_v3."pools"
        WHERE blockchain = 'ethereum'
            AND tvl_usd > 100000
        ORDER BY tvl_usd DESC
        LIMIT {limit}
        """

    @staticmethod
    def curve_pools_query(limit: int = 50) -> str:
        """Query Curve pools with APY and volumes"""
        return f"""
        SELECT
            pool_name,
            pool_address,
            coins,
            balances,
            total_supply,
            admin_fee,
            fee,
            virtual_price,
            apy,
            apy_base,
            apy_incentive,
            volume_24h,
            volume_7d,
            tvl
        FROM curve."pools"
        WHERE blockchain = 'ethereum'
        ORDER BY tvl DESC
        LIMIT {limit}
        """

    @staticmethod
    def yield_comparison_query() -> str:
        """Compare yields across Aave, Curve, Uniswap"""
        return """
        WITH aave_data AS (
            SELECT
                'aave' as protocol,
                reserve_symbol as asset,
                supply_rate as apy,
                available_liquidity as liquidity,
                evt_block_time as timestamp
            FROM aave_v3."Reserve"
            WHERE blockchain = 'ethereum'
        ),
        curve_data AS (
            SELECT
                'curve' as protocol,
                pool_name as asset,
                apy,
                tvl as liquidity,
                evt_block_time as timestamp
            FROM curve."pools"
            WHERE blockchain = 'ethereum'
        )
        SELECT
            protocol,
            asset,
            apy,
            liquidity,
            timestamp
        FROM aave_data
        UNION ALL
        SELECT * FROM curve_data
        ORDER BY timestamp DESC, apy DESC
        """


# ============================================================================
# Convenience Functions
# ============================================================================


async def fetch_protocol_metrics(
    api_key: str,
) -> Dict[str, Any]:
    """
    Fetch key metrics from all protocols

    Args:
        api_key: Dune API key

    Returns:
        Protocol metrics
    """
    client = DuneClient(api_key)

    try:
        aave, uniswap, curve = await asyncio.gather(
            client.get_aave_protocol_metrics(days=7),
            client.get_uniswap_pool_analytics("0x...", days=7),
            client.get_curve_pool_apy("3pool", days=7),
            return_exceptions=True,
        )

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "aave": aave if not isinstance(aave, Exception) else {},
            "uniswap": uniswap if not isinstance(uniswap, Exception) else {},
            "curve": curve if not isinstance(curve, Exception) else {},
        }
    except Exception as e:
        logger.error(f"Error fetching protocol metrics: {e}")
        raise


if __name__ == "__main__":
    # Test script
    import json
    import os
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.getenv("DUNE_API_KEY", "")

    async def test():
        if not api_key:
            print("❌ DUNE_API_KEY not set in .env")
            return

        data = await fetch_protocol_metrics(api_key)
        print(json.dumps(data, indent=2, default=str))

    asyncio.run(test())
