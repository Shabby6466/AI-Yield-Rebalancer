import asyncio
from src.data.defillama_client import DefiLlamaClient
import json

async def check_pools():
    client = DefiLlamaClient()
    print("Fetching top 10 stablecoin pools from DeFiLlama...")
    pools = await client.fetch_top_pools(limit=10)
    for i, p in enumerate(pools):
        print(f"{i+1}. {p['symbol']} ({p['project']}) on {p['chain']} - APY: {p['apy']:.2f}% | TVL: ${p['tvlUsd']:,.0f} | ID: {p['pool']}")

if __name__ == "__main__":
    asyncio.run(check_pools())
