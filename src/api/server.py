"""
FastAPI Backend Server
The central nervous system linking Data -> AI -> Execution
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict
import asyncio
import logging
from datetime import datetime, timedelta
import psycopg2
from psycopg2 import pool

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from src.core.config import settings
from src.data.defillama_client import DefiLlamaClient
from src.data.defillama_ingestor import YieldIngestor
import os
from src.ml.mean_reversion import MeanReversionFilter
from src.risk.liquidity import LiquidityFilter
from src.optimizer.gas import GasOptimizer
from src.optimizer.trade_sizer import TradeSizer
from src.backtest.prediction_tracker import PredictionTracker
from src.risk.slippage_client import SlippageClient

# Conditional ML Imports for Shadow Mode safety
# Initialize to None first to ensure they exist even if import fails
FeaturePipeline = None
YieldPredictor = None
RiskScorer = None
ML_AVAILABLE = False

try:
    from src.ml.feature_pipeline import FeaturePipeline
    from src.ml.lstm_model import YieldPredictor
    from src.ml.risk_scorer import RiskScorer
    import torch
    
    # Verify imports are valid classes/callables
    if callable(YieldPredictor) and callable(RiskScorer):
        ML_AVAILABLE = True
    else:
        logger.warning(f"⚠️ ML modules imported but invalid (YieldPredictor={YieldPredictor}, RiskScorer={RiskScorer}). Disabling ML.")
        ML_AVAILABLE = False

except ImportError as e:
    logger.warning(f"⚠️ ML Libraries missing: {e}. Running in Heuristic-Only mode.")
    ML_AVAILABLE = False
except Exception as e:
    logger.error(f"⚠️ Unexpected error importing ML libraries: {e}")
    ML_AVAILABLE = False
import warnings
from web3 import Web3

# Shared Clients (Singleton)
clients = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP LOGIC ---
    global ML_AVAILABLE
    logger.info("Starting AI Yield Rebalancer Brain...")
    
    # Initialize Shared Clients
    clients['defillama'] = DefiLlamaClient()
    clients['mean_reversion'] = MeanReversionFilter()
    clients['liquidity'] = LiquidityFilter()
    clients['gas'] = GasOptimizer()
    clients['sizer'] = TradeSizer()
    clients['slippage'] = SlippageClient()
    
    # Initialize ML Models (Shadow Mode)
    if ML_AVAILABLE:
        try:
            clients['feature_pipeline'] = FeaturePipeline(os.getenv("DATABASE_URL"))
            
            # Load LSTM
            lstm_path = 'models/lstm_yield_predictor.pth'
            if os.path.exists(lstm_path):
                try:
                    # Load checkpoint to get input_dim
                    checkpoint = torch.load(lstm_path, map_location='cpu')
                    input_dim = checkpoint.get('input_dim', 19)
                    
                    clients['lstm'] = YieldPredictor(input_dim=input_dim)
                    clients['lstm'].load(lstm_path)
                    clients['lstm_ready'] = True
                    logger.info(f"✅ LSTM Model loaded successfully (input_dim={input_dim})")
                except Exception as e:
                    logger.error(f"Failed to load LSTM weights: {e}")
                    clients['lstm_ready'] = False
            else:
                logger.warning("⚠️ LSTM model file not found. Running in heuristic-only mode.")
                clients['lstm_ready'] = False
                
            # Load Risk Scorer
            risk_path = 'models/xgboost_risk_scorer_v1.pkl'
            clients['risk_scorer'] = RiskScorer()
            if os.path.exists(risk_path):
                try:
                    clients['risk_scorer'].load(risk_path)
                    clients['risk_ready'] = True
                    logger.info("✅ Risk Scorer loaded successfully")
                except Exception as e:
                    logger.error(f"Failed to load Risk Scorer: {e}")
                    clients['risk_ready'] = False
            else:
                logger.warning("⚠️ Risk Scorer model file not found. Disabling ML risk scoring.")
                clients['risk_ready'] = False
        except Exception as e:
            logger.error(f"Failed to initialize ML Pipeline: {e}")
            ML_AVAILABLE = False
    
    # Initialize Web3 for on-chain state (Vault Assets)
    rpc_url = os.getenv("RPC_URL", "http://localhost:8545")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    # Load StrategyHub Address with Priority: Env Var > .env > File
    hub_address = os.getenv("STRATEGY_HUB_ADDRESS")
    
    if not hub_address and os.path.exists(".env"):
        try:
            with open(".env") as f:
                for line in f:
                    if "STRATEGY_HUB_ADDRESS" in line:
                         parts = line.strip().split("=")
                         if len(parts) > 1:
                             hub_address = parts[1]
                             logger.info(f"Loaded StrategyHub from .env: {hub_address}")
        except Exception: pass

    if not hub_address and os.path.exists("contracts/deployed_address.txt"):
        with open("contracts/deployed_address.txt", "r") as f:
            hub_address = f.read().strip()
            logger.info(f"Loaded StrategyHub from file: {hub_address}")

    if not hub_address:
        logger.warning("⚠️ No StrategyHub address found. Defaulting to known testnet address (may fail).")
        hub_address = "0xe6DF612E4ae0F2284CD4959Fca047171C10D9402" # Hardcoded backup

    
    clients['w3'] = w3
    clients['hub_address'] = hub_address
    # StrategyHub ABI for getBalances()
    clients['hub_abi'] = [
        {
            "inputs": [],
            "name": "getBalances",
            "outputs": [
                {"internalType": "uint256", "name": "aaveBalance", "type": "uint256"},
                {"internalType": "uint256", "name": "compoundBalance", "type": "uint256"},
                {"internalType": "uint256", "name": "idleBalance", "type": "uint256"},
                {"internalType": "uint256", "name": "total", "type": "uint256"}
            ],
            "stateMutability": "view",
            "type": "function"
        }
    ]
    
    if ML_AVAILABLE:
        try:
            # This block is already handled above, removing the duplicate.
            pass
        except Exception as e:
            logger.error(f"❌ Critical ML Init Error: {e}")
            clients['lstm_ready'] = False
            clients['risk_ready'] = False
    else:
        logger.info("  ML_AVAILABLE=False. Skipping ML model initialization.")
        clients['feature_pipeline'] = None
        clients['lstm_ready'] = False
        clients['risk_ready'] = False
    
    # Initialize PostgreSQL Connection Pool
    db_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    workers_started = False
    
    if db_url:
        try:
            clients['db_pool'] = pool.SimpleConnectionPool(
                1, 5,
                dsn=db_url,
                connect_timeout=5
            )
            clients['tracker'] = PredictionTracker(conn_pool=clients['db_pool'], validation_days=1)
            logger.info("PostgreSQL connection pool initialized for PredictionTracker")
        except psycopg2.Error as e:
            logger.error(f"Failed to initialize PostgreSQL pool: {e}")
            clients['tracker'] = None
        
        # Start DefiLlama ingestion loop
        ingestor = YieldIngestor(db_url)
        clients['ingestor'] = ingestor

        async def _run_ingestor_loop():
            try:
                await ingestor.fetch_and_store()
            except Exception as e:
                logger.error(f"Initial DefiLlama ingest failed: {e}")

            while True:
                try:
                    await ingestor.fetch_and_store()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"DefiLlama ingestion error: {e}")
                await asyncio.sleep(3600)

        # Start Validation Worker
        async def _run_validation_worker():
            await asyncio.sleep(60)
            while True:
                try:
                    if clients.get('tracker'):
                        result = clients['tracker'].auto_validate_from_db()
                        logger.info(f"✅ AI Accuracy Validation: {result['validated']} validated, {result['pending']} pending")
                    else:
                        logger.warning("PredictionTracker not available for validation")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"❌ Validation Worker Error: {e}", exc_info=True)
                await asyncio.sleep(21600)
        
        # NOTE: Sentinel signal generator is DISABLED.
        # The real rebalancer_service (rebalancer_loop.py) is the authoritative source of cycle records.
        # The Sentinel was writing phantom REBALANCE records with 0% current APY (empty allocations),
        # which polluted the dashboard feed. The real rebalancer writes accurate records.
        logger.info("ℹ️  Sentinel signal generator disabled — real rebalancer_service is active.")

        ingestor_task = asyncio.create_task(_run_ingestor_loop())
        validation_task = asyncio.create_task(_run_validation_worker())
        workers_started = True
    else:
        logger.warning("DATABASE_URL not set — PostgreSQL features disabled")
        clients['tracker'] = None

    yield # --- SERVER RUNNING ---

    # --- SHUTDOWN LOGIC ---
    logger.info("Shutting down AI Yield Rebalancer...")
    
    if workers_started:
        ingestor_task.cancel()
        validation_task.cancel()
        try:
            await ingestor_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error cancelling tasks: {e}")
            
    if 'defillama' in clients:
        await clients['defillama'].close()
        logger.info("DeFiLlama client closed")
    
    if 'ingestor' in clients:
        await clients['ingestor'].close()
        logger.info("Ingestor closed")
    
    if 'db_pool' in clients:
        clients['db_pool'].closeall()
        logger.info("Database connection pool closed")
    
    logger.info("Shutdown complete")


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

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
    market_context: Optional[Dict] = None

@app.get("/health")
def health_check():
    return {
        "status": "operational", 
        "version": "0.1.0",
        "ml_brain": {
            "lstm_ready": clients.get('lstm_ready', False),
            "risk_ready": clients.get('risk_ready', False),
            "ml_available": ML_AVAILABLE
        },
        "config": {
            "network": os.getenv("NETWORK", "local"),
            "rebalance_interval": int(os.getenv("REBALANCE_INTERVAL", 600))
        }
    }

@app.post("/inference/predict", response_model=PredictionResponse)
async def predict_yield_opportunity(request: RebalanceRequest):
    """
    STATE-AWARE Rebalancing Intelligence:
    1. Syncs with user's current portfolio position(s)
    2. Calculates weighted average APY of current holdings
    3. Scans market for optimal opportunities
    4. Uses TradeSizer to recommend ideal allocation
    5. Checks profitability with slippage/gas cost awareness
    6. Makes decision based on APY delta hurdle rate (0.5% minimum)
    
    This is "set and forget" - it knows what you own and what you should own.
    """
    return await _predict_and_record(
        request.current_allocations, 
        request.portfolio_id, 
        request.force_execution,
        source="api"
    )

async def _predict_and_record(
    current_allocations: Dict[str, float], 
    portfolio_id: str, 
    force_execution: bool = False,
    source: str = "api"
) -> PredictionResponse:
    """
    Core prediction logic separated from API endpoint.
    Handles analysis, ML inference, and DB recording.
    """
    portfolio_value = sum(current_allocations.values()) if current_allocations else 100000.0
    
    # --- STEP 1: FETCH CURRENT PORTFOLIO STATE ---
    # Sync with the pools the user is ACTUALLY in right now
    current_positions = []
    
    if current_allocations:
        for pool_id, balance in current_allocations.items():
            try:
                # Fetch live stats for the pool the user is in
                pool_data = await clients['defillama'].fetch_pool_yields([pool_id])
                if pool_data:
                    current_positions.append({
                        "pool": pool_data[0],
                        "balance": balance,
                        "apy": pool_data[0].get('apy', 0)
                    })
                else:
                    logger.warning(f"Could not fetch data for current pool: {pool_id}")
            except Exception as e:
                logger.error(f"Error fetching current pool {pool_id}: {e}")
    
    # Calculate current weighted average APY
    if current_positions:
        current_avg_apy = sum(p['apy'] * (p['balance'] / portfolio_value) for p in current_positions)
        current_pool_id = current_positions[0]['pool']['pool']
    else:
        current_avg_apy = 0.0
        current_pool_id = "00000000-0000-0000-0000-000000000000" # Null UUID for "Cash"
    
    if source == "api":
        logger.info(f"Current Portfolio: {len(current_positions)} position(s), Weighted APY: {current_avg_apy:.2f}%")

    # --- STEP 2: GLOBAL MARKET SCAN ---
    # Find the "Optimal" pools available (Blue Chip whitelist only)
    try:
        candidate_pools = await clients['defillama'].fetch_top_pools(limit=30)
        if not candidate_pools:
            logger.warning("No candidate pools found during market scan.")
            raise HTTPException(status_code=404, detail="No candidate pools found")
        
        # Log top candidates
        logger.info(f"🔎 Market Scan: Found {len(candidate_pools)} candidates.")
        for i, p in enumerate(candidate_pools[:3]):
            logger.info(f"   {i+1}. {p.get('symbol')} ({p.get('project')}): {p.get('apy', 0):.2f}% APY, ${p.get('tvlUsd', 0):,.0f} TVL")
    except Exception as e:
        logger.error(f"Failed to fetch candidate pools: {e}")
        raise HTTPException(status_code=502, detail="Market data unavailable")

    # --- STEP 3: REBALANCE SIMULATION ---
    # Use TradeSizer to see what the IDEAL portfolio looks like
    try:
        optimization = clients['sizer'].recommend(
            candidate_pools, 
            portfolio_value, 
            current_pool_id=current_pool_id if current_positions else None
        )
        target_apy = optimization.get('weighted_apy', 0)
        optimal_allocations = optimization.get('allocations', [])
    except Exception as e:
        logger.error(f"TradeSizer optimization failed: {e}")
        target_apy = 0.0
        optimal_allocations = []

    apy_delta = target_apy - current_avg_apy
    
    if source == "api":
        logger.info(f"📊 Optimization Result:")
        logger.info(f"   Current APY: {current_avg_apy:.2f}%")
        logger.info(f"   Target APY:  {target_apy:.2f}%")
        logger.info(f"   APY Delta:   {apy_delta:.2f}%")
        if optimal_allocations:
            top_alloc = optimal_allocations[0]
            logger.info(f"   Recommended: {top_alloc.symbol} ({top_alloc.protocol}) - {top_alloc.apy:.2f}% APY")


    # --- STEP 4: PROFITABILITY & CONCENTRATION GATEKEEPER ---
    # Check if moving makes financial sense (gas + slippage vs. gain)
    is_profitable = False
    total_cost = 0
    gas_data = {}
    concentration_risk = False
    
    # Dynamic hurdle rate: 0.75% for larger portfolios (matches Lifeboat Protocol)
    hurdle_rate = 0.75 if portfolio_value >= 100_000 else 0.5

    if apy_delta > hurdle_rate:
        # Use dot notation for dataclass attributes (PoolAllocation has .tvl_usd attribute)
        target_pool_tvl = optimal_allocations[0].tvl_usd if optimal_allocations else 1e9
        
        # NEW: Concentration Risk Check
        # Ensure portfolio position doesn't exceed 5% of target pool's TVL
        # (Anything >5% means we become the liquidity - exit risk catastrophic)
        concentration_ratio = portfolio_value / target_pool_tvl
        if concentration_ratio > 0.05:
            concentration_risk = True
            logger.warning(
                f"⚠️  Concentration Risk: Portfolio (${portfolio_value:,.0f}) is {concentration_ratio:.1%} "
                f"of target pool TVL (${target_pool_tvl:,.0f}). Exceeds 5% threshold. REJECTING MOVE."
            )
        else:
            try:
                is_profitable, total_cost, gas_data = clients['gas'].should_rebalance(
                    current_avg_apy / 100, 
                    target_apy / 100, 
                    portfolio_value,
                    pool_tvl=target_pool_tvl
                )
            except Exception as e:
                logger.error(f"Gas optimization failed: {e}")
                gas_data = {'monthly_gain': 0}

            # Compute real slippage for the proposed swap and inject into gas_data
            try:
                current_sym = current_positions[0]['pool'].get('symbol', 'USDC') if current_positions else 'USDC'
                target_sym = optimal_allocations[0].symbol if optimal_allocations else 'USDC'
                # Strip pair notation (e.g. "USDC-USDT" -> "USDC")
                current_sym = current_sym.split('-')[0].split('/')[0].upper()
                target_sym = target_sym.split('-')[0].split('/')[0].upper()
                slippage_val = clients['slippage'].get_expected_slippage_sync(
                    current_sym, target_sym, portfolio_value
                )
                gas_data['estimated_slippage'] = slippage_val
                logger.info(f"   Slippage estimate ({current_sym}→{target_sym}): {slippage_val:.4%}")
            except Exception as e:
                logger.warning(f"Slippage estimation failed: {e}")
                gas_data['estimated_slippage'] = 0.0005

        if is_profitable:
            logger.info(f"💰 Profitability Check: PASS")
            logger.info(f"   Est. Cost: ${total_cost:.2f}")
            logger.info(f"   Est. Monthly Gain: ${gas_data.get('monthly_gain', 0):.2f}")
        else:
            logger.info(f"💰 Profitability Check: FAIL")
            logger.info(f"   Est. Cost: ${total_cost:.2f} > Monthly Gain ${gas_data.get('monthly_gain', 0):.2f}")

    # --- ML SHADOW MODE ---
    # Analyze the heuristic's recommended target pool(s) with ML models
    ml_shadow_data = {}
    if optimal_allocations and clients.get('feature_pipeline'):
        try:
            target_pool = optimal_allocations[0] # Analyze the top pick
            pool_id = target_pool.pool_id
            
            # 1. Fetch History from DB
            start_date = (datetime.utcnow() - timedelta(days=60)).strftime('%Y-%m-%d')
            df_history = clients['feature_pipeline'].load_data_for_pool(pool_id, start_date=start_date)
            
            if not df_history.empty and len(df_history) > 30:
                # 2. Create Features
                df_features = clients['feature_pipeline'].create_features(
                    df_history, 
                    gas_price_wei=30e9,  # 30 Gwei default
                    market_sentiment=0.5
                )
                
                # 3. Prepare Input Sequence (Last 30 days)
                X_seq, _, _ = clients['feature_pipeline'].prepare_sequences(
                    df_features, 
                    sequence_length=30, 
                    prediction_horizon=7
                )
                
                if len(X_seq) > 0:
                    # Take the most recent sequence
                    X_input = X_seq[-1:]
                    
                    # 4. LSTM Prediction
                    if clients.get('lstm_ready'):
                        pred_apy = clients['lstm'].predict(X_input)[0]
                    else:
                        pred_apy = -1.0
                        
                    # 5. Risk Scoring
                    if clients.get('risk_ready'):
                        risk_input_df = clients['feature_pipeline'].get_risk_features({
                            'audit_score': 50,
                            'age_days': 100, 
                            'tvl_usd': target_pool.tvl_usd
                        })
                        risk_score = clients['risk_scorer'].calculate_risk_score(risk_input_df)[0]
                        risk_class = clients['risk_scorer'].predict(risk_input_df)[0] # 0,1,2
                    else:
                        risk_score = -1
                        risk_class = -1
                        
                    ml_shadow_data = {
                        "target_pool": pool_id,
                        "lstm_predicted_apy_7d": float(pred_apy),
                        "xgboost_risk_score": float(risk_score),
                        "xgboost_risk_class": int(risk_class),
                        "model_confidence": "shadow_mode"
                    }
            else:
                pass # Insufficient history

        except Exception as e:
            logger.error(f"ML Shadow Mode failed: {e}")

    # Prepare market context
    # Build target_metadata from the top PoolAllocation so the dashboard can show TVL, symbol, chain
    _top = optimal_allocations[0] if optimal_allocations else None
    target_metadata = {
        "symbol":   _top.symbol   if _top else "Unknown",
        "tvlUsd":   _top.tvl_usd  if _top else 0,
        "pool":     _top.pool_id  if _top else "",
        "chain":    _top.chain    if _top else "Ethereum",
        "project":  _top.protocol if _top else "",
        "apy":      _top.apy      if _top else 0,
    } if _top else {}

    # Current pool symbol for the "Current:" comparison row
    _cur_pool = current_positions[0]['pool'] if current_positions else {}
    current_pool_symbol = _cur_pool.get('symbol', 'CASH') if _cur_pool else 'CASH'

    market_context = {
        "runner_ups": [{"symbol": p.get('symbol'), "apy": p.get('apy'), "tvl": p.get('tvlUsd')} 
                       for p in candidate_pools[:3]],
        "target_metadata":    target_metadata,
        "current_pool_symbol": current_pool_symbol,
        "metrics":            gas_data,   # populated after gas check; {} for early-exit HOLDs
        "portfolio_value_usd": portfolio_value,
        "analysis_timestamp": datetime.utcnow().isoformat(),
        "ml_shadow_mode": ml_shadow_data,
        "source": source
    }

    # --- STEP 5: FINAL DECISION & RECORDING ---
    prediction_record = {
        "current_pool_id": current_pool_id,
        "current_pool_apy": current_avg_apy,
        "capital_usd": portfolio_value,
        "market_context": market_context,
        "gas_cost": total_cost,
        "slippage": 0.0, # Placeholder
        "predicted_apy": target_apy, # Heuristic APY as baseline prediction
    }
    
    response = None

    # 0. FORCE EXECUTION
    if force_execution:
        target_alloc = {a.pool_id: a.allocation_usd for a in optimal_allocations} if optimal_allocations else {}
        response = PredictionResponse(
            action="REBALANCE",
            target_allocations=target_alloc or current_allocations,
            confidence=1.0,
            reason="[ADMIN] rebalance execution.",
            estimated_gas=total_cost,
            net_apy_gain=apy_delta,
            metrics={},
            market_context=market_context
        )
        prediction_record.update({
            "prediction_type": "REBALANCE",
            "target_pool_id": list(target_alloc.keys())[0] if target_alloc else None,
            "target_pool_apy": target_apy,
            "confidence": 1.0,
            "reason": "[ADMIN] Forced execution"
        })

    # 1. Position is Optimal
    elif apy_delta <= hurdle_rate:
        response = PredictionResponse(
            action="HOLD",
            target_allocations=current_allocations,
            confidence=0.85,
            reason=f"Current position is near-optimal. APY gain {apy_delta:.2f}% < hurdle {hurdle_rate}%. [HOLD_OPTIMAL]",
            estimated_gas=0.0,
            net_apy_gain=apy_delta,
            metrics={"hurdle_rate": hurdle_rate},
            market_context=market_context
        )
        prediction_record.update({
            "prediction_type": "HOLD",
            "target_pool_id": None, 
            "target_pool_apy": target_apy,
            "confidence": 0.85,
            "reason": response.reason
        })
    
    # 2. Concentration Risk
    elif concentration_risk:
        target_pool_tvl = optimal_allocations[0].tvl_usd if optimal_allocations else 1e9
        concentration_ratio = (portfolio_value / target_pool_tvl) * 100
        response = PredictionResponse(
            action="HOLD",
            target_allocations=current_allocations,
            confidence=0.3,
            reason=f"CONCENTRATION RISK: Position ({concentration_ratio:.1f}% of TVL) > 5%.",
            estimated_gas=0.0,
            net_apy_gain=apy_delta,
            metrics={"concentration_ratio": concentration_ratio},
            market_context=market_context
        )
        prediction_record.update({
            "prediction_type": "HOLD",
            "target_pool_id": optimal_allocations[0].pool_id if optimal_allocations else None,
            "target_pool_apy": target_apy,
            "confidence": 0.3,
            "reason": response.reason
        })

    # 3. Not Profitable
    elif apy_delta > hurdle_rate and not is_profitable:
        response = PredictionResponse(
            action="HOLD",
            target_allocations=current_allocations,
            confidence=0.6,
            reason=f"High friction. Cost ${total_cost:.2f} > Monthly Gain.",
            estimated_gas=total_cost,
            net_apy_gain=apy_delta,
            metrics={k: float(v) for k, v in gas_data.items()},
            market_context=market_context
        )
        prediction_record.update({
            "prediction_type": "HOLD",
            "target_pool_id": optimal_allocations[0].pool_id if optimal_allocations else None,
            "target_pool_apy": target_apy,
            "confidence": 0.6,
            "reason": response.reason
        })

    # 4. Valid Opportunity
    else:
        monthly_gain = gas_data.get('monthly_gain', 0)
        target_alloc = {a.pool_id: a.allocation_usd for a in optimal_allocations} if optimal_allocations else {}
        response = PredictionResponse(
            action="REBALANCE",
            target_allocations=target_alloc or current_allocations,
            confidence=0.92,
            reason=f"Strong opportunity: {current_avg_apy:.2f}% → {target_apy:.2f}%. "
                   f"Monthly gain: ${monthly_gain:.2f} after costs.",
            estimated_gas=total_cost,
            net_apy_gain=apy_delta,
            metrics={k: float(v) for k, v in gas_data.items()},
            market_context=market_context
        )
        prediction_record.update({
            "prediction_type": "REBALANCE",
            "target_pool_id": list(target_alloc.keys())[0] if target_alloc else None,
            "target_pool_apy": target_apy,
            "confidence": 0.92,
            "reason": response.reason
        })

    # --- RECORD PREDICTION TO DB ---
    if clients.get('tracker'):
        try:
            clients['tracker'].record_prediction(prediction_record)
        except Exception as e:
            logger.error(f"Failed to record prediction: {e}")
    else:
        logger.warning("PredictionTracker not available, prediction NOT saved.")

    return response

@app.get("/admin/stats")
async def get_ai_performance():
    """
    Returns the accuracy and financial performance of the rebalancer.
    Use this to verify if filters (Mean Reversion, Gas, Liquidity) are working.
    Shows overall accuracy, profit tracked, and recent decision history.
    """
    if not clients.get('tracker'):
        raise HTTPException(status_code=503, detail="PredictionTracker not initialized")
    
    try:
        # 1. Get Aggregate Stats
        stats = clients['tracker'].get_detailed_stats()
        
        # 2. Get Recent Predictions (last 5)
        recent = clients['tracker'].get_all_predictions(limit=5)
        
        return {
            "status": "success",
            "timestamp": datetime.utcnow().isoformat(),
            "performance": stats,
            "recent_activity": recent
        }
    except Exception as e:
        logger.error(f"Stats fetch failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not retrieve performance data")

@app.get("/admin/vault/assets")
async def get_vault_assets():
    """
    Fetch real-time on-chain assets from the StrategyHub contract.
    Used by the dashboard to show the 'Agent Wallet' state.
    """
    hub_address = clients.get('hub_address')
    w3 = clients.get('w3')
    
    # Also get Vault Address for direct check
    vault_address = os.getenv("VAULT_CONTRACT_ADDRESS")
    if not vault_address and os.path.exists(".env"):
         # Try to load from .env if not in env vars
         try:
             with open(".env") as f:
                 for line in f:
                     if "VAULT_CONTRACT_ADDRESS" in line:
                         vault_address = line.strip().split("=")[1]
         except: pass

    if not hub_address or not w3:
        raise HTTPException(status_code=503, detail="Vault connection not initialized")
        
    try:
        logger.info(f"🔍 Checking Assets | Hub: {hub_address} | Vault: {vault_address}")

        # Load yield/ROI state
        from src.core.state_store import StateStore as _StateStore
        state = _StateStore().load_state()

        aave_usd = 0.0
        comp_usd = 0.0
        idle_usd = 0.0
        total_usd = 0.0

        # 1. Hub Balances (Graceful Failure)
        try:
            hub = w3.eth.contract(address=hub_address, abi=clients['hub_abi'])
            balances = await asyncio.to_thread(hub.functions.getBalances().call)
            
            aave_usd = float(balances[0]) / 1e6
            comp_usd = float(balances[1]) / 1e6
            idle_usd = float(balances[2]) / 1e6
            total_usd = float(balances[3]) / 1e6
        except Exception as hub_err:
            logger.warning(f"StrategyHub call failed (Address: {hub_address}): {hub_err}. Proceeding to fallback.")
        
        # 2. Fallback/Direct Vault Check if Hub reports 0 but we have a Vault address
        vault_direct = 0.0
        if total_usd == 0 and vault_address:
            try:
                # USDC Address (Mainnet/Anvil)
                USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
                abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
                usdc_contract = w3.eth.contract(address=USDC, abi=abi)
                raw_bal = await asyncio.to_thread(usdc_contract.functions.balanceOf(vault_address).call)
                vault_direct = float(raw_bal) / 1e6
                
                if vault_direct > 0:
                    logger.info(f"⚠️ Hub reports 0, but Vault has ${vault_direct} USDC directly.")
                    idle_usd = vault_direct # Assume it's idle if in Vault
                    total_usd = vault_direct
            except Exception as e:
                logger.warning(f"Failed direct vault check: {e}")

        return {
            "status": "success",
            "hub_address": hub_address,
            "vault_address": vault_address,
            "assets": {
                "Aave V3": aave_usd,
                "Compound V3": comp_usd,
                "Idle USDC": idle_usd
            },
            "total_usd": total_usd,
            # ── Yield & ROI from StateStore ──────────────────────────────
            "net_roi_pct": state.get("net_roi_pct", 0.0),
            "total_yield_earned": state.get("total_yield_earned", 0.0),
            "initial_capital": state.get("initial_capital", 0.0),
            "last_harvest_time": state.get("last_harvest_time"),
            # ─────────────────────────────────────────────────────────────
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Failed to fetch vault assets: {e}")
        # Try to return partial data if possible or at least the address being used
        return {
             "status": "error",
             "error": str(e),
             "hub_used": hub_address,
             "vault_used": vault_address
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


@app.post("/admin/harvest")
async def trigger_harvest(days: float = 30.0):
    """
    Simulate yield harvest by fast-forwarding Anvil time by `days` days.
    Reads aToken/cToken balance growth as yield earned and updates StateStore.
    """
    hub_address = clients.get('hub_address')
    w3 = clients.get('w3')
    if not hub_address or not w3:
        raise HTTPException(status_code=503, detail="Hub connection not initialized")

    try:
        HUB_ABI = [{"name":"getBalances","type":"function","inputs":[],"outputs":[
            {"name":"aaveBalance","type":"uint256"},{"name":"compoundBalance","type":"uint256"},
            {"name":"idleBalance","type":"uint256"},{"name":"total","type":"uint256"}
        ]}]
        hub = w3.eth.contract(address=hub_address, abi=HUB_ABI)

        balances_before = await asyncio.to_thread(hub.functions.getBalances().call)
        total_before = balances_before[3]

        # Fast-forward time and mine blocks
        seconds = int(days * 86400)
        await asyncio.to_thread(w3.provider.make_request, "evm_increaseTime", [seconds])
        await asyncio.to_thread(w3.provider.make_request, "anvil_mine", [100])

        balances_after = await asyncio.to_thread(hub.functions.getBalances().call)
        total_after = balances_after[3]

        yield_earned_usd = max(0.0, (total_after - total_before) / 1e6)

        from src.core.state_store import StateStore as _SS
        ss = _SS()
        st = ss.load_state()
        initial_capital = st.get("initial_capital", 0.0) or (total_before / 1e6)
        prev_yield = st.get("total_yield_earned", 0.0)
        new_total_yield = prev_yield + yield_earned_usd
        net_roi_pct = (new_total_yield / initial_capital * 100.0) if initial_capital > 0 else 0.0

        import time as _time
        ss.update_state(
            initial_capital=initial_capital,
            total_yield_earned=round(new_total_yield, 6),
            net_roi_pct=round(net_roi_pct, 6),
            last_harvest_time=_time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            last_harvest_yield=round(yield_earned_usd, 6),
            current_total_value=round(total_after / 1e6, 6),
        )

        return {
            "status": "success",
            "days_simulated": days,
            "yield_earned_usd": round(yield_earned_usd, 4),
            "total_yield_earned": round(new_total_yield, 4),
            "net_roi_pct": round(net_roi_pct, 4),
            "initial_capital": round(initial_capital, 2),
            "total_value_usd": round(total_after / 1e6, 2),
            "aave_usd": round(balances_after[0] / 1e6, 4),
            "compound_usd": round(balances_after[1] / 1e6, 4),
        }
    except Exception as e:
        logger.error(f"Harvest failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/harvest")
async def get_harvest_state():
    """Return current yield/ROI state without simulating."""
    from src.core.state_store import StateStore as _SS
    state = _SS().load_state()
    return {
        "net_roi_pct": state.get("net_roi_pct", 0.0),
        "total_yield_earned": state.get("total_yield_earned", 0.0),
        "initial_capital": state.get("initial_capital", 0.0),
        "last_harvest_time": state.get("last_harvest_time"),
        "last_harvest_yield": state.get("last_harvest_yield", 0.0),
        "current_total_value": state.get("current_total_value", 0.0),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
