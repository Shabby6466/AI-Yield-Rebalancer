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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.APP_NAME)
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

class RebalanceRequest(BaseModel):
    portfolio_id: str
    current_allocations: Dict[str, float]
    force_execution: bool = False

class PredictionResponse(BaseModel):
    action: str
    target_allocations: Dict[str, float]
    confidence: float
    reason: str
    estimated_gas: float
    net_apy_gain: float
    metrics: Optional[Dict[str, float]] = None
    market_context: Optional[Dict] = None

@app.get("/health")
def health_check():
    return {"status": "operational", "version": "0.1.0"}

@app.post("/inference/predict", response_model=PredictionResponse)
async def predict_yield_opportunity(request: RebalanceRequest):
    # 1. Detect Current State (SIMPLIFIED FALLBACK)
    current_pool_id = None
    current_symbol = "CASH"
    current_apy = 0.0
    
    if not current_pool_id and request.current_allocations:
        current_pool_id = list(request.current_allocations.keys())[0]

    logger.info(f"Analyzing portfolio for: {current_symbol}")
    live_pools = await clients['defillama'].fetch_top_pools(limit=50)
    
    if not live_pools:
        raise HTTPException(status_code=404, detail="Pool data not found")
        
    best_pool = max(live_pools, key=lambda p: p.get('apy', 0))
    current_pool = next((p for p in live_pools if p['pool'] == current_pool_id), None)
    
    if current_pool:
        current_apy = current_pool['apy'] / 100
    
    new_apy = best_pool['apy'] / 100
    capital_usd = sum(request.current_allocations.values()) if request.current_allocations else 100000.0
    
    logger.info(f"Market Scan: Current APY {current_apy:.2%} -> Best APY {new_apy:.2%} ({best_pool['symbol']})")
    
    history = await clients['defillama'].fetch_historical_yield(best_pool['pool'])
    historical_apys = [d.get('apy', 0)/100 for d in history[-30:]] if history else []
    
    if clients['mean_reversion'].is_spike(new_apy, historical_apys):
        return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.25,
            "reason": f"Mean Reversion Risk: New APY {new_apy:.2%} is a statistical anomaly.",
            "estimated_gas": 0,
            "net_apy_gain": 0,
            "metrics": None
        }

    if not clients['liquidity'].check_liquidity_depth(best_pool, capital_usd):
         return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.25,
            "reason": f"Liquidity Risk: Moving ${capital_usd} would cause high slippage.",
            "estimated_gas": 0,
            "net_apy_gain": 0,
            "metrics": None
        }

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

    if not is_profitable:
        return {
            "action": "HOLD",
            "target_allocations": request.current_allocations,
            "confidence": 0.5,
            "reason": f"High migration costs (${total_cost:.2f}) vs monthly gain (${cost_breakdown.get('monthly_gain', 0):.2f}).",
            "estimated_gas": total_cost,
            "net_apy_gain": (new_apy - current_apy),
            "metrics": cost_breakdown,
            "market_context": market_context
        }

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
