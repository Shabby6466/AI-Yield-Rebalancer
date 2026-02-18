"""
The Graph GraphQL Client
Queries official subgraphs for Aave V3, Uniswap V3, and Curve Finance
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional, Any
import aiohttp
from datetime import datetime

logger = logging.getLogger(__name__)


class GraphClient:
    """Client for querying The Graph subgraphs"""

    def __init__(self, api_key: Optional[str] = None, timeout: int = 30):
        self.timeout = timeout
        self.api_key = api_key or os.getenv("GRAPH_API_KEY", "")
        
        # Using The Graph Studio subgraph endpoints (official deployments)
        # These are production-ready subgraphs deployed on mainnet
        self.subgraphs = {
            # Aave V3 Ethereum - Official Aave Protocol subgraph
            # Using Aave's native schema (reserves, not markets)
            "aave": f"https://gateway.thegraph.com/api/{self.api_key}/subgraphs/id/AUkKNd4Gh2DsHMWyJGh7tMjE6Nav2ttjiiVx54zwYodk",
            
            # Uniswap V3 Ethereum subgraph (official) - WORKING ✅
            # https://thegraph.com/explorer/subgraphs/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV
            "uniswap": f"https://gateway.thegraph.com/api/{self.api_key}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
            
            # Curve Finance - Convex Finance subgraph (has Curve data)
            # Alternative for Curve pools information
            "curve": f"https://gateway.thegraph.com/api/{self.api_key}/subgraphs/id/Dnwwqb3gKBk7J1DyhxQqAyJyevbcwqmQNETifARPKdgK",
        }

    async def query(
        self,
        subgraph: str,
        query_string: str,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a GraphQL query against a subgraph

        Args:
            subgraph: Subgraph name ('aave', 'uniswap', 'curve')
            query_string: GraphQL query string
            variables: Query variables

        Returns:
            Query result data
        """
        if subgraph not in self.subgraphs:
            raise ValueError(f"Unknown subgraph: {subgraph}")

        url = self.subgraphs[subgraph]
        payload = {"query": query_string, "variables": variables or {}}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    data = await response.json()

                    if "errors" in data:
                        logger.error(
                            f"GraphQL error for {subgraph}: {data['errors']}"
                        )
                        raise Exception(f"GraphQL error: {data['errors']}")

                    return data.get("data", {})
        except asyncio.TimeoutError:
            logger.error(f"Timeout querying {subgraph}")
            raise
        except Exception as e:
            logger.error(f"Error querying {subgraph}: {e}")
            raise

    # =========================================================================
    # Aave V3 Queries
    # =========================================================================

    async def get_aave_reserves(self, skip: int = 0, first: int = 100) -> List[Dict]:
        """
        Get Aave lending pool reserves (USDC, DAI, USDT, etc)

        Returns:
            List of reserve data with APY, liquidity, utilization
        """
        query = """
        query GetAaveReserves($skip: Int!, $first: Int!) {
            reserves(
                skip: $skip
                first: $first
                orderBy: liquidityRate
                orderDirection: desc
                where: { isActive: true }
            ) {
                id
                symbol
                decimals
                liquidityRate
                variableBorrowRate
                stableBorrowRate
                totalLiquidity
                availableLiquidity
                utilizationRate
                lastUpdateTimestamp
            }
        }
        """
        result = await self.query(
            "aave", query, {"skip": skip, "first": first}
        )
        return result.get("reserves", [])

    async def get_aave_protocol_data(self) -> Dict:
        """Get overall Aave protocol metrics"""
        query = """
        query GetAaveProtocol {
            lendingProtocol(id: "1") {
                id
                methodologyVersion
                schemaVersion
                subgraphVersion
                totalDepositBased
                totalBorrowBased
                totalValueLockedUSD
                cumulativeDepositUSD
                cumulativeBorrowUSD
                cumulativeWithdrawUSD
                cumulativeRepayUSD
            }
        }
        """
        return await self.query("aave", query)

    # =========================================================================
    # Uniswap V3 Queries
    # =========================================================================

    async def get_uniswap_pools(
        self, skip: int = 0, first: int = 100
    ) -> List[Dict]:
        """
        Get Uniswap V3 pools sorted by liquidity

        Returns:
            List of pool data with liquidity, volumes, fees
        """
        query = """
        query GetUniswapPools($skip: Int!, $first: Int!) {
            pools(
                skip: $skip
                first: $first
                orderBy: liquidity
                orderDirection: desc
                where: { liquidity_gt: "0" }
            ) {
                id
                token0 {
                    id
                    symbol
                    decimals
                }
                token1 {
                    id
                    symbol
                    decimals
                }
                feeTier
                liquidity
                sqrtPrice
                tick
                volumeUSD
                volumeToken0
                volumeToken1
                feesUSD
                totalValueLockedUSD
                txCount
                createdAtTimestamp
            }
        }
        """
        result = await self.query(
            "uniswap", query, {"skip": skip, "first": first}
        )
        return result.get("pools", [])

    async def get_uniswap_token_data(self, token_id: str) -> Dict:
        """Get data for a specific token on Uniswap V3"""
        query = """
        query GetUniswapToken($id: ID!) {
            token(id: $id) {
                id
                symbol
                name
                decimals
                totalSupply
                totalValueLockedUSD
                totalValueLockedUSDUntracked
                volume
                volumeUSD
                feesUSD
                txCount
                poolCount
            }
        }
        """
        return await self.query("uniswap", query, {"id": token_id})

    async def get_uniswap_pool_day_data(self, pool_id: str) -> Dict:
        """Get 24h metrics for a specific pool"""
        query = """
        query GetPoolDayData($pool: String!) {
            poolDayDatas(
                first: 1
                orderBy: date
                orderDirection: desc
                where: { pool: $pool }
            ) {
                date
                volumeUSD
                feesUSD
                tvlUSD
                liquidity
            }
        }
        """
        return await self.query("uniswap", query, {"pool": pool_id})

    async def get_uniswap_protocol_data(self) -> Dict:
        """Get overall Uniswap V3 protocol metrics"""
        query = """
        query GetUniswapProtocol {
            factories(first: 1) {
                id
                poolCount
                txCount
                totalVolumeUSD
                totalValueLockedUSD
                totalFeesUSD
            }
            uniswapDayDatas(first: 1, orderBy: date, orderDirection: desc) {
                date
                volumeUSD
                feesUSD
                tvlUSD
            }
        }
        """
        return await self.query("uniswap", query)

    # =========================================================================
    # Curve Finance Queries
    # =========================================================================

    async def get_curve_pools(self, skip: int = 0, first: int = 100) -> List[Dict]:
        """
        Get Curve Finance pools with liquidity data

        Returns:
            List of pool data with volumes, APY, TVL
        """
        query = """
        query GetCurvePools($skip: Int!, $first: Int!) {
            liquidityPools(
                skip: $skip
                first: $first
                orderBy: totalValueLockedUSD
                orderDirection: desc
            ) {
                id
                name
                symbol
                inputTokens {
                    id
                    symbol
                    decimals
                }
                outputTokens {
                    id
                    symbol
                    decimals
                }
                totalValueLockedUSD
                cumulativeSupplySideRevenueUSD
                cumulativeProtocolSideRevenueUSD
                cumulativeVolumeUSD
                stakedOutputTokenAmount
                openPositionCount
                closedPositionCount
                baseYield
            }
        }
        """
        result = await self.query(
            "curve", query, {"skip": skip, "first": first}
        )
        return result.get("liquidityPools", [])

    async def get_curve_pool_detail(self, pool_id: str) -> Dict:
        """Get detailed data for a specific Curve pool"""
        query = """
        query GetCurvePool($id: ID!) {
            liquidityPool(id: $id) {
                id
                name
                symbol
                inputTokens {
                    id
                    symbol
                    decimals
                    lastPriceUSD
                }
                outputTokens {
                    id
                    symbol
                    decimals
                }
                totalValueLockedUSD
                totalVolumeUSD
                totalDepositCount
                totalWithdrawCount
                totalSwapCount
                dailySnapshots(first: 1, orderBy: timestamp, orderDirection: desc) {
                    id
                    timestamp
                    totalValueLockedUSD
                    cumulativeVolumeUSD
                    dailyVolumeUSD
                    dailySupplySideRevenueUSD
                }
                hourlySnapshots(first: 1, orderBy: timestamp, orderDirection: desc) {
                    id
                    timestamp
                    totalValueLockedUSD
                    cumulativeVolumeUSD
                    hourlyVolumeUSD
                }
            }
        }
        """
        return await self.query("curve", query, {"id": pool_id})

    async def get_curve_protocol_data(self) -> Dict:
        """Get overall Curve protocol metrics"""
        query = """
        query GetCurveProtocol {
            protocols(first: 1) {
                id
                name
                type
                totalValueLockedUSD
                cumulativeVolumeUSD
                cumulativeSupplySideRevenueUSD
                cumulativeProtocolSideRevenueUSD
            }
        }
        """
        return await self.query("curve", query)


