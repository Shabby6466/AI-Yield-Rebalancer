"""
DeFi Llama API Client
Fetches live APY data for lending protocols and liquidity pools from DefiLlama
"""

import asyncio
import logging
import aiohttp
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class DefiLlamaClient:
    """Client for querying DeFi Llama Yields API"""

    BASE_URL = "https://yields.llama.fi"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    async def _get(self, endpoint: str) -> Any:
        """
        Execute a GET request to DeFi Llama API
        
        Args:
            endpoint: API endpoint (e.g., '/pools')
            
        Returns:
            JSON response data
        """
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status != 200:
                        logger.error(f"Error fetching {url}: {response.status}")
                        response.raise_for_status()
                        
                    return await response.json()
        except asyncio.TimeoutError:
            logger.error(f"Timeout querying {url}")
            raise
        except Exception as e:
            logger.error(f"Error querying {url}: {e}")
            raise

    async def fetch_all_yields(self) -> List[Dict]:
        """
        Fetch yields for ALL pools tracked by DeFi Llama
        
        Returns:
            List of pool dictionary objects
        """
        response = await self._get("/pools")
        return response.get("data", [])

    async def fetch_pool_yields(self, pool_ids: List[str]) -> List[Dict]:
        """
        Fetch yields for specific pool IDs
        
        Args:
            pool_ids: List of DeFi Llama pool UUIDs
            
        Returns:
            List of filtered pool data
        """
        all_pools = await self.fetch_all_yields()
        
        # Filter pools by ID
        # pool['pool'] contains the UUID (e.g. "747c1d2a-c668-4682-b9f9-296708a3dd90")
        filtered_pools = [
            pool for pool in all_pools 
            if pool.get("pool") in pool_ids
        ]
        
        if not filtered_pools:
            logger.warning(f"No pools found for IDs: {pool_ids}")

        return filtered_pools

    async def fetch_historical_yield(self, pool_id: str) -> List[Dict]:
        """
        Fetch historical APY data for a specific pool
        
        Args:
            pool_id: DeFi Llama pool UUID
            
        Returns:
            List of historical data points
        """
        response = await self._get(f"/chart/{pool_id}")
        return response.get("data", [])

    async def get_protocol_yields(self, protocol_slug: str) -> List[Dict]:
        """
        Fetch yields for a specific protocol (by slug)
        
        Args:
            protocol_slug: Protocol slug (e.g., "aave-v3", "curve-dex")
            
        Returns:
            List of pools for that protocol
        """
        all_pools = await self.fetch_all_yields()
        
        return [
            pool for pool in all_pools
            if pool.get("project") == protocol_slug
        ]

if __name__ == "__main__":
    # Test script
    import json

    async def test():
        client = DefiLlamaClient()
        
        print("Fetching Aave V3 pools...")
        aave_pools = await client.get_protocol_yields("aave-v3")
        print(f"Found {len(aave_pools)} Aave V3 pools")
        
        if aave_pools:
            # Take the first pool ID and fetch specific details
            sample_pool_id = aave_pools[0]['pool']
            print(f"\nFetching details for pool ID: {sample_pool_id} ({aave_pools[0]['symbol']})")
            
            specific_pools = await client.fetch_pool_yields([sample_pool_id])
            print(json.dumps(specific_pools, indent=2))
            
            print(f"\nFetching history for pool ID: {sample_pool_id}")
            history = await client.fetch_historical_yield(sample_pool_id)
            print(f"Retrieved {len(history)} historical data points")

    asyncio.run(test())
