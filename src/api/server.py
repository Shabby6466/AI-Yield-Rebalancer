"""
FastAPI Backend Server
The central nervous system linking Data -> AI -> Execution
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Dict
import asyncio
import logging

from src.core.config import settings
from src.data.defillama_client import DefiLlamaClient
from src.ml.mean_reversion import MeanReversionFilter
from src.risk.liquidity import LiquidityFilter
from src.optimizer.gas import GasOptimizer
from src.optimizer.trade_sizer import TradeSizer
from src.backtest.prediction_tracker import PredictionTracker

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.APP_NAME)

# Shared Clients (Singleton)
clients = {}

@app.on_event("startup")
async def startup_event():
    logger.info("Starting AI Yield Rebalancer Brain...")
    clients['defillama'] = DefiLlamaClient()
    clients['mean_reversion'] = MeanReversionFilter()
    clients['liquidity'] = LiquidityFilter()
    clients['gas'] = GasOptimizer()
    clients['sizer'] = TradeSizer()
    clients['tracker'] = PredictionTracker()

# Data Models
class RebalanceRequest(BaseModel):
    portfolio_id: str
    current_allocations: Dict[str, float]  # pool_id -> amount_usd
    force_execution: bool = False

class PredictionResponse(BaseModel):
    action: str  # "REBALANCE" or "HOLD"
    target_allocations: Dict[str, float]
    confidence: float
    reason: str
    estimated_gas: float
    net_apy_gain: float
    metrics: Optional[Dict[str, float]] = None

@app.get("/health")
def health_check():
    return {"status": "operational", "version": "0.1.0"}

@app.post("/inference/predict", response_model=PredictionResponse)
async def predict_yield_opportunity(request: RebalanceRequest):
    """
    Core Intelligence Endpoint:
    1. Fetches live data (DefiLlama)
    2. Runs AI Filters (Mean Reversion, Vampire)
    3. Checks Gas Optimization
    4. Returns decision
    """
    # 1. Detect Current State
    # Simplified for POC: Assume we are starting fresh or get state from DB later
    current_pool_id = None
    current_symbol = "CASH"
    current_apy = 0.0   
    # If the request provides allocations, we can either override or merge
    # For now, if use_system_state is True, we use what the rebalancer service thinks
    if not current_pool_id and request.current_allocations:
        current_pool_id = list(request.current_allocations.keys())[0]

    logger.info(f"Analyzing portfolio for: {current_symbol}")
    
    # 2. Fetch Real Data
    # In practice, we'd fetch all 200+ pools, then filter
    live_pools = await clients['defillama'].fetch_top_pools(limit=50)
    
    if not live_pools:
        raise HTTPException(status_code=404, detail="Pool data not found")
        
    # Simplify: Assess the largest position first
    # In a real system, we'd optimize the full portfolio vector
    best_pool = max(live_pools, key=lambda p: p.get('apy', 0))
    # Find current pool in the scan to get its LATEST APY
    current_pool = next((p for p in live_pools if p['pool'] == current_pool_id), None)
    
    if current_pool:
        current_apy = current_pool['apy'] / 100
    
    new_apy = best_pool['apy'] / 100
    capital_usd = sum(request.current_allocations.values()) if request.current_allocations else 100000.0
    
    logger.info(f"Market Scan: Current APY {current_apy:.2%} -> Best APY {new_apy:.2%} ({best_pool['symbol']})")
    
    # 2. Filter: Mean Reversion (Is the new yield a spike?)
    history = await clients['defillama'].fetch_historical_yield(best_pool['pool'])
    if not history:
        historical_apys = []
    else:
        historical_apys = [d.get('apy', 0)/100 for d in history[-30:]]
    
    is_spike = clients['mean_reversion'].is_spike(new_apy, historical_apys)

    # 3. Filter: Liquidity Check
    is_low_liquidity = not clients['liquidity'].check_liquidity_depth(best_pool, capital_usd)

    # 4. Financial Analysis (Gas & Profitability) - Run this ALWAYS for visibility
    if current_pool_id == best_pool['pool']:
         # Special case: We are already there
         is_profitable = False
         total_cost = 0
         cost_breakdown = {}
         reason = "Already in best performing pool."
         confidence = 0.9
    else:
        is_profitable, total_cost, cost_breakdown = clients['gas'].should_rebalance(
            current_apy, new_apy, capital_usd
        )
        
        # Enhance metrics
        if cost_breakdown:
            cost_breakdown['total_conversion_loss'] = cost_breakdown.get('total', 0)
            cost_breakdown['roi_days'] = (
                total_cost / (cost_breakdown.get('monthly_gain', 1)/30) 
                if cost_breakdown.get('monthly_gain', 0) > 0 else 999
            )

    market_context = {
        "runner_ups": [{"symbol": p['symbol'], "apy": p['apy'], "tvl": p['tvlUsd']} for p in live_pools[:3]],
        "target_metadata": best_pool,
        "current_pool_symbol": current_symbol
    }

    # --- DECISION LOGIC ---
    
    # 1. Risk: Spike
    if is_spike:
        return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.25,
            "reason": f"Mean Reversion Risk: New APY {new_apy:.2%} is likely a temporary spike.",
            "estimated_gas": total_cost,
            "net_apy_gain": (new_apy - current_apy),
            "metrics": cost_breakdown,
            "market_context": market_context
        }

    # 2. Risk: Liquidity
    if is_low_liquidity:
         return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.25,
            "reason": f"Liquidity Risk: Pool depth insufficient for ${capital_usd:,.0f} (High Slippage).",
            "estimated_gas": total_cost,
            "net_apy_gain": (new_apy - current_apy),
            "metrics": cost_breakdown,
            "market_context": market_context
        }

    # 3. Financials: Not Profitable
    if not is_profitable:
        monthly_gain = cost_breakdown.get('monthly_gain', 0) if cost_breakdown else 0
        return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.5,
            "reason": f"Unprofitable: Gas cost (${total_cost:.2f}) exceeds projected monthly gain (${monthly_gain:.2f}).",
            "estimated_gas": total_cost,
            "net_apy_gain": (new_apy - current_apy),
            "metrics": cost_breakdown,
            "market_context": market_context,
        }

    # 4. Valid Opportunity
    return {
        "action": "REBALANCE",
        "target_allocations": {best_pool['pool']: capital_usd},
        "confidence": 0.95,
        "reason": f"Valid opportunity: +{new_apy-current_apy:.2%} APY gain. Safe & Profitable.",
        "estimated_gas": total_cost,
        "net_apy_gain": (new_apy - current_apy),
        "metrics": cost_breakdown,
        "market_context": market_context
    }

@app.post("/admin/rebalance")
async def execute_rebalance(
    request: RebalanceRequest, 
    background_tasks: BackgroundTasks
):
    """
    Manual override to force rebalancing
    """
    # Logic to trigger on-chain transaction
    return {"status": "Rebalancing queued", "tx_hash": "pending"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