# ============================================================================
# Convenience Functions
# ============================================================================


async def fetch_all_protocol_data() -> Dict[str, Any]:
    """
    Fetch data from all three protocols in parallel

    Returns:
        Dictionary with data from Aave, Uniswap, and Curve
    """
    client = GraphClient()

    try:
        results = await asyncio.gather(
            client.get_aave_reserves(),
            client.get_uniswap_pools(),
            client.get_curve_pools(),
            client.get_aave_protocol_data(),
            client.get_uniswap_protocol_data(),
            client.get_curve_protocol_data(),
            return_exceptions=True,
        )

        aave_reserves, uniswap_pools, curve_pools, aave_protocol, uniswap_protocol, curve_protocol = results

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "aave": {
                "reserves": aave_reserves if not isinstance(aave_reserves, Exception) else [],
                "protocol": aave_protocol if not isinstance(aave_protocol, Exception) else {},
            },
            "uniswap": {
                "pools": uniswap_pools if not isinstance(uniswap_pools, Exception) else [],
                "protocol": uniswap_protocol if not isinstance(uniswap_protocol, Exception) else {},
            },
            "curve": {
                "pools": curve_pools if not isinstance(curve_pools, Exception) else [],
                "protocol": curve_protocol if not isinstance(curve_protocol, Exception) else {},
            },
        }
    except Exception as e:
        logger.error(f"Error fetching protocol data: {e}")
        raise


if __name__ == "__main__":
    # Test script
    import json

    async def test():
        data = await fetch_all_protocol_data()
        print(json.dumps(data, indent=2, default=str))

    asyncio.run(test())
