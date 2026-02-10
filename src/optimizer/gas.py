import logging
import requests
import os
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()
logger = logging.getLogger(__name__)

class GasOptimizer:
    def __init__(self, risk_multiplier: float = 10.0):
        self.risk_multiplier = risk_multiplier
        self.etherscan_key = os.getenv("ETHERSCAN_API_KEY")
        
        # Multi-Chain RPCs
        self.w3_eth = Web3(Web3.HTTPProvider("https://cloudflare-eth.com"))
        self.w3_base = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
        
        # Gas Limits (Estimated)
        self.limits = {
            "transfer": 21000,
            "swap": 200000,
            "bridge": 150000
        }

    def get_eth_price(self):
        """Fetch live ETH price (USD)"""
        try:
            # Try Etherscan first
            if self.etherscan_key:
                url = f"https://api.etherscan.io/api?module=stats&action=ethprice&apikey={self.etherscan_key}"
                resp = requests.get(url, timeout=3).json()
                if resp['status'] == '1':
                    return float(resp['result']['ethusd'])
            
            # Fallback to CryptoCompare
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get("https://min-api.cryptocompare.com/data/price?fsym=ETH&tsyms=USD", headers=headers, timeout=3)
            return float(resp.json().get("USD", 3000.0))
        except Exception as e:
            logger.warning(f"Price fetch failed: {e}")
            return 3000.0

    def get_real_gas_cost(self, chain: str, action: str, eth_price: float) -> float:
        """Calculate USD cost for a specific action on a specific chain"""
        try:
            w3 = self.w3_eth if chain == "ethereum" else self.w3_base
            gas_price_wei = w3.eth.gas_price
            
            # Cost = (Limit * Price_in_Wei) / 1e18 * ETH_Price
            cost_usd = (self.limits.get(action, 200000) * gas_price_wei) / 1e18 * eth_price
            return cost_usd
        except Exception as e:
            logger.error(f"Gas fetch failed for {chain}: {e}")
            return 1.0 if chain == "ethereum" else 0.5

    def calculate_swap_fee(self, amount_usd: float, is_stable_pair: bool = True) -> float:
        """
        Estimate DEX Swap Fee
        - Stable-Stable (Curve/Uni v3 0.05%): 0.0005
        - Volatile (Uni v3 0.3%): 0.003
        """
        fee_rate = 0.0005 if is_stable_pair else 0.003
        return amount_usd * fee_rate

    def should_rebalance(self, 
                        current_apy: float, 
                        new_apy: float, 
                        capital_usd: float,
                        is_stable_pair: bool = True) -> tuple[bool, float, dict]:
        """
        Decide based on Total Migration Cost (Gas + Swap Fees)
        """
        eth_price = self.get_eth_price()
        
        # 1. Gas Costs (Exit -> Bridge -> Enter)
        cost_exit = self.get_real_gas_cost("ethereum", "swap", eth_price)
        cost_bridge = self.get_real_gas_cost("ethereum", "bridge", eth_price)
        cost_enter = self.get_real_gas_cost("base", "swap", eth_price)
        total_gas = cost_exit + cost_bridge + cost_enter
        
        # 2. Swap Fees (The invisible killer)
        # We pay swap fees TWICE if we exit to stable then enter new position? 
        # Usually: Exit (LP -> Stable) [Fee] -> Bridge -> Enter (Stable -> LP) [Fee]
        # Let's assume 2 swaps for full migration
        swap_fee_exit = self.calculate_swap_fee(capital_usd, is_stable_pair)
        swap_fee_enter = self.calculate_swap_fee(capital_usd, is_stable_pair)
        total_swap_fee = swap_fee_exit + swap_fee_enter
        
        # 3. Total Migration Cost
        total_cost = total_gas + total_swap_fee
        
        # Profitability Check (Annualized -> Monthly)
        apy_diff = new_apy - current_apy
        if apy_diff <= 0:
            return False, 0.0, {}
            
        annual_profit = capital_usd * apy_diff
        monthly_profit = annual_profit / 12
        
        # Strict Check: Monthly profit must cover migration cost * Multiplier
        is_profitable = monthly_profit > (total_cost * self.risk_multiplier)
        
        breakdown = {
            "gas_exit": cost_exit,
            "gas_bridge": cost_bridge,
            "gas_enter": cost_enter,
            "swap_fees": total_swap_fee,
            "total": total_cost,
            "monthly_gain": monthly_profit
        }
        
        logger.info(f"💰 Cost Benefit Analysis (Capital: ${capital_usd:,.0f}):")
        logger.info(f"   Gas (ETH+Base): ${total_gas:.2f}")
        logger.info(f"   Swap Fees (x2): ${total_swap_fee:.2f} ({(total_swap_fee/capital_usd)*100:.2f}%)")
        logger.info(f"   ---------------------------")
        logger.info(f"   Total Cost:     ${total_cost:.2f}")
        logger.info(f"   Monthly Gain:   ${monthly_profit:.2f}")
        logger.info(f"   ROI Period:     {total_cost/(monthly_profit/30):.1f} days")
        
        return is_profitable, total_cost, breakdown
