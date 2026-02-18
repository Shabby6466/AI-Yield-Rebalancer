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
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        """
        Returns a persistent session, creating it if necessary.
        This prevents socket exhaustion and reduces connection overhead.
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def _get(self, endpoint: str) -> Any:
        """
        Execute a GET request to DeFi Llama API using persistent session
        
        Args:
            endpoint: API endpoint (e.g., '/pools')
            
        Returns:
            JSON response data
        """
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            session = await self.get_session()
            async with session.get(url) as response:
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

    async def close(self):
        """
        Cleanup and close persistent session on app shutdown.
        Must be called when client is no longer needed.
        """
        if self._session and not self._session.closed:
            await self._session.close()

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

    async def fetch_top_pools(
        self, 
        chains: List[str] = ["Ethereum", "Base"], 
        projects: List[str] = ["uniswap-v3", "aave-v3", "compound-v3", "lido"],
        min_tvl: int = 1_000_000, 
        limit: int = 20
    ) -> List[Dict]:
        """
        Fetch top performing pools restricted to a 'Blue Chip' whitelist.
        
        Args:
            chains: List of chain names (case-insensitive)
            projects: List of whitelisted protocol slugs (e.g., "aave-v3", "uniswap-v3")
            min_tvl: Minimum TVL in USD
            limit: Number of pools to return
            
        Returns:
            List of top pools sorted by APY, filtered by whitelist criteria
        """
        all_pools = await self.fetch_all_yields()
        
        # Convert to sets for O(1) lookups and case-insensitivity
        target_chains = {c.lower() for c in chains}
        target_projects = {p.lower() for p in projects}
        
        filtered = []
        for p in all_pools:
            # 1. Chain Filter
            if p.get('chain', '').lower() not in target_chains:
                continue
                
            # 2. Project Whitelist Filter
            if p.get('project', '').lower() not in target_projects:
                continue
                
            # 3. Stablecoin/Safety Filter
            # We prioritize 'stablecoin': True, but allow Lido (stETH) 
            # as it is single-sided and 'Blue Chip'.
            is_stable = p.get('stablecoin', False)
            is_lido_lst = (p.get('project', '').lower() == 'lido' and p.get('symbol') in ['stETH', 'wstETH'])
            
            if not (is_stable or is_lido_lst):
                continue

            # 4. Economic Vitality Filter
            if p.get('tvlUsd', 0) < min_tvl:
                continue
                
            # 5. Outlier/Error Filter
            if p.get('apy', 0) <= 0 or p.get('apy', 0) > 200:
                continue
                
            filtered.append(p)
        
        # Sort by APY descending
        sorted_pools = sorted(filtered, key=lambda x: x.get('apy', 0), reverse=True)
        
        logger.info(f"Filtered {len(filtered)} pools from {len(all_pools)} total candidates.")
        return sorted_pools[:limit]

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
    
    async def fetch_stablecoin_pools(self, chains: List[str] = ["Ethereum"], min_tvl: int = 1000000) -> List[Dict]:
        """
        Fetch only pools where stablecoin is True
        """
        all_pools = await self.fetch_all_yields()
        chains_lower = [c.lower() for c in chains]
        allowed_protocols = {'aave-v3', 'curve-dex', 'uniswap-v3', 'compound-v3'}
        
        filtered = [
            p for p in all_pools
            if p.get('stablecoin') is True
            and p.get('chain', '').lower() in chains_lower
            and p.get('project', '').lower() in allowed_protocols
            and p.get('tvlUsd', 0) >= min_tvl
        ]
        
        # Sort by APY descending
        return sorted(filtered, key=lambda x: x.get('apy', 0), reverse=True)
    
if __name__ == "__main__":
    # Test script

    async def test():
        client = DefiLlamaClient()

        print("Fetching top stablecoin pools on Base...")

        top_stablecoin_pools = await client.fetch_stablecoin_pools(chains=["Base"], min_tvl=1000000)
        print(f"Found {len(top_stablecoin_pools)} stablecoin pools on Base with TVL > $1M:")
        for pool in top_stablecoin_pools:
            print(f"- {pool['symbol']} (APY: {pool['apy']:.2f}%, TVL: ${pool['tvlUsd']:.2f})")
        
        # print("Fetching Uniswap V3 pools...")
        # aave_pools = await client.get_protocol_yields("uniswap-v3")
        # print(f"Found {len(aave_pools)} Uniswap V3 pools")
        
        # if aave_pools:
        #     # Take the first pool ID and fetch specific details
        #     sample_pool_id = aave_pools[0]['pool']
        #     print(f"\nFetching details for pool ID: {sample_pool_id} ({aave_pools[0]['symbol']})")
            
        #     specific_pools = await client.fetch_pool_yields([sample_pool_id])

        #     print(json.dumps(specific_pools, indent=2))
            
        #     print(f"\nFetching history for pool ID: {sample_pool_id}")
        #     history = await client.fetch_historical_yield(sample_pool_id)
        #     print(f"Retrieved {len(history)} historical data points")

    asyncio.run(test())
