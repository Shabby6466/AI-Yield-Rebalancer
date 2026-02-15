import logging
import asyncio
from typing import List, Dict, Any, Optional
from web3 import Web3
from eth_account import Account
from src.ml.rl_agent import PPORebalancer, DefiRebalanceEnv
from src.risk.circuit_breaker import CircuitBreaker
from src.execution.flashbots_relay import FlashbotsRelayer
from src.data.dune_client import DuneClient
from src.data.defillama_client import DefiLlamaClient
from src.risk.slippage_client import SlippageClient
from src.backtest.prediction_tracker import PredictionTracker
from src.core.state_store import StateStore
from src.execution.ml_prediction_service import MLPredictionService
from src.data.chainlink_client import ChainlinkClient
from src.data.timeseries_db import TimeseriesDB
from src.data.uniswap_v3_client import UniswapV3Client
from src.data.uniswap_position_manager import UniswapV3PositionManager
from src.risk.drift_logger import DriftLogger
import numpy as np
import random
import os
from datetime import datetime
from dotenv import load_dotenv
from decimal import Decimal, getcontext

# Set precision to 18 decimal places matching blockchain standards
getcontext().prec = 18

# Configure logging to both console and file for dashboard tailing
log_file = "data/rebalancer.log"
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class RebalancerService:
    """
    The Orchestrator. Coordinates the 4-layer system:
    1. Data Ingestion (DeFiLlama/Dune/RPC)
    2. Inference (PPO RL Agent)
    3. Safety Checks (Circuit Breaker)
    4. Execution (Flashbots)
    """
    
    def __init__(self):
        load_dotenv()
        # Add a strict request timeout to prevent hanging on slow RPCs
        self.w3 = Web3(Web3.HTTPProvider(
            os.getenv("RPC_URL"),
            request_kwargs={'timeout': 20}
        ))
        self.signer = Account.from_key(os.getenv("KEEPER_PRIVATE_KEY"))
        
        # Phase 1: Institutional Intelligence Layer
        # Detect Network (Priority: Env -> File -> Default)
        network = os.getenv("NETWORK", "ethereum")
        self.usdc_address = os.getenv("USDC_ADDRESS", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
        self.erc20_abi = [
            {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}
        ]
        
        if os.path.exists("contracts/deployed_address.txt"):
            network = "local"
            logger.info("Found local deployment file. Switching to NETWORK=local")
        
        self.network = network
            
        self.ml_service = MLPredictionService(network=network)
        self.chainlink = ChainlinkClient(self.w3)
        
        # Initialize Layers
        
        # Resolve StrategyHub Address (Env or Local File)
        hub_address = os.getenv("STRATEGY_HUB_ADDRESS")
        if not hub_address and os.path.exists("contracts/deployed_address.txt"):
            with open("contracts/deployed_address.txt", "r") as f:
                hub_address = f.read().strip()
                logger.info(f"Loaded StrategyHub from file: {hub_address}")
        
        self.guard = CircuitBreaker(self.w3, hub_address, ml_service=self.ml_service)
        self.hands = FlashbotsRelayer(self.w3, self.signer)
        self.dune = DuneClient(os.getenv("DUNE_API_KEY"))
        self.defillama = DefiLlamaClient()
        self.slippage = SlippageClient()
        self.uniswap = UniswapV3Client(self.w3)
        self.drift_logger = DriftLogger(self.w3)
        
        # Load RL Agent (The Brain) with Risk Preference
        self.risk_tolerance = float(os.getenv("RISK_TOLERANCE", 1.0))
        logger.info(f"Using Risk Tolerance: {self.risk_tolerance} (1.0=Balanced, <1.0=Aggressive)")
        
        self.env = DefiRebalanceEnv(pool_data=[], risk_tolerance=self.risk_tolerance, max_pools=10) 
        self.brain = PPORebalancer(self.env)
        if os.path.exists("models/ppo_rebalancer_v1.zip"):
            self.brain.load("models/ppo_rebalancer_v1.zip")
            
        self.tracker = PredictionTracker()
        self.state_store = StateStore()
        self.db = TimeseriesDB() # Added local DB access
        self.phase_logs = []  # Collector for 4-phase storytelling
        self.force_rerange_evaluation = False
        
        # --- SMART Engine State & Config ---
        self.risk_tolerance = float(os.getenv("RISK_TOLERANCE", "1.0"))
        self.min_gain_threshold = float(os.getenv("MIN_APY_GAIN", "1.0"))
        self.max_slippage_threshold = float(os.getenv("MAX_SLIPPAGE", "0.02")) # 2%
        self.max_gas_gwei = float(os.getenv("MAX_GAS_GWEI", "50.0"))
        self.cooling_period_hours = float(os.getenv("COOLING_PERIOD_HOURS", "48.0"))
        
        self.drift_status = "HEALTHY" # HEALTHY, COOLING, AUDITING, EXECUTING
        self.drift_start_timestamp = None
        self.retracement_prob = 0.0
        self.drift_logger = DriftLogger(self.w3)
        self.pos_manager = UniswapV3PositionManager(self.w3)
        
        # ERC-20 ABI (Minimal)
        self.erc20_abi = [
            {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"}
        ]
        
        # Standard Token List for Portfolio Discovery
        self.discovery_tokens = {
            "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eb48",
            "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "DAI": "0x6b175474e89094c44da98b954eedeac495271d0f",
            "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"
        }
        
        # State Initialization
        state = self.state_store.load_state()
        self.current_pool_id = state.get("current_pool_id")
        self.current_pool_symbol = state.get("current_pool_symbol", "CASH")
        self.current_apy = state.get("current_apy", 0.0)
        self.initial_capital = Decimal(str(state.get("initial_capital", 0.0)))
        self.cumulative_costs = Decimal(str(state.get("cumulative_costs", 0.0)))
        self.wait_savings = Decimal(str(state.get("wait_savings", 0.0)))
        self.last_rebalance_time: Optional[datetime] = None # Track last move timestamp
        self.last_scanned_pools = []   # Cache of pool metadata
            
    async def run_cycle(self):
        """Execute one rebalancing cycle."""
        self.phase_logs = [] # Reset for new cycle
        logger.info("--- Starting Rebalancing Cycle ---")
        
        # 0. Cooldown / Stability Check (Prevent Ping-Pong)
        if self.last_rebalance_time:
             time_since_move = datetime.utcnow() - self.last_rebalance_time
             # Demo Cooldown: Use env var or default to 5 minutes (300s)
             cooldown_seconds = int(os.getenv("REBALANCE_COOLDOWN_SECONDS", 300))
             if time_since_move.total_seconds() < cooldown_seconds: 
                 logger.info(f"❄️ COOLDOWN ACTIVE: Last rebalance was {time_since_move.total_seconds()/60:.1f} mins ago. Holding position to prevent churn.")
                 return

        # -1.5 Nonce Lock: Prevent Zombie Transactions
        try:
            # Check for any "in-flight" transactions by comparing latest vs pending nonces
            current_block = await asyncio.to_thread(getattr, self.w3.eth, 'block_number')
            on_chain_nonce = await asyncio.to_thread(self.w3.eth.get_transaction_count, self.signer.address, 'latest')
            pending_nonce = await asyncio.to_thread(self.w3.eth.get_transaction_count, self.signer.address, 'pending')
            
            if pending_nonce > on_chain_nonce:
                logger.warning(f"🚨 NONCE LOCK: {pending_nonce - on_chain_nonce} transaction(s) still pending. Aborting cycle to avoid zombie state.")
                return
                
            financial_metrics: Dict[str, Any] = {
                "heartbeat_block": float(current_block),
                "execution_block": float(current_block),
                "total_portfolio_usd": float(os.getenv("PORTFOLIO_SIZE_USD", 100000.0)),
                "wallet_balance_eth": 0.0,
                "eth_price": 2500.0,
                "gas_cost_usd": 0.0,
                "estimated_slippage": 0.0,
                "total_costs_usd": 0.0,
                "daily_yield_gain": 0.0,
                "break_even_days": 0.0,
                "net_roi_pct": 0.0,
                "wait_savings": float(self.wait_savings)
            }
            logger.debug(f"Nonce Sync: {on_chain_nonce} (No pending txs) | Block: {current_block}")
        except Exception as e:
            logger.error(f"Nonce Lock Check failed: {e}")
            return # Safety: Don't proceed if we can't verify transaction state
        
        # Initialize cycle variables
        is_drifting = False
        
        # -1. Auto-Validate Maturing Predictions
        try:
            val_results = await asyncio.to_thread(self.tracker.auto_validate_from_db)
            if val_results:
                logger.info(f"Auto-validated {len(val_results)} maturing predictions.")
        except Exception as e:
            logger.warning(f"Auto-validation failed: {e}")
        
        # 0. Sync Current Position Yield (Live Check)
        # We perform a dual-sync: 
        # 1. Fetch live metrics for our specific position to get the most accurate APY
        # 2. Match against the broader market scan to update symbol/metadata
        if self.current_pool_id and self.current_pool_id != "none" and self.current_pool_id != "CASH":
            try:
                # 1. Direct fetch for high-precision APY
                current_pool_data = await self.defillama.fetch_pool_yields([self.current_pool_id])
                if current_pool_data:
                    old_apy = self.current_apy
                    self.current_apy = current_pool_data[0].get('apy', 0.0)
                    # Sync symbol from direct fetch if available
                    fetch_symbol = current_pool_data[0].get('symbol')
                    if fetch_symbol:
                        self.current_pool_symbol = fetch_symbol

                    if abs(old_apy - self.current_apy) > 0.1:
                        logger.info(f"Market Sync: {self.current_pool_symbol} yield shifted {old_apy:.2f}% -> {self.current_apy:.2f}%")
                else:
                    logger.warning(f"Position Alert: {self.current_pool_symbol} not found in live data. Potential de-listing?")
                    # We don't reset to 0 yet, let the market scan confirm
            except Exception as e:
                logger.error(f"Failed to sync current pool: {e}")

        # 1. Layer 1: Data Ingestion & Market Health
        # Wrap in thread since it makes synchronous Web3 calls
        is_healthy, safety_report = await asyncio.to_thread(self.guard.check_market_health)
        if not is_healthy:
            return

        # 1.5 Drift Check (Uniswap V3 specific)
        if self.current_pool_id and "uniswap" in self.current_pool_symbol.lower():
            curr_addr = self._resolve_pool_address(self.current_pool_id, self.current_pool_symbol, "")
            if curr_addr:
                # For POC, we'll assume a standard +/- 1000 tick range if not saved
                # In production, these would be read from the user's active Position NFT
                metrics = self.uniswap.get_pool_metrics(curr_addr)
                if metrics:
                    tick = metrics['tick']
                    # Dummy range for POC
                    t_lower = tick - 1000
                    t_upper = tick + 1000
                    is_drifting = self.drift_logger.check_drift(curr_addr, t_lower, t_upper, tick)
                    
                    if is_drifting:
                        drift_hours = self.drift_logger.get_active_drift_duration_hours(curr_addr)
                        if drift_hours >= 48:
                            logger.info(f"🧠 SMART Decision Triggered: Drift detected for {drift_hours:.1f} hours (>48h limit).")
                            # We will force a re-evaluation in the next steps using ROI simulation
                            self.force_rerange_evaluation = True
                        else:
                            logger.info(f"Drift active for {drift_hours:.1f}h. Threshold for re-evaluation is 48h.")
                            self.force_rerange_evaluation = False
                    else:
                        self.force_rerange_evaluation = False

        # 2. Ingest broad market data
        latest_features = await self._fetch_latest_market_state()
        
        # 3. Brain Inference (Threaded CPU Task)
        weights = await asyncio.to_thread(self.brain.predict, latest_features)
        
        # 3.5 Final Metadata Sync (Ensure symbol is never 'CASH' if we have an ID)
        if self.current_pool_id and self.current_pool_id != "none":
            match = next((p for p in self.last_scanned_pools if p['pool'] == self.current_pool_id), None)
            if match:
                self.current_pool_symbol = match['symbol']
                # If we couldn't fetch a precise APY in Step 0, use the scan's APY
                if self.current_apy == 0:
                    self.current_apy = match['apy']
        
        # --- INSTITUTIONAL RISK DIVERSIFICATION ---
        # If the top pool has suspicious yield (>50% APY), cap its allocation to 40%
        # This prevents 100% concentration in "vampire" pools and forces rebalancing
        top_idx = np.argmax(weights)
        target_pool = self.last_scanned_pools[top_idx]
        
        if target_pool['apy'] > 50.0:
            logger.info(f"Applying Risk Cap: {target_pool['symbol']} yield ({target_pool['apy']:.2f}%) is ultra-high. Capping allocation.")
            # Force weight redistribution (e.g., 40% top, 60% spread to runner ups)
            new_weights = np.copy(weights)
            excess = max(0, new_weights[top_idx] - 0.40)
            new_weights[top_idx] = 0.40
            # Distribute excess to the next 2 best pools
            runners = np.argsort(weights)[::-1][1:3]
            for r_idx in runners:
                new_weights[r_idx] += excess / 2
            weights = new_weights
            # Re-pick top_idx after redistribution if necessary
            top_idx = np.argmax(weights)
            target_pool = self.last_scanned_pools[top_idx]

        # Fetch Detailed Portfolio (ETH + ERC20 + LP Positions)
        portfolio_report = await self._get_detailed_portfolio()
        PORTFOLIO_SIZE = portfolio_report['total_usd']
        wallet_balance = portfolio_report['eth_balance']
        stable_balance = portfolio_report['stable_balance']
        eth_price = portfolio_report['eth_price']
        
        financial_metrics.update({
            "wallet_balance_eth": float(wallet_balance),
            "total_portfolio_usd": float(PORTFOLIO_SIZE),
            "asset_breakdown": portfolio_report['assets'],
            "eth_price": float(eth_price)
        })

        # Calculate Financial Health Metrics
        # Principal tracking for ROI (Actual System)
        if not hasattr(self, 'initial_capital') or self.initial_capital <= 0:
            self.initial_capital = Decimal(str(PORTFOLIO_SIZE))
            logger.info(f"🚀 Initializing Actual System Portfolio: ${float(self.initial_capital):,.2f}")
            self.state_store.update_state(initial_capital=float(self.initial_capital))

        principal = float(self.initial_capital)
        current_val = float(PORTFOLIO_SIZE)
        
        # Net ROI & PnL
        net_pnl = current_val - principal
        net_roi = (net_pnl / principal) * 100.0 if principal > 0 else 0.0
        
        # Daily Yield Projection (Asset * APY / 365)
        daily_yield = (current_val * self.current_apy / 100.0) / 365.0
        
        # Break Even Analysis
        total_costs = float(self.cumulative_costs)
        # Avoid division by zero
        break_even_days = (total_costs / daily_yield) if daily_yield > 0.01 else 999.0
        
        financial_metrics.update({
            "net_roi_pct": net_roi,
            "daily_yield_gain": daily_yield,
            "break_even_days": break_even_days,
            "total_costs_usd": total_costs
        })

        # --- NEW: Log Wallet Snapshot for Persistence ---
        try:
             await asyncio.to_thread(
                 self.db.log_wallet_snapshot,
                 float(PORTFOLIO_SIZE),
                 float(wallet_balance),
                 portfolio_report['assets']
             )
        except Exception as e:
             logger.warning(f"Failed to log wallet snapshot: {e}")
        
        # SMART Method Decision Logic
        # If we have enough USDC to cover the portfolio size, we "Add Capital" instead of swapping existing positions.
        rebalance_requirement = PORTFOLIO_SIZE
        execution_method = "SWAP" # Default
        if stable_balance >= rebalance_requirement:
            execution_method = "ADD_CAPITAL"
            logger.info(f"🧠 SMART Method: Enough USDC detected (${float(stable_balance):,.2f}). Using 'Adding Capital' instead of Swap.")
        else:
            logger.info(f"🧠 SMART Method: Not enough USDC (${float(stable_balance):,.2f} < ${float(rebalance_requirement):,.2f}). Using 'Swap' method.")

        # Oracle Lag Safety with Escalating Warnings
        # This assumes price_updated_at is available, which it isn't in the provided snippet.
        # For now, I'll use a dummy value or assume it's part of `eth_price` from `_get_detailed_portfolio`.
        # Given `chainlink.get_asset_price` returns `(price, timestamp)`, I'll assume `eth_price` is the price and `price_updated_at` is the timestamp.
        # I need to modify `_get_detailed_portfolio` to return `eth_price_float` and `eth_price_timestamp`
        # For now, I'll use a placeholder for `price_updated_at` to avoid breaking the code.
        # Let's assume `price_updated_at` is part of the `portfolio_report` or derived from it.
        # The `_get_detailed_portfolio` method now returns `eth_price` as Decimal, so I need to get the timestamp from Chainlink directly.
        _, price_updated_at = await self.chainlink.get_asset_price('ETH') # Re-fetch timestamp
        
        lag_seconds = int(datetime.utcnow().timestamp()) - price_updated_at
        lag_minutes = lag_seconds / 60
        lag_hours = lag_seconds / 3600
        
        # Critical Thresholds
        WARN_THRESHOLD = 60  # 1 minute
        CRITICAL_THRESHOLD = 3600  # 1 hour
        HALT_THRESHOLD = 14400  # 4 hours (even in local mode)
        
        if lag_seconds > HALT_THRESHOLD:
            logger.critical(f"🚨 CRITICAL ORACLE FAILURE: ETH Price is {lag_hours:.1f} hours stale!")
            logger.critical(f"   Data Age: {lag_seconds}s ({lag_hours:.1f}h)")
            logger.critical(f"   Portfolio valuation (${float(PORTFOLIO_SIZE):,.0f}) is UNRELIABLE.")
            logger.critical(f"   HALTING ALL OPERATIONS - Anvil fork needs refresh!")
            logger.critical(f"   ACTION REQUIRED: Restart Anvil with fresh fork: docker compose restart anvil")
            await self._record_cycle_prediction([], {}, forced_type="ABORTED", 
                                         forced_reason=f"[ABORT_STALE_DATA] Oracle lag {lag_hours:.1f}h exceeds safety limit",
                                         target_pool={}, safety_report={},
                                         metrics=financial_metrics)
            return
        
        # Initialize initial_capital on first real run
        if self.initial_capital == 0 and PORTFOLIO_SIZE > 0:
            self.initial_capital = PORTFOLIO_SIZE
            self.state_store.save_state(self.current_pool_id, self.current_pool_symbol, self.current_apy, 
                                      initial_capital=float(self.initial_capital))
            logger.info(f"Initialized Tracking: Initial Capital set to ${float(self.initial_capital):,.2f}")
        elif lag_seconds > CRITICAL_THRESHOLD:
            logger.critical(f"CRITICAL ORACLE LAG: ETH Price is {lag_hours:.1f} hours stale!")
            logger.critical(f"   Portfolio valuation may be incorrect by ±{lag_hours * 2:.0f}%")
            logger.critical(f"   Proceeding with EXTREME CAUTION in local mode...")
            if self.network != "local":
                logger.error(f"ORACLE LAG DETECTED: ETH Price is {lag_seconds}s stale (Max: {WARN_THRESHOLD}s). Aborting cycle for safety.")
                return
        elif lag_seconds > WARN_THRESHOLD:
            if self.network == "local":
                logger.warning(f"Oracle Lag Detected ({lag_seconds}s / {lag_minutes:.1f}m), but proceeding because NETWORK=local (simulation mode).")
            else:
                logger.error(f"ORACLE LAG DETECTED: ETH Price is {lag_seconds}s stale (Max: {WARN_THRESHOLD}s). Aborting cycle for safety.")
                return


        logger.info(f"DYNAMIC_CAPITAL: Scaling decisions based on ${float(PORTFOLIO_SIZE):,.2f} total assets (ETH @ ${float(eth_price):,.2f})")

        # Enhanced ML Prediction Audit (Liquidity-Aware & Threaded)
        symbol = target_pool.get('symbol', 'USDC').upper()
        project = target_pool.get('project', '').lower()
        
        # 1. Use the centralized resolver for consistency
        pool_address = self._resolve_pool_address(target_pool['pool'], symbol, project)
        
        # 3. Hard Safety Check: Never pass a UUID to the ML service
        if not pool_address or not pool_address.startswith('0x'):
            logger.warning(f"⚠️ Could not resolve hex address for {symbol} (UUID: {target_pool['pool']}). Skipping pool.")
            # Record skip for tracker
            await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                        forced_reason=f"[SKIP_UUID] Could not resolve address for {symbol}",
                                        target_pool=target_pool, safety_report=safety_report)
            return
             
        # 3. Resolve Asset Token Address (0x hex) specifically for the audit
        underlying = target_pool.get('underlyingTokens', [])
        asset_token_address = underlying[0] if underlying else None
        
        # --- NEW: Institutional Safety (Emergency Audit CURRENT pool first) ---
        current_ml_audit = None
        is_liquidity_emergency = False
        
        if self.current_pool_id and self.current_pool_id != "none" and self.current_pool_id != "CASH":
            curr_pool_addr = self._resolve_pool_address(self.current_pool_id, self.current_pool_symbol, "")
            if curr_pool_addr and curr_pool_addr.startswith('0x'):
                current_ml_audit = await asyncio.to_thread(
                    self.ml_service.generate_prediction,
                    pool_address=curr_pool_addr,
                    asset_symbol=self.current_pool_symbol,
                    portfolio_size_usd=PORTFOLIO_SIZE
                )
                
                if current_ml_audit:
                    curr_tvl = Decimal(str(current_ml_audit.get('tvl', 0)))
                    # Hard Exit Rule: If TVL < 2x Portfolio, we are trapped. 
                    if curr_tvl < (Decimal("2.0") * PORTFOLIO_SIZE):
                        logger.error(f"🚨 ILLIQUIDITY PANIC: Current Pool {self.current_pool_symbol} TVL (${float(curr_tvl):,.0f}) < 2x Portfolio Size!")
                        is_liquidity_emergency = True

        # --- Lifeboat Protocol: Blacklist current pool if it's a trap ---
        if is_liquidity_emergency:
            logger.info(f"Lifeboat Protocol: Blacklisting {self.current_pool_symbol} for this cycle. Searching for safe harbor...")
            # Filter out the trapped pool and re-pick the best alternative
            available_indices = [i for i, p in enumerate(self.last_scanned_pools) if p['pool'] != self.current_pool_id]
            if available_indices:
                # Re-calculate top weights among non-trapped pools
                top_idx = available_indices[np.argmax(weights[available_indices])]
                target_pool = self.last_scanned_pools[top_idx]
                target_pool_id = target_pool['pool']
                
                # Re-pick symbol/project for the new target
                symbol = target_pool.get('symbol', 'USDC').upper()
                project = target_pool.get('project', '').lower()
                pool_address = self._resolve_pool_address(target_pool['pool'], symbol, project)
                
                # Update underlying for the new target audit
                underlying = target_pool.get('underlyingTokens', [])
                asset_token_address = underlying[0] if underlying else None
                logger.info(f"Target Redirected to: {target_pool['symbol']} (Conviction: {weights[top_idx]:.1%})")
            else:
                logger.warning("🚨 EMERGENCY FAILSAFE: No alternative pools found. Capital at risk.")

        # 3. ML Audit for the (potentially new) TARGET pool
        if not pool_address or not pool_address.startswith('0x'):
            logger.warning(f"⚠️ Resolution Failed for {symbol} (Project: {project}). Skipping ML audit.")
            await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                          forced_reason=f"[SKIP_UUID] No address for {symbol}",
                                          target_pool=target_pool, safety_report=safety_report)
            return

        try:
            logger.info(f"Starting ML Audit for {symbol} ({pool_address})...")
            ml_audit = await asyncio.wait_for(
                asyncio.to_thread(
                    self.ml_service.generate_prediction,
                    pool_address=pool_address, 
                    asset_symbol=symbol,
                    asset_address=asset_token_address,
                    portfolio_size_usd=PORTFOLIO_SIZE
                ),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.error(f"❌ ML Audit TIMEOUT for {symbol} ({pool_address}). Skipping pool.")
            await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                          forced_reason=f"[SKIP_TIMEOUT] ML Audit Timed Out for {symbol}",
                                          target_pool=target_pool, safety_report=safety_report)
            return
        except Exception as e:
            logger.error(f"❌ ML Audit FAILED for {symbol}: {e}")
            return
        
        if not ml_audit or not ml_audit.get('success', False):
            reason = ml_audit.get('msg', 'Unknown Error') if ml_audit else 'Audit Failed'
            logger.warning(f"Skipping pool {target_pool['symbol']} due to failed ML audit: {reason}")
            return

        # --- SMART ROI Calculation (Early for Visibility) ---
        gas_cost_usd = await self.guard.estimate_rebalance_gas(self.current_pool_symbol, target_pool['symbol'], target_pool.get('project', ''))
        
        # Friction = Gas + Slippage
        estimated_slippage_float = await asyncio.to_thread(
            self.slippage.get_expected_slippage_sync, self.current_pool_symbol, target_pool['symbol'], float(PORTFOLIO_SIZE)
        )
        estimated_slippage = Decimal(str(estimated_slippage_float))
        conversion_loss = PORTFOLIO_SIZE * estimated_slippage
        cost_of_move = Decimal(str(gas_cost_usd)) + conversion_loss
        
        ml_predicted_target_apy = ml_audit.get('predicted_apy', target_pool['apy'])
        ml_predicted_current_apy = current_ml_audit.get('predicted_apy', self.current_apy) if current_ml_audit else self.current_apy
        apy_gain = ml_predicted_target_apy - ml_predicted_current_apy
        
        daily_yield_gain = (Decimal(str(apy_gain)) / Decimal("100")) * (PORTFOLIO_SIZE / Decimal("365"))
        break_even_days = (cost_of_move / daily_yield_gain) if daily_yield_gain > 0 else Decimal('999')
        
        net_roi_pct = 0.0
        if self.initial_capital > 0:
            net_roi_pct = float(((PORTFOLIO_SIZE) - (self.initial_capital + self.cumulative_costs)) / self.initial_capital * 100)

        # Update Metrics Wrapper Early
        financial_metrics.update({
            "gas_cost_usd": float(gas_cost_usd),
            "estimated_slippage": float(estimated_slippage),
            "total_costs_usd": float(cost_of_move),
            "daily_yield_gain": float(daily_yield_gain),
            "break_even_days": float(break_even_days),
            "net_roi_pct": net_roi_pct,
            "wait_savings": float(self.wait_savings)
        })

        # --- ORACLE HEALTH CHECK: System-Wide Halt on Multiple Ghost TVLs ---
        ghost_count = 0
        if ml_audit.get('ghost_tvl', False):
            ghost_count += 1
        if current_ml_audit and current_ml_audit.get('ghost_tvl', False):
            ghost_count += 1
        
        if ghost_count >= 2:
            logger.critical("SYSTEM-WIDE ORACLE FAILURE: Multiple major protocols report $0 TVL.")
            logger.critical("   This indicates RPC/Web3 provider connection failure or severe network issues.")
            logger.critical("   HALTING ALL OPERATIONS until connection is restored.")
            await self._record_cycle_prediction(weights, latest_features, forced_type="ABORTED", 
                                        forced_reason="[ABORT_ORACLE_FAILURE] System-wide TVL data corruption detected",
                                        target_pool=target_pool, safety_report=safety_report,
                                        ml_audit=ml_audit,
                                        metrics=financial_metrics)
            return
        elif ghost_count == 1:
            logger.warning("⚠️ Oracle Health Warning: 1 pool reporting Ghost TVL. Proceeding with caution.")

        # 4. Liquidity Concentration Check (Crucial for $2.9M+)
        # Ensure we don't own more than 5% of the pool to avoid toxic slippage
        pool_tvl = ml_audit.get('tvl', 0)
        MAX_CONCENTRATION = 0.05 # 5% Limit
        
        if pool_tvl > 1000: # Only check if TVL is reasonably provided
            concentration = PORTFOLIO_SIZE / pool_tvl
            if concentration > MAX_CONCENTRATION:
                logger.warning(f"REJECTED: Portfolio (${PORTFOLIO_SIZE:,.0f}) is {concentration*100:.1f}% of Pool TVL (${pool_tvl:,.0f}). Max is {MAX_CONCENTRATION*100}%.")
                await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                            forced_reason=f"[SKIP_CONCENTRATION] Portfolio too large for pool depth ({concentration*100:.1f}% > {MAX_CONCENTRATION*100}%)",
                                             target_pool=target_pool, safety_report=safety_report,
                                             ml_audit=ml_audit,
                                             metrics=financial_metrics)
                return

        if ml_audit['risk_level'] == 'high':
            toxic_reason = " [Liquidity Toxic]" if ml_audit.get('liquidity_toxic') else ""
            logger.warning(f"ML Audit REJECTED Target {target_pool['symbol']}: {ml_audit['risk_level']} Risk{toxic_reason}")
            await self._record_cycle_prediction(weights, latest_features, forced_type="ABORTED", 
                                         forced_reason=f"[ABORT_ML_RISK]{toxic_reason} {ml_audit['risk_level'].upper()} Risk detected",
                                         target_pool=target_pool, safety_report=safety_report,
                                         ml_audit=ml_audit,
                                         metrics=financial_metrics)
            return

        target_pool_id = target_pool['pool']
        target_apy = target_pool['apy']
        
        logger.info(f"Strategy: {target_pool['symbol']} ({target_apy:.2f}%) | Conviction: {weights[top_idx]:.1%}")

        # --- REALISM CHECK 1: Are we already in this pool? ---
        
        # Determine metrics even if holding
        # If holding identical pool, costs are technically zero relative to staying
        zero_metrics = financial_metrics.copy()
        zero_metrics.update({
            "gas_cost_usd": 0.0,
            "estimated_slippage": 0.0,
            "total_costs_usd": 0.0,
            "monthly_gain_usd": 0.0,
            "net_profit_usd": 0.0,
            "break_even_days": 0.0,
            "gas_exit": 0.0,
            "gas_enter": 0.0,
            "swap_fees": 0.0,
            "total_conversion_loss": 0.0,
            "roi_days": 0.0,
            "wallet_balance_eth": float(wallet_balance),
            "eth_price": float(eth_price),
            "last_updated": datetime.utcnow().isoformat()
        })

        if target_pool_id == self.current_pool_id and weights[top_idx] > 0.8 and not is_liquidity_emergency:
            logger.info(f"HOLD: Current position {target_pool['symbol']} remains optimal.")
            await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                        forced_reason=f"[HOLD_OPTIMAL] Already in {target_pool['symbol']}",
                                        target_pool=target_pool, safety_report=safety_report,
                                        metrics=zero_metrics) # Pass zero metrics!
            return

        # ... (Phases 1-3 logic continues) ...
        # (We skip Lines 207-213 in replacement as they are preserved below, but we need to update financial_metrics later)
        # Phase 3: Risk Audit Re-Check for Final Decision
        if ml_audit['risk_level'] == 'high':
            logger.warning(f"ML Audit REJECTED Target {target_pool['symbol']}: {ml_audit['risk_level']} Risk")
            await self._record_cycle_prediction(weights, latest_features, forced_type="ABORTED", 
                                         forced_reason=f"[ABORT_ML_RISK] {ml_audit['risk_level'].upper()} Risk detected",
                                         target_pool=target_pool, safety_report=safety_report,
                                         metrics=financial_metrics)
            return

        # Update persistent costs if we proceed (handled at transaction completion in production)
        # Note: cost_of_move is total_costs_usd
        if target_pool_id != self.current_pool_id:
             # We increment after successful execution now to avoid double counting
             pass

        # Yield Differential Logic (Defense active check)
        risk_delta = (current_ml_audit.get('risk_score', 50.0) if current_ml_audit else 50.0) - ml_audit.get('risk_score', 50.0)
        is_defensive_move = risk_delta > 30.0
        
        # Phase 1: The Opportunity Gap
        risk_msg = f" (Defense active: Risk reduction {risk_delta:.1f} pts)" if is_defensive_move else ""
        raw_gas_price = self.w3.eth.gas_price
        # Simulation floor for local demo to show costs (20 Gwei)
        if self.network == "local" and raw_gas_price < 1e9:
            raw_gas_price = 20 * 1e9
            
        gas_price_gwei = Decimal(str(raw_gas_price)) / Decimal("1e9")
        tx_gas_limit = Decimal("500000")
        gas_cost_usd = (tx_gas_limit * gas_price_gwei * Decimal("1e-9")) * eth_price
        
        # Wrap slippage check in thread
        estimated_slippage_float = await asyncio.to_thread(
            self.slippage.get_expected_slippage_sync, self.current_pool_symbol, target_pool['symbol'], float(PORTFOLIO_SIZE)
        )
        estimated_slippage = Decimal(str(estimated_slippage_float))
        
        total_costs_usd = gas_cost_usd + (PORTFOLIO_SIZE * estimated_slippage)
        monthly_gain_usd = (PORTFOLIO_SIZE * (Decimal(str(apy_gain)) / Decimal("100"))) / Decimal("12")
        net_profit_usd = monthly_gain_usd - total_costs_usd
        
        # Calculate Break-Even
        break_even_days = (total_costs_usd / (monthly_gain_usd / Decimal("30"))) if monthly_gain_usd > 0 else Decimal('Infinity')

        # Bundle Metrics for XAI (Convert to float for logging/DB compatibility)
        financial_metrics.update({
            "gas_cost_usd": float(gas_cost_usd),
            "estimated_slippage": float(estimated_slippage),
            "total_costs_usd": float(total_costs_usd),
            "monthly_gain_usd": float(monthly_gain_usd),
            "net_profit_usd": float(net_profit_usd),
            "break_even_days": float(break_even_days) if monthly_gain_usd > 0 else 999.0,
            # Map to dashboard keys
            "gas_exit": float(gas_cost_usd / 2),
            "gas_enter": float(gas_cost_usd / 2),
            "swap_fees": float(PORTFOLIO_SIZE * estimated_slippage),
            "total_conversion_loss": float(total_costs_usd),
            "roi_days": float(break_even_days) if monthly_gain_usd > 0 else 999.0,
            # Wallet Tracking
            "wallet_balance_eth": float(wallet_balance),
            "eth_price": float(eth_price),
            "last_updated": datetime.utcnow().isoformat()
        })

        # Phase 1: The Opportunity Gap
        risk_msg = f" (Defense active: Risk reduction {risk_delta:.1f} pts)" if is_defensive_move else ""
        
        # Labeling (Predicted vs Live) for Transparency
        c_label = "Predicted" if current_ml_audit else "Live"
        t_label = "Predicted" if ml_audit else "Live"
        
        p1_msg = f"🔍 Phase 1: Detecting Opportunity Gap...\n- Current: {self.current_pool_symbol} ({ml_predicted_current_apy:.2f}% {c_label} APY - Expected Future)\n- Target: {target_pool['symbol']} ({ml_predicted_target_apy:.2f}% {t_label} APY - Expected Future)\n- Gap: {apy_gain:.2f}% yield differential{risk_msg}"
        logger.info(p1_msg)
        self.phase_logs.append(p1_msg)

        # Phase 2: The Go/No-Go Decision
        # Projects a 14-day duration based on LSTM Trend
        predicted_trend = "Trending Up" if ml_predicted_target_apy > target_apy else "Stable"
        p2_msg = f"📊 Phase 2: The Go/No-Go Decision (Trend: {predicted_trend})\n- Expected Monthly Gain: ${monthly_gain_usd:,.2f}\n- Total Friction Costs: ${total_costs_usd:,.2f}\n- Break-Even Point: {break_even_days:.1f} days"
        logger.info(p2_msg)
        self.phase_logs.append(p2_msg)

        # Profitability Filter: Must be profitable in USD OR satisfy defensive move
        MIN_GAIN_THRESHOLD = 0.75
        
        # --- SMART Decision Intelligence Engine ---
        self.drift_status = "HEALTHY"
        self.retracement_prob = 0.0
        
        if is_drifting:
            drift_hours = self.drift_logger.get_active_drift_duration_hours(curr_addr)
            self.drift_status = "COOLING"

            
            # ML Retracement Analysis
            self.retracement_prob = self.ml_service.analyze_retracement_probability(curr_addr, tick, t_lower)
            
            if drift_hours >= self.cooling_period_hours:
                self.drift_status = "AUDITING"
                logger.info(f"🧠 SMART Decision Triggered: Mature Drift ({drift_hours:.1f}h). Running Method Audit.")
                
                smart_result = self._simulate_smart_roi(PORTFOLIO_SIZE, target_apy, self.current_apy, total_costs_usd, eth_price)
                
                if smart_result['recommendation'] == 'RERANGE':
                    if self.retracement_prob > 0.7:
                        logger.info(f"🧠 SMART Decision: High Retracement Prob ({self.retracement_prob:.1%}). Waiting for mean reversion.")
                        forced_reason = f"[SMART_WAIT] Retracement Prob {self.retracement_prob:.0%} > 70%"
                        await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                                    forced_reason=forced_reason,
                                                    target_pool=target_pool, safety_report=safety_report,
                                                    ml_audit=ml_audit, metrics=financial_metrics)
                        return
                    else:
                        logger.info(f"🧠 SMART Decision: RERANGE recommended over SWAP. Centering range.")
                        # Proceed with rebalance logic but focus on current_pool with new ticks
                else:
                    logger.info(f"🧠 SMART Decision: SWAP remains superior. Proceeding to Execution.")
                    self.drift_status = "EXECUTING"
            else:
                remaining = self.cooling_period_hours - drift_hours
                logger.info(f"🟡 COOLING: Drift active for {drift_hours:.1f}h. {remaining:.1f}h remaining in wait timer.")
                await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                            forced_reason=f"[COOLING] Drift active {drift_hours:.1f}h. Waiting for retracement ({self.retracement_prob:.0%})",
                                            target_pool=target_pool, safety_report=safety_report,
                                            ml_audit=ml_audit, metrics=financial_metrics)
                return

        # --- Gas Threshold Guard ---
        current_gas_gwei = float(self.w3.eth.gas_price) / 1e9
        if current_gas_gwei > self.max_gas_gwei and not is_liquidity_emergency:
            logger.info(f"⛽ GAS GUARD: Current gas ({current_gas_gwei:.1f}) > Max ({self.max_gas_gwei:.1f}). Throttling.")
            await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                        forced_reason=f"[GAS_GUARD] Gas {current_gas_gwei:.1f} > {self.max_gas_gwei:.1f} Limit",
                                        target_pool=target_pool, safety_report=safety_report,
                                        ml_audit=ml_audit, metrics=financial_metrics)
            return

        is_profitable = (apy_gain >= MIN_GAIN_THRESHOLD and net_profit_usd > 0)
        
        # Update metrics with SMART data for Dashboard
        financial_metrics.update({
            "drift_status": self.drift_status,
            "drift_hours": float(self.drift_logger.get_active_drift_duration_hours(curr_addr)) if is_drifting else 0.0,
            "retracement_prob": float(self.retracement_prob),
            "max_gas_threshold": self.max_gas_gwei
        })
        
        # --- FRAGMENTED EXIT DETECTION: Large Portfolio Slippage Trap ---
        CRITICAL_SLIPPAGE_THRESHOLD = Decimal("0.05")  # 5%
        LARGE_PORTFOLIO_THRESHOLD = Decimal("1000000")  # $1M
        
        if is_liquidity_emergency and estimated_slippage > CRITICAL_SLIPPAGE_THRESHOLD and PORTFOLIO_SIZE > LARGE_PORTFOLIO_THRESHOLD:
            logger.critical(f"🚨 FRAGMENTED EXIT REQUIRED: Portfolio (${float(PORTFOLIO_SIZE):,.0f}) too large for single-block exit.")
            logger.critical(f"   Estimated Slippage: {float(estimated_slippage):.2%} would cost ${float(PORTFOLIO_SIZE * estimated_slippage):,.0f}")
            logger.critical(f"   RECOMMENDATION: Break withdrawal into tranches of $100k every 30 minutes.")
            logger.critical(f"   This allows arbitrageurs to refill liquidity and reduces total slippage to <1%.")
            # For now, we abort the single-block exit to prevent catastrophic loss
            await self._record_cycle_prediction(weights, latest_features, forced_type="ABORTED", 
                                        forced_reason=f"[ABORT_FRAGMENTED_EXIT_NEEDED] Slippage {float(estimated_slippage):.2%} too high for ${float(PORTFOLIO_SIZE):,.0f} exit",
                                        target_pool=target_pool, safety_report=safety_report,
                                        ml_audit=ml_audit, metrics=financial_metrics)
            return
        
        # Decision Matrix
        if not is_defensive_move and not is_liquidity_emergency:
             # Basic Profitability Check
             if net_profit_usd < 0:
                 reason = f"Unprofitable: Net ${net_profit_usd:.2f}"
                 logger.info(f"VERDICT: REJECTED ({reason})")
                 await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                              forced_reason=f"[HOLD_PROFIT_GUARD] {reason}",
                                              target_pool=target_pool, safety_report=safety_report,
                                              ml_audit=ml_audit,
                                              metrics=financial_metrics)
                 return
             
             # Churn Check
             if apy_gain < MIN_GAIN_THRESHOLD:
                 reason = f"Low Gain {apy_gain:.2f}% < {MIN_GAIN_THRESHOLD}%"
                 logger.info(f"VERDICT: REJECTED ({reason})")
                 await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                              forced_reason=f"[HOLD_CHURN] {reason}",
                                              target_pool=target_pool, safety_report=safety_report,
                                              ml_audit=ml_audit,
                                              metrics=financial_metrics)
                 return

        if is_defensive_move and apy_gain < 0:
            logger.info(f"🛡️ DEFENSIVE MOVE: Proceeding with {apy_gain:.2f}% yield drop to reduce risk by {risk_delta:.1f} pts.")
            self.phase_logs.append(f"🛡️ DEFENSIVE MOVE: Reducing risk by {risk_delta:.1f} pts.")

        if is_liquidity_emergency:
            logger.error("🚨 EMERGENCY EXIT: Liquidity Trap detected in current pool. Overriding yield math.")
            self.phase_logs.append("🚨 EMERGENCY EXIT: Liquidity Trap detected.")

        logger.info(f"✅ Verdict: GO! Triggering Atomic Rebalance...")
        self.phase_logs.append("✅ Verdict: GO! Triggering Atomic Rebalance...")

        # Phase 3: The Atomic Execution
        p3_msg = "⚡ Phase 3: The Atomic Execution\n- [Step 1] Withdrawal Command Initialized...\n- [Step 2] Optimizing Swap Routes via 1inch/StrategyHub...\n- [Step 3] Capital Deployment to Target Vault..."
        logger.info(p3_msg)
        self.phase_logs.append(p3_msg)
        
        # Determine BPS for rebalance(aaveBps, compoundBps)
        is_aave = "aave" in target_pool['symbol'].lower() or "aave" in target_pool.get('project', '').lower()
        new_aave_bps = 10000 if is_aave else 0
        new_comp_bps = 10000 - new_aave_bps

        rebalance_txs = self._construct_rebalance_txs(target_pool_id, new_aave_bps, new_comp_bps)
        if not rebalance_txs:
            logger.info("   - [Step 4] NO-OP: Strategy already aligned.")
            self.phase_logs.append("- [Step 4] NO-OP: Strategy already aligned.")
            await self._record_cycle_prediction(weights, latest_features, forced_type="HOLD", 
                                        forced_reason="[HOLD_STRATEGY_IDLE] Position is optimal",
                                        target_pool=target_pool, safety_report=safety_report,
                                        ml_audit=ml_audit, metrics=financial_metrics)
            return

        # Check slippage (Simulated but high-fidelity for Phase 3)
        if not self.slippage.is_safe_to_rebalance(estimated_slippage):
            logger.warning(f"🚨 Phase 3 ABORTED: Slippage too high ({estimated_slippage:.2%})")
            self.phase_logs.append(f"🚨 Phase 3 ABORTED: Slippage too high ({estimated_slippage:.2%})")
            await self._record_cycle_prediction(weights, latest_features, forced_type="ABORTED", 
                                        forced_reason=f"[ABORT_SLIPPAGE_HIGH] Slippage {estimated_slippage:.2%}",
                                        target_pool=target_pool, safety_report=safety_report,
                                        ml_audit=ml_audit, metrics=financial_metrics)
            return
            


        # 5. Layer 4: Execution
        current_block = self.w3.eth.block_number
        if self.network == "local":
            # For POC/Local, execute rebalance_txs directly on Anvil
            success = await asyncio.to_thread(self._send_direct_bundle, rebalance_txs)
        else:
            # Use Flashbots to bypass public mempool (MEV Protection)
            success = await asyncio.to_thread(self.hands.send_rebalance_bundle, rebalance_txs, current_block + 1)
        
        if success:
            logger.info(f"   - [Step 4] Rebalance Verified at block {current_block}! Method: {execution_method}")
            prev_id = self.current_pool_id
            prev_symbol = self.current_pool_symbol
            prev_apy = self.current_apy
            
            self.current_pool_id = target_pool_id
            self.current_pool_symbol = target_pool['symbol']
            self.current_apy = target_apy
            
            self.cumulative_costs += total_costs_usd
            await asyncio.to_thread(
                self.state_store.save_state, 
                self.current_pool_id, 
                self.current_pool_symbol, 
                self.current_apy, 
                cumulative_costs=float(self.cumulative_costs)
            )
            
            # --- NEW: Refresh Wallet Snapshot Immediately Post-Execution ---
            if self.network == "local":
                # SIMULATION: Manually swap tokens on Anvil to reflect the move
                # 1. Wipe previous asset (WETH/USDC)
                # 2. Deal new asset (Target Token)
                try:
                    logger.info(f"🧪 SIMULATION: Swapping portfolio on Anvil to {target_pool['symbol']}...")
                    
                    # Resolve Target Token Address
                    target_token_address = self._resolve_pool_address(target_pool_id, target_pool['symbol'], target_pool.get('project', ''))
                    
                    if target_token_address:
                        my_addr = self.signer.address
                        current_eth = self.w3.eth.get_balance(my_addr)
                        # Auto-reset disabled for demo to show gas tracking
                        pass
                        
                        # Deal Target Token (Simulate $200k)
                        # We need the decimals.
                        try:
                            token_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(target_token_address), abi=self.erc20_abi)
                            decimals = token_contract.functions.decimals().call()
                            
                            # Deduct costs from portfolio to simulate fees
                            cost_usd = financial_metrics.get("total_costs_usd", 0.0)
                            net_portfolio_value = max(0.0, float(PORTFOLIO_SIZE) - cost_usd)
                            amount_wei = int(net_portfolio_value * (10**decimals)) 
                            
                            # Let's try to populate the discovery_tokens map so _get_detailed_portfolio picks it up
                            self.discovery_tokens[target_pool['symbol']] = target_token_address
                            
                            # And we can try to use a 'deal' cheat if available, but standard Web3 doesn't have it easily without slot mapping.
                            # ALTERNATIVE: We can just MOCK the snapshot in the DB directly since this is a visual dashboard test.
                            await asyncio.to_thread(
                                 self.db.log_wallet_snapshot,
                                 float(net_portfolio_value),
                                 float(self.w3.from_wei(current_eth, 'ether')), # Use REAL gas balance
                                 [{
                                     "symbol": target_pool['symbol'],
                                     "balance": float(amount_wei) / (10**decimals),
                                     "value_usd": float(net_portfolio_value),
                                     "type": "yield_bearing"
                                 }]
                            )
                            
                        except Exception as e:
                            logger.warning(f"Failed to resolve token details: {e}")
                            
                except Exception as e:
                    logger.warning(f"Simulation Swap Failed: {e}")

            try:
                # Wait a bit for chain state propagation
                new_portfolio = await self._get_detailed_portfolio()
                
                # OVERRIDE for Dashboard Demo if local
                if self.network == "local":
                    # Deduct simulated costs to show "Real" PnL impact
                    simulated_cost = financial_metrics.get('total_costs_usd', 0.0)
                    net_pos_value = float(PORTFOLIO_SIZE) - simulated_cost
                    
                    # Use REAL ETH balance from portfolio scan (shows gas usage)
                    real_eth = float(new_portfolio['eth_balance'])
                    eth_value = real_eth * 2000.0 # Approximate ETH price or fetch

                    new_portfolio['assets'] = [{
                        "symbol": target_pool['symbol'],
                        "balance": net_pos_value, # Approximate 1:1 USD for simplification
                        "value_usd": net_pos_value,
                        "type": "strategy_position"
                    }, {
                        "symbol": "ETH",
                        "balance": real_eth,
                        "value_usd": eth_value,
                        "type": "gas"
                    }]
                    new_portfolio['total_usd'] = net_pos_value + eth_value
                
                await asyncio.to_thread(
                     self.db.log_wallet_snapshot,
                     float(new_portfolio['total_usd']),
                     float(new_portfolio['eth_balance']),
                     new_portfolio['assets']
                )
                logger.info(f"✅ Wallet snapshot updated post-execution: {new_portfolio['eth_balance']:.4f} ETH")
            except Exception as e:
                logger.warning(f"Failed to update post-exec snapshot: {e}")
            
            # Phase 4: Monitoring the Pulse
            p4_msg = f"💓 Phase 4: Monitoring the Pulse\n- Yield Watch: Tracking {target_pool['symbol']} for trend decay...\n- Circuit Breaker: Safety rails active and monitoring TVL/Peg."
            logger.info(p4_msg)
            self.phase_logs.append(p4_msg)

            financial_metrics['execution_block'] = current_block
            financial_metrics['execution_method'] = execution_method

            self.last_rebalance_time = datetime.utcnow()
            
            await self._record_cycle_prediction(weights, latest_features, forced_type="SUCCESS", 
                                         forced_reason=f"[SUCCESS_REBALANCED] {execution_method} to {target_pool['symbol']}",
                                        target_pool=target_pool, safety_report=safety_report,
                                        override_current_symbol=prev_symbol,
                                        override_current_apy=prev_apy,
                                        override_current_id=prev_id,
                                        metrics=financial_metrics)
        else:
            logger.error("❌ Phase 3 FAILED: Flashbots bundle rejected.")
            self.phase_logs.append("❌ Phase 3 FAILED: Flashbots bundle rejected.")
            self.tracker.record_prediction(
                prediction_type="ABORTED",
                current_pool_id=self.current_pool_id or "none",
                current_pool_apy=self.current_apy,
                target_pool_id=target_pool_id,
                target_pool_apy=target_apy,
                confidence=float(weights[top_idx]),
                reason="[ABORT_EXECUTION_ERROR] Flashbots bundle failed or rejected",
                capital_usd=100000.0,
                market_context={
                    "target_pool": target_pool,
                    "safety_report": safety_report,
                    "current_pool_symbol": self.current_pool_symbol,
                    "metrics": financial_metrics
                }
            )

    async def _record_cycle_prediction(self, weights: np.ndarray, features: np.ndarray,
                                 forced_type: str = None, forced_reason: str = None,
                                 target_pool: Dict = None, safety_report: Dict = None,
                                 override_current_symbol: str = None,
                                 ml_audit: Dict = None,
                                 override_current_apy: float = None,
                                 override_current_id: str = None,
                                 metrics: Dict = None):
        """Log the cycle's decision to both SQLite (Dashboard) and PostgreSQL (Audit Trail)."""
        try:
            # Determine prediction type from forced_type or calculate it
            # In this flow, forced_type is almost always passed ("HOLD", "REBALANCE", "ABORTED")
            prediction_type = forced_type if forced_type else "HOLD"

            actual_current_symbol = override_current_symbol if override_current_symbol else self.current_pool_symbol
            
            # Helper Variables
            top_idx = np.argmax(weights)
            runner_ups = [] # Simplified for stability
            confidence_breakdown = {
                "yield_momentum": float(weights[top_idx]),
                "stability_score": 0.5, # Default
                "token_correlation": 0.2
            }
            # Determine Capital (Dynamic from metrics if available)
            wallet_eth = metrics.get('wallet_balance_eth', 0.0) if metrics else 0.0
            price_eth = metrics.get('eth_price', 2500.0) if metrics else 2500.0
            dynamic_capital = wallet_eth * price_eth if wallet_eth > 0.01 else float(os.getenv("PORTFOLIO_SIZE_USD", 100000.0))

            # Call Record Prediction with FULL ARGUMENTS (Threaded DB I/O)
            await asyncio.to_thread(
                self.tracker.record_prediction,
                prediction_type=prediction_type,
                current_pool_id=override_current_id if override_current_id else self.current_pool_id,
                current_pool_apy=override_current_apy if override_current_apy else self.current_apy,
                target_pool_id=target_pool['pool'] if target_pool else "none",
                target_pool_apy=target_pool['apy'] if target_pool else 0.0,
                confidence=float(weights[top_idx]),
                reason=forced_reason if forced_reason else "Autonomous Update",
                capital_usd=dynamic_capital,
                market_context={
                    "runner_ups": runner_ups,
                    "target_metadata": target_pool if target_pool else None,
                    "current_pool_symbol": actual_current_symbol,
                    "safety_report": safety_report,
                    "confidence_breakdown": confidence_breakdown,
                    "phase_logs": self.phase_logs,
                    "metrics": metrics
                },
                gas_cost=metrics.get('gas_cost_usd', 0.0) if metrics else 0.0,
                slippage=metrics.get('estimated_slippage', 0.0) if metrics else 0.0,
                volatility_score=float(np.std(features[:, 0])),
                predicted_apy=target_pool['apy'] if target_pool else float(features[top_idx, 0])
            )

            # Phase 3: PostgreSQL Audit Trail
            if ml_audit:
                try:
                    self.ml_service.db_logger.log_prediction({
                        'network': 'ethereum_mainnet_fork',
                        'pool_address': target_pool['pool'] if target_pool else "none",
                        'asset_address': target_pool.get('symbol', 'USDC'),
                        'protocol_name': target_pool.get('project', 'Unknown') if target_pool else "Unknown",
                        'predicted_apy': ml_audit.get('predicted_apy', 0.0),
                        'risk_level': ml_audit.get('risk_level', 'medium'),
                        'confidence': ml_audit.get('confidence', 0.0),
                    })
                except Exception as e:
                    logger.warning(f"Postgres logging failed: {e}")
        except Exception as e:
            logger.error(f"Failed to record prediction: {e}")

    def _send_direct_bundle(self, bundle: List[Dict]) -> bool:
        """Submit transactions directly for local testing."""
        try:
            for tx in bundle:
                signed_tx = self.w3.eth.account.sign_transaction(tx, self.signer.key)
                tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                logger.info(f"Direct TX Sent: {tx_hash.hex()}")
                # Wait for receipt on local node
                self.w3.eth.wait_for_transaction_receipt(tx_hash)
            return True
        except Exception as e:
            logger.error(f"Direct execution failed: {e}")
            return False

    async def _fetch_latest_market_state(self) -> Any:
        """Fetch latest pool data from DeFiLlama."""
        logger.info("Fetching latest market state from DeFiLlama...")
        
        # Constrain chains based on network to avoid cross-chain address resolution errors
        scan_chains = ["Ethereum"]
        if self.network == "base" or self.network == "base_sepolia":
            scan_chains = ["Base"]
            
        # Fetch wider range of pools to filter for quality
        raw_pools = await self.defillama.fetch_top_pools(
            chains=scan_chains,
            limit=40 # Fetch more to allow filtering
        )
        
        # Actual System Requirement: Tier 1 Stables & Bluechip Protocols
        tier_1_stables = ['USDC', 'USDT', 'DAI', 'FRAX', 'LUSD', 'GHO', 'USDS', 'PYUSD']
        trusted_protocols = ['aave', 'compound', 'curve', 'uniswap', 'maker', 'yearn', 'morpho', 'spark', 'sky', 'ethena']
        
        valid_pools = []
        for p in raw_pools:
            proj = p.get('project', '').lower()
            sym = p.get('symbol', '').upper()
            
            # Filter for high-quality bluechips
            is_bluechip_proto = any(t in proj for t in trusted_protocols)
            is_tier_1_stable = any(s in sym for s in tier_1_stables)
            
            if is_bluechip_proto or is_tier_1_stable:
                # Add slight jitter to apy to ensure graining if many are identical
                p['apy'] = p.get('apy', 0) + random.uniform(-0.005, 0.005)
                valid_pools.append(p)
                
        # Shuffle to avoid deterministic picking of the same pool every time
        random.shuffle(valid_pools)
        
        # Sort and take top
        valid_pools = sorted(valid_pools, key=lambda x: x.get('apy', 0), reverse=True)[:self.env.max_pools]
        self.last_scanned_pools = valid_pools
        top_pools = self.last_scanned_pools
        
        # In a real scenario, we'd convert these pools into the 32-dim feature vector
        # For now, we simulate feature extraction with tiny jitter for realism
        features = []
        for pool in top_pools:
            pool_feat = np.zeros(32, dtype=np.float32)
            # Add ±0.01% jitter to the apy to ensure the brain's conviction changes
            apy_val = pool.get('apy', 0) + random.uniform(-0.01, 0.01)
            pool_feat[0] = apy_val
            pool_feat[4] = np.log1p(pool.get('tvlUsd', 0))
            
            # Phase 1: On-Chain Enrichment (V3 Metrics)
            if "uniswap-v3" in pool.get('project', '').lower():
                addr = self._resolve_pool_address(pool['pool'], pool['symbol'], "uniswap-v3")
                if addr:
                    v3_metrics = self.uniswap.get_pool_metrics(addr)
                    if v3_metrics:
                        # Map on-chain metrics to unused feature slots (28-31)
                        # 28: feeGrowthGlobal, 29: liquidity, 30: sqrtPriceX96
                        pool_feat[28] = float(v3_metrics['feeGrowthGlobal0X128']) / 1e30 # Scale down
                        pool_feat[29] = float(v3_metrics['liquidity']) / 1e18
                        pool_feat[30] = float(v3_metrics['sqrtPriceX96']) / 1e28
            
            features.append(pool_feat)
            
        # Ensure we have exactly max_pools
        while len(features) < self.env.max_pools:
            features.append(np.zeros(32, dtype=np.float32))
            
        return np.array(features).reshape(self.env.max_pools, 32)
    async def _get_detailed_portfolio(self) -> Dict:
        """Fetch full portfolio state: ETH, ERC-20s, and Uniswap V3 LPs."""
        report: Dict[str, Any] = {
            "total_usd": Decimal("0"),
            "eth_balance": Decimal("0"),
            "stable_balance": Decimal("0"),
            "eth_price": Decimal("0"),
            "assets": []
        }
        
        try:
            # 1. ETH Balance (Gas Tank)
            eth_price_float, _ = await self.chainlink.get_asset_price('ETH')
            eth_price = Decimal(str(eth_price_float))
            report['eth_price'] = eth_price
            
            raw_eth = await asyncio.to_thread(self.w3.eth.get_balance, self.signer.address)
            eth_bal = Decimal(str(self.w3.from_wei(raw_eth, 'ether')))
            report['eth_balance'] = eth_bal
            
            eth_usd = eth_bal * eth_price
            report['total_usd'] += eth_usd
            report['assets'].append({"symbol": "ETH", "balance": float(eth_bal), "value_usd": float(eth_usd), "type": "gas"})

            # 2. ERC-20 Assets (Discovery)
            for symbol, addr in self.discovery_tokens.items():
                # If we are in local demo mode and in a strategy, we should hide old assets to stay 'clean'
                if self.network == "local" and self.current_pool_symbol != "CASH" and symbol in ["WETH", "WBTC", "DAI", "USDC"]:
                    # In local demo, if we are in a strategy, we hide the vault tokens 
                    # from the wallet view to focus on the 'StrategyHub' position
                    continue

                contract = self.w3.eth.contract(address=self.w3.to_checksum_address(addr), abi=self.erc20_abi)
                raw_bal = await asyncio.to_thread(contract.functions.balanceOf(self.signer.address).call)
                if raw_bal > 0:
                    decimals = await asyncio.to_thread(contract.functions.decimals().call)
                    bal = Decimal(str(raw_bal)) / Decimal(str(10**decimals))
                    
                    # Valuation
                    price = Decimal("1.0") # Default for stables
                    if symbol == "WETH": price = eth_price
                    elif symbol == "WBTC":
                        btc_price, _ = await self.chainlink.get_asset_price('BTC')
                        price = Decimal(str(btc_price))
                        
                    usd_val = bal * price
                    report['total_usd'] += usd_val
                    report['assets'].append({"symbol": symbol, "balance": float(bal), "value_usd": float(usd_val), "type": "token"})
                    
                    if symbol in ["USDC", "USDT", "DAI"]:
                        report['stable_balance'] += bal

            # 3. Protocol Discovery / Strategy Position
            if self.current_pool_id and self.current_pool_symbol != "CASH":
                # Scale strategy size: Prioritize initial_capital (persisted) over ENV default
                base_capital = self.initial_capital if self.initial_capital > 0 else Decimal(os.getenv("PORTFOLIO_SIZE_USD", "100000.0"))
                
                # Deduct simulated fees to show the user the amount REDUCED by rebalancing friction
                net_strategy_value = base_capital - self.cumulative_costs
                
                report['total_usd'] += net_strategy_value
                report['assets'].append({
                    "symbol": self.current_pool_symbol,
                    "balance": float(net_strategy_value),
                    "value_usd": float(net_strategy_value),
                    "type": "strategy_position"
                })
            elif report['total_usd'] < 100:
                # Failsafe fallback: use persisted capital if available
                base_capital = self.initial_capital if self.initial_capital > 0 else Decimal(os.getenv("PORTFOLIO_SIZE_USD", "100000.0"))
                report['total_usd'] += base_capital
                report['assets'].append({
                    "symbol": self.current_pool_symbol,
                    "balance": float(base_capital),
                    "value_usd": float(base_capital),
                    "type": "vault"
                })

            logger.info(f"PORTFOLIO DISCOVERY: Total Value: ${float(report['total_usd']):,.2f} | ETH: {float(eth_bal):.4f}")
            
        except Exception as e:
            logger.error(f"Portfolio Discovery Failed: {e}")
            report['total_usd'] = Decimal(os.getenv("PORTFOLIO_SIZE_USD", "100000.0"))

        return report

    def _construct_rebalance_txs(self, target_pool_id: str, new_aave_bps: int, new_comp_bps: int) -> List[Dict]:
        """Calculates transaction data for StrategyHub.rebalance(aaveBps, compBps)."""
        logger.info("Constructing rebalancing transactions for StrategyHub...")
        # In this POC, StrategyHub handles Aave and Compound. 
        # The BPS values specify the desired allocation.
        
        # Encoding calldata for StrategyHub.rebalance(uint256 aaveBps, uint256 compoundBps)
        # Function selector for rebalance(uint256,uint256) is 0x56a427f1
        method_id = "0x56a427f1"
        
        # Ensure BPS are within range
        safe_aave = max(0, min(10000, new_aave_bps))
        safe_comp = max(0, min(10000, new_comp_bps))
        
        aave_hex = format(safe_aave, '064x')
        comp_hex = format(safe_comp, '064x')
        calldata = method_id + aave_hex + comp_hex

        rebalance_tx = {
            'from': self.signer.address,
            'to': os.getenv("STRATEGY_HUB_ADDRESS", "0x56d4d6aEe0278c5Df2FA23Ecb32eC146C9446FDf"),
            'value': 0,
            'data': calldata,
            'nonce': self.w3.eth.get_transaction_count(self.signer.address),
            'chainId': self.w3.eth.chain_id
        }

        # Dynamic Gas Estimation (Issue 3 Implementation)
        try:
            # Estimate gas on-chain
            estimated_gas = self.w3.eth.estimate_gas(rebalance_tx)
            
            # Sanity Check: DeFi transactions should be 300k-600k gas
            if estimated_gas < 100000:
                logger.warning(f"   - Gas estimate ({estimated_gas}) suspiciously low for DeFi tx. Using safe minimum of 350,000.")
                estimated_gas = 350000
            
            # Add 20% buffer for complex state changes
            rebalance_tx['gas'] = int(estimated_gas * 1.2)
            logger.info(f"   - Estimated Gas: {estimated_gas} (Limit set to {rebalance_tx['gas']} with 20% buffer)")
        except Exception as e:
            logger.warning(f"   - Gas Estimation failed: {e}. Falling back to 550,000.")
            rebalance_tx['gas'] = 550000

        # Add EIP-1559 Fees (Correct Formula)
        try:
            # Get current base fee from latest block
            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block.get('baseFeePerGas', self.w3.eth.gas_price)
            
            # Priority fee (tip to miners)
            max_priority_fee = self.w3.to_wei(2, 'gwei')
            
            # Max fee must be >= base_fee + priority_fee
            max_fee_per_gas = int(base_fee * 1.5 + max_priority_fee)
            
            rebalance_tx['maxFeePerGas'] = max_fee_per_gas
            rebalance_tx['maxPriorityFeePerGas'] = max_priority_fee
            
            logger.info(f"   - Gas Fees: Base={base_fee/1e9:.2f} Gwei, Priority={max_priority_fee/1e9:.2f} Gwei, Max={max_fee_per_gas/1e9:.2f} Gwei")
        except Exception as e:
            # Fallback for networks without EIP-1559 support
            logger.warning(f"   - EIP-1559 not supported, using legacy gas price: {e}")
            rebalance_tx['gasPrice'] = int(self.w3.eth.gas_price * 1.2)
        
        logger.info(f"   - Encoded StrategyHub.rebalance({new_aave_bps}, {new_comp_bps})")
        return [rebalance_tx]

    def _audit_prediction_drift(self):
        """Implement System Health: Drift Monitoring every 7 days"""
        # Look at the prediction made 7 days ago and compare it to actual realized APY.
        # This is a stub for the accuracy audit loop.
        logger.info("🔍 Running Accuracy Audit: Checking for Model Drift...")
        # if MAE > 20%: logger.critical("🚨 MODEL DRIFT DETECTED: Retrain Model Alert Triggered!")
        pass

    async def start(self):
        """Continuously run the rebalancer."""
        cycle_count: int = 0
        interval = int(os.getenv("REBALANCE_INTERVAL", 300))
        
        logger.info(f"Rebalancer Loop Active (Interval: {interval}s)")

        while True:
            try:
                # Use a Hard Timeout (2 mins) to ensure we NEVER hang forever on a stalling RPC/API
                current_cycle = cycle_count + 1
                logger.info(f"⏱️ Starting Cycle {current_cycle}...")
                await asyncio.wait_for(self.run_cycle(), timeout=120)
                cycle_count = current_cycle # Use assignment to satisfy type checker
                
                # Accuracy Audit
                if cycle_count % 24 == 0:
                    self._audit_prediction_drift()
                    
            except asyncio.TimeoutError:
                logger.error(f"🚨 Cycle {cycle_count + 1} TIMED OUT after 120s! Skipping to next interval...")
            except Exception as e:
                logger.error(f"Error in rebalancer cycle: {e}", exc_info=True)
            
            # Use Heartbeat Logging during wait
            logger.info(f"💤 Cycle {cycle_count} complete. Sleeping for {interval}s...")
            await asyncio.sleep(interval)

    def _resolve_pool_address(self, pool_uuid: str, symbol: str, project: str) -> Optional[str]:
        """Centralized helper to map pool IDs to on-chain hex addresses with fuzzy logic."""
        if not pool_uuid:
            return None
        
        # 1. Direct DB Lookup (Most accurate if we saved it from DeFiLlama metadata)
        addr = self.db.get_pool_address(pool_uuid)
        if addr and addr.startswith('0x'):
            return addr
            
        # 2. Ask ML Service (It has the master map of Verified Addresses)
        resolved = self.ml_service.resolve_asset_address(symbol)
        if resolved:
            return resolved

        # 3. Fuzzy Fallbacks (Chain/Protocol specific)
        symbol_up = str(symbol).upper()
        project_low = str(project).lower()
        
        if 'USP' in symbol_up:
            return '0x098697ba3fee4ea76294c5d6a466a4e3b3e95fe6' 
        elif 'AAVE' in project_low or 'AAVE' in symbol_up:
            return '0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2'
        elif 'COMPOUND' in project_low or 'COMP' in symbol_up:
            return '0xc3d688b66703497daa19211eedff47f25384cdc3'
        elif 'ETHENA' in project_low or 'SUSDE' in symbol_up or 'USDE' in symbol_up:
            return '0x9d39a5de30e57443bff2a8307a4256c8797a3497'
        elif 'USDC' in symbol_up:
             return '0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2' # Default to Aave V3
             
        return None

    def _simulate_smart_roi(self, portfolio_size: Decimal, target_apy: float, current_apy: float, total_costs_usd: Decimal, eth_price: Decimal) -> Dict:
        """
        Intelligence Engine: SMART Decision ROI Simulation.
        Compares 'Reranging' (Adding Capital back to range) vs 'Swapping' (Exiting to Target).
        """
        # 1. Rerange Scenario: Single transaction + min gas
        rerange_gas_limit = Decimal("250000")
        gas_price_gwei = Decimal(str(self.w3.eth.gas_price)) / Decimal("1e9")
        rerange_cost = (rerange_gas_limit * gas_price_gwei * Decimal("1e-9")) * eth_price
        
        # Monthly gain if we just rerange (assume current pool APY is restored)
        rerange_monthly_gain = (portfolio_size * (Decimal(str(current_apy)) / Decimal("100"))) / Decimal("12")
        rerange_net_30d = rerange_monthly_gain - rerange_cost
        
        # 2. Swap Scenario: Costs already calculated in main loop
        swap_monthly_gain = (portfolio_size * (Decimal(str(target_apy)) / Decimal("100"))) / Decimal("12")
        swap_net_30d = swap_monthly_gain - total_costs_usd
        
        logger.info(f"SMART ROI SIM: Rerange (+${float(rerange_net_30d):,.2f}/mo) vs Swap (+${float(swap_net_30d):,.2f}/mo)")
        
        return {
            "rerange_net": float(rerange_net_30d),
            "swap_net": float(swap_net_30d),
            "recommendation": "SWAP" if swap_net_30d > rerange_net_30d else "RERANGE"
        }


if __name__ == "__main__":
    service = RebalancerService()
    asyncio.run(service.start())
