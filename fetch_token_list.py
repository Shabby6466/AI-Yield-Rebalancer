import requests
import json
import os

TOKEN_LIST_URL = "https://tokens.uniswap.org/"

def fetch_tokens():
    print(f"Fetching token list from {TOKEN_LIST_URL}...")
    try:
        response = requests.get(TOKEN_LIST_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        tokens = data.get('tokens', [])
        
        # Transform to simple Symbol -> Address map
        token_map = {}
        for t in tokens:
            if t.get('chainId') == 1:  # Ethereum Mainnet
                symbol = t.get('symbol', '').upper()
                address = t.get('address')
                if symbol and address:
                    token_map[symbol] = address
        
        # Save to file
        os.makedirs("src/data", exist_ok=True)
        with open("src/data/token_map.json", "w") as f:
            json.dump(token_map, f, indent=2)
            
        print(f"✅ Saved {len(token_map)} tokens to src/data/token_map.json")
        
        # Check for ZBU
        if token_map.get('ZBU'):
            print(f"Found ZBU: {token_map['ZBU']}")
        else:
            print("ZBU not in Uniswap list.")
            
    except Exception as e:
        print(f"❌ Failed to fetch token list: {e}") 

if __name__ == "__main__":
    fetch_tokens()
