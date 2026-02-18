import logging
import requests
import os
import random
import asyncio
from typing import Dict, Tuple
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()
logger = logging.getLogger(__name__)

class GasOptimizer:
    def __init__(self, risk_multiplier: float = 3.0):
        # Reduced from 10.0 to 3.0 to be more realistic for active rebalancing
        self.risk_multiplier = risk_multiplier
        self.etherscan_key = os.getenv("ETHERSCAN_API_KEY")
        
        # Multi-Chain RPCs
        # Use these or get a free API key from Alchemy/Infura for 100% reliability
        self.w3_eth = Web3(Web3.HTTPProvider("https://eth.drpc.org")) # dRPC is often more stable
        self.w3_base = Web3(Web3.HTTPProvider("https://base.meowrpc.com")) # Alternative Base RPC
        
        # Gas Limits (L2 execution only)
        self.limits = {
            "transfer": 65000,
            "swap": 350000,
            "bridge": 200000
        }

    def get_eth_price(self) -> float:
        """Fetch live ETH price with fallback"""
        try:
            url = f"https://api.etherscan.io/api?module=stats&action=ethprice&apikey={self.etherscan_key}"
            resp = requests.get(url, timeout=5).json()
            if resp.get('status') == '1':
                return float(resp['result']['ethusd'])
            return 3000.0 # Standard fallback
        except Exception as e:
            logger.warning(f"Price fetch failed: {e}")
            return 3000.0

    def get_l1_data_fee(self, eth_gas_price_wei: int) -> float:
        """
        Calculates the L1 Data Fee for Base (OP Stack).
        L1 Fee = (CalldataSize * L1GasPrice * Scalar)
        Approximated for a standard DeFi transaction (~300 bytes).
        """
        # Base uses a scalar (approx 0.6) and L1 gas price
        l1_scalar = 0.684
        tx_data_size = 300 # bytes for a typical swap calldata
        l1_fee_wei = int(tx_data_size * eth_gas_price_wei * l1_scalar)
        return l1_fee_wei / 1e18

    def get_real_gas_cost(self, chain: str, action: str, eth_price: float) -> float:
        """Optimized calculation including L1 Data Fees for L2s"""
        try:
            eth_gas_price = self.w3_eth.eth.gas_price
            
            if chain == "base":
                l2_gas_price = self.w3_base.eth.gas_price
                l2_execution = (self.limits.get(action, 200000) * l2_gas_price) / 1e18
                l1_data_cost = self.get_l1_data_fee(eth_gas_price)
                total_wei = l2_execution + l1_data_cost
            else:
                total_wei = (self.limits.get(action, 210000) * eth_gas_price) / 1e18
                
            return total_wei * eth_price * random.uniform(0.98, 1.05)
        except Exception as e:
            logger.error(f"Gas fetch failed: {e}")
            return 5.0 if chain == "ethereum" else 0.50

    def calculate_swap_fee(self, amount_usd: float, pool_tvl: float, is_stable: bool) -> float:
        """
        Optimized Concentrated Liquidity Slippage Estimation.
        Uni V3 Stablecoin pools (0.01% tier) have significantly higher depth.
        """
        # Base fee (0.01% for stabl-stabl, 0.3% for volatile)
        fee_rate = 0.0001 if is_stable else 0.003
        base_fee = amount_usd * fee_rate

        # Concentrated Liquidity Slippage Model
        # Stablecoin liquidity is ~25x more concentrated at the $1 peg
        concentration_factor = 25.0 if is_stable else 1.0
        # Slippage = (Trade / Effective_Liquidity)
        slippage = amount_usd / (pool_tvl * concentration_factor)
        
        return base_fee + (amount_usd * slippage)

    def should_rebalance(self, 
                        current_apy: float, 
                        new_apy: float, 
                        capital_usd: float,
                        pool_tvl: float,
                        is_stable: bool = True) -> Tuple[bool, float, Dict]:
        """Final decision engine with refined ROI calculation"""
        eth_price = self.get_eth_price()
        
        # 1. Chain Migration Costs
        cost_exit = self.get_real_gas_cost("ethereum", "swap", eth_price)
        cost_bridge = self.get_real_gas_cost("ethereum", "bridge", eth_price)
        cost_enter = self.get_real_gas_cost("base", "swap", eth_price)
        total_gas = cost_exit + cost_bridge + cost_enter
        
        # 2. DEX Friction
        total_swap_fee = self.calculate_swap_fee(capital_usd, pool_tvl, is_stable) * 2
        total_cost = total_gas + total_swap_fee
        
        # 3. Monthly ROI Check
        apy_diff = (new_apy - current_apy)
        if apy_diff <= 0.005: # Minimum 0.5% gain required to even consider rebalancing
            return False, total_cost, {"reason": "Yield spread too thin"}

        monthly_gain = (capital_usd * apy_diff) / 12
        
        # Hurdle: Monthly gain must cover (Total Cost * Risk Multiplier)
        is_profitable = monthly_gain > (total_cost * self.risk_multiplier)
        
        breakdown = {
            "total_gas": total_gas,
            "swap_fees": total_swap_fee,
            "total_cost": total_cost,
            "monthly_gain": monthly_gain,
            "roi_days": (total_cost / (monthly_gain / 30)) if monthly_gain > 0 else 999
        }
        
        return is_profitable, total_cost, breakdown
    
