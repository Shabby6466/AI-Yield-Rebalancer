import requests
import asyncio
from src.data.defillama_client import DefiLlamaClient

async def get_test_pools():
    client = DefiLlamaClient()
    # Fetch Aave V3 pools to find valid UUIDs
    aave_pools = await client.get_protocol_yields("aave-v3")
    
    # Sort by APY to find a "Low Yield" and "High Yield" pool
    sorted_pools = sorted(aave_pools, key=lambda x: x['apy'])
    
    low_yield_pool = sorted_pools[0]
    high_yield_pool = sorted_pools[-1]
    
    return low_yield_pool, high_yield_pool

def verify_brain():
    # 1. Get Real Pool IDs
    print("Fetching real pool IDs from DeFi Llama...")
    low_pool, high_pool = asyncio.run(get_test_pools())
    
    print(f"Low Yield Pool: {low_pool['symbol']} ({low_pool['apy']:.2%} APY) - ID: {low_pool['pool']}")
    print(f"High Yield Pool: {high_pool['symbol']} ({high_pool['apy']:.2%} APY) - ID: {high_pool['pool']}")
    
    # 2. Simulate User Holding the LOW yield asset
    # The server logic currently only looks at the pools WE PROVIDE in the request.
    # So we must provide both so it can "see" the opportunity.
    # (In a real production app, the server would search the market itself, but for this MVP input logic, we pass the menu).
    
    payload = {
        "portfolio_id": "test_verification_001",
        "current_allocations": {
            low_pool['pool']: 100_000, # We hold $100k of the bad one
            high_pool['pool']: 0       # We know about the good one but hold 0
        }
    }
    
    print("\nSending request to Brain...")
    try:
        response = requests.post("http://localhost:8000/inference/predict", json=payload)
        result = response.json()
        
        print("\n BRAIN DECISION:")
        print(f"Action: {result['action']}")
        print(f"Confidence: {result['confidence']}")
        print(f"Reason: {result['reason']}")
        print(f"Estimated Gas: ${result['estimated_gas']}")
        print(f"Net APY Gain: {result['net_apy_gain']:.2%}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    verify_brain()
