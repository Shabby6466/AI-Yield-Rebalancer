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
    logger.info(f"Analyzing portfolio: {request.portfolio_id}")
    
    # 1. Fetch Real Data
    pool_ids = list(request.current_allocations.keys())
    live_pools = await clients['defillama'].fetch_pool_yields(pool_ids)
    
    if not live_pools:
        raise HTTPException(status_code=404, detail="Pool data not found")
        
    # Simplify: Assess the largest position first
    # In a real system, we'd optimize the full portfolio vector
    best_pool = max(live_pools, key=lambda p: p.get('apy', 0))
    current_pool_id = pool_ids[0] # Assume single-asset strategy for MVP
    current_pool = next((p for p in live_pools if p['pool'] == current_pool_id), None)
    
    if not current_pool:
         raise HTTPException(status_code=404, detail="Current pool data not found")

    current_apy = current_pool['apy'] / 100
    new_apy = best_pool['apy'] / 100
    capital_usd = sum(request.current_allocations.values())
    
    logger.info(f"Market Scan: Current APY {current_apy:.2%} -> Best APY {new_apy:.2%} ({best_pool['symbol']})")
    
    # 2. Filter: Mean Reversion (Is the new yield a spike?)
    # Fetches simple history (last 30 points)
    history = await clients['defillama'].fetch_historical_yield(best_pool['pool'])
    historical_apys = [d['apy']/100 for d in history[-30:]] # Last 30 days
    
    if clients['mean_reversion'].is_spike(new_apy, historical_apys):
        return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.25, # Risk Detected
            "reason": f"Mean Reversion Risk: New APY {new_apy:.2%} is a statistical anomaly.",
            "estimated_gas": 0,
            "net_apy_gain": 0,
            "metrics": None
        }

    # 3. Filter: Vampire Attack (Liquidity Check)
    if not clients['liquidity'].check_liquidity_depth(best_pool, capital_usd):
         return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.25, # Risk Detected
            "reason": f"Liquidity Risk: Moving ${capital_usd} would cause high slippage.",
            "estimated_gas": 0,
            "net_apy_gain": 0,
            "metrics": None
        }

    # 4. Filter: Gas Optimization
    # Estimate Profit vs Cost
    if current_pool_id == best_pool['pool']:
         return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.9,
            "reason": "Already in best performing pool.",
            "estimated_gas": 0,
            "net_apy_gain": 0,
            "metrics": None
        }

    is_profitable, total_cost, cost_breakdown = clients['gas'].should_rebalance(
        current_apy, new_apy, capital_usd
    )
    
    # Enhance metrics with clarity
    if cost_breakdown:
        # Total Conversion Loss = Swap Fees + Slippage (assumed negligible for now if liquidity check passed)
        cost_breakdown['total_conversion_loss'] = cost_breakdown.get('total', 0)
        cost_breakdown['roi_days'] = (
            total_cost / (cost_breakdown.get('monthly_gain', 1)/30) 
            if cost_breakdown.get('monthly_gain', 0) > 0 else 999
        )

    if not is_profitable:
        return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.5,
            "reason": f"High migration costs (${total_cost:.2f}) vs monthly gain (${cost_breakdown.get('monthly_gain', 0):.2f}).",
            "estimated_gas": total_cost,
            "net_apy_gain": (new_apy - current_apy),
            "metrics": cost_breakdown
        }

    # If all pass -> REBALANCE
    return {
        "action": "REBALANCE",
        "target_allocations": {best_pool['pool']: capital_usd},
        "confidence": 0.95,
        "reason": f"Valid opportunity: +{new_apy-current_apy:.2%} APY gain. Safe & Profitable.",
        "estimated_gas": total_cost,
        "net_apy_gain": (new_apy - current_apy),
        "metrics": cost_breakdown
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