if __name__ == "__main__":
    import asyncio

    async def run_diagnostic_test():
        # 1. Initialize Optimizer
        # risk_multiplier=3 means monthly profit must be 3x the migration cost
        optimizer = GasOptimizer(risk_multiplier=3.0)
        
        print("--- 🛠️ GAS OPTIMIZER DIAGNOSTIC START ---")
        
        # 2. Fetch Market Data
        eth_price = optimizer.get_eth_price()
        print(f"[MARKET] Current ETH Price: ${eth_price:,.2f}")
        
        # 3. Simulate a Rebalance Scenario
        # Scenario: Moving $100k from 5% APY (Current) to 12% APY (Target)
        capital = 500.0
        current_yield = 0.05
        target_yield = 0.12
        pool_liquidity = 5000000.0 # $5M TVL in the target pool
        
        print(f"[SCENARIO] Capital: ${capital:,.0f}")
        print(f"[SCENARIO] Yield Shift: {current_yield*100}% -> {target_yield*100}%")
        
        # 4. Run Optimization Logic
        is_profitable, total_cost, data = optimizer.should_rebalance(
            current_apy=current_yield,
            new_apy=target_yield,
            capital_usd=capital,
            pool_tvl=pool_liquidity,
            is_stable=True
        )
        
        # 5. Output Detailed Results
        print("\n--- 📊 BREAKDOWN ---")
        print(f"Total Migration Cost:   ${data['total_cost']:.2f}")
        print(f"  └─ Gas (Est.):        ${data['total_gas']:.2f}")
        print(f"  └─ Swap Fees:         ${data['swap_fees']:.2f}")
        print(f"Projected Monthly Gain: ${data['monthly_gain']:.2f}")
        print(f"Payback Period (ROI):   {data['roi_days']:.1f} days")
        
        print("\n--- 🤖 AI DECISION ---")
        if is_profitable:
            print(f"✅ STATUS: REBALANCE RECOMMENDED")
            print(f"REASON: Monthly gain is {data['monthly_gain']/data['total_cost']:.1f}x the migration cost.")
        else:
            print(f"❌ STATUS: HOLD POSITION")
            print(f"REASON: {data.get('reason', 'Migration cost too high relative to gains.')}")
            
        print("\n--- 🛠️ DIAGNOSTIC COMPLETE ---")

    # Run the test
    asyncio.run(run_diagnostic_test())    