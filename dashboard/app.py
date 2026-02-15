import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import streamlit as st
import requests
import pandas as pd
import time
from src.backtest.engine import BacktestEngine
from src.data.timeseries_db import TimeseriesDB
from src.optimizer.trade_sizer import TradeSizer
from src.backtest.prediction_tracker import PredictionTracker
from src.execution.sim_control import SimController
from web3 import Web3
from eth_account import Account
import json
import os
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# Auto-refresh the page every 2 seconds to keep stats live without manual refresh
# This is crucial for local testing where cycles are fast.
st_autorefresh(interval=5000, key="datarefresh")

# Manual Refresh override
if st.sidebar.button("🔄 Refresh Data"):
    st.rerun()

st.set_page_config(page_title="AI Yield Brain - Live Status", layout="wide")

# --- Premium Styling ---
st.markdown("""
    <style>
    .main {
        background: #0e1117;
    }
    .stMetric {
        background: rgba(49, 51, 63, 0.4);
        padding: 15px;
        border-radius: 10px;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    div[data-testid="stExpander"] {
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 10px;
        background: rgba(49, 51, 63, 0.2);
    }
    .stButton>button {
        border-radius: 5px;
        font-weight: bold;
        transition: all 0.3s;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 5px 15px rgba(0, 178, 255, 0.4);
    }
    h1, h2, h3 {
        color: #00b2ff !important;
        font-family: 'Outfit', sans-serif;
    }
    .status-badge {
        padding: 5px 10px;
        border-radius: 15px;
        font-weight: bold;
        text-transform: uppercase;
        font-size: 0.8em;
    }
    </style>
""", unsafe_allow_html=True)

st.title("AI Yield Rebalancer: SMART Control Tower")

# --- Sidebar ---
st.sidebar.header("System Status")
api_host = os.getenv("BRAIN_API_HOST", "localhost")
try:
    health = requests.get(f"http://{api_host}:8000/health", timeout=2).json()
    st.sidebar.success(f"Brain Online (v{health['version']})")
except:
    st.sidebar.error(f"Brain Offline (Is server.py running?)")

# --- Wallet Status Section ---
st.sidebar.markdown("---")
st.sidebar.subheader(" Agent Wallet")

# Initialize latest_preds to ensure it exists for Tab 1
latest_preds = []

try:
    # 1. Fetch Predictions (Critical for Context)
    tracker = PredictionTracker()
    latest_preds = tracker.get_all_predictions(limit=1)
    
    # 2. Fetch Wallet Snapshot (Persistence)
    wallet_snap = None
    try:
        db = TimeseriesDB()
        if hasattr(db, 'get_latest_wallet_snapshot'):
            wallet_snap = db.get_latest_wallet_snapshot()
    except Exception as e:
        st.sidebar.warning(f"DB Connection Issue: {e}")

    # --- Display Wallet Section ---
    if wallet_snap:
        # A. Use Snapshot Data (Preferred)
        total_value = wallet_snap['total_usd']
        st.sidebar.markdown(f"### Total: **${total_value:,.2f}**")
        
        eth_bal = wallet_snap['eth_balance']
        st.sidebar.metric("⛽ Gas Tank", f"{eth_bal:.4f} ETH", help="Used for transaction fees")

        assets = wallet_snap['assets']
        if assets:
            with st.sidebar.expander("Asset Breakdown", expanded=True):
                valid_assets = [a for a in assets if a['value_usd'] > 1.0]
                if valid_assets:
                    df_assets = pd.DataFrame(valid_assets)
                    df_assets['Balance'] = df_assets['balance'].map(lambda x: f"{x:.4f}")
                    df_assets['Value'] = df_assets['value_usd'].map(lambda x: f"${x:,.2f}")
                    st.table(df_assets[['symbol', 'Balance', 'Value']])
                else:
                    st.sidebar.caption("No significant assets found.")
    
    elif latest_preds:
        # B. Fallback to Cycle Data (If snapshot missing)
        latest = latest_preds[0]
        st.sidebar.info("Using Cycle Data (Snapshot missing)")
        
        # Try to extract total value from context
        ctx = json.loads(latest.get('market_context', '{}'))
        metrics = ctx.get('metrics', {})
        total_value = metrics.get('total_portfolio_usd', latest.get('capital_usd', 0.0))
        st.sidebar.markdown(f"### Total: **${float(total_value):,.2f}**")
        
    else:
        st.sidebar.warning("No Wallet/Agent History Found.")

    # --- Signals & Config ---
    st.sidebar.markdown("---")
    st.sidebar.subheader("⚙️ Agent Controls")

    if latest_preds:
        latest = latest_preds[0]
        ctx = json.loads(latest.get('market_context', '{}'))
        metrics = ctx.get('metrics', {}) or {}
        
        # Active Pool
        current_pool = "Starting..."
        if ctx.get('current_pool_symbol'):
            current_pool = ctx['current_pool_symbol']
        st.sidebar.metric("Active Strategy", current_pool)
        
        # SMART Status
        status = metrics.get('drift_status', 'HEALTHY')
        status_icon = "🟢" if status == "HEALTHY" else ("🟡" if status == "COOLING" else ("🔍" if status == "AUDITING" else "🚀"))
        st.sidebar.markdown(f"**SMART Status**: {status_icon} {status}")

    # Preferences
    with st.sidebar.expander("Risk Parameters", expanded=False):
        risk_tol = st.slider("Risk Tolerance", 0.1, 1.0, 0.8)
        min_gain = st.slider("Min Yield Gain (%)", 0.0, 10.0, 1.0)
        max_slip = st.slider("Max Slippage (%)", 0.1, 5.0, 2.0)
        max_gas = st.number_input("Max Gas (Gwei)", 10, 500, 50)
        if st.button("Update Config"):
            st.success("Config Updated")

except Exception as e:
    st.sidebar.error(f"Sidebar Error: {str(e)}")

# --- Test Portfolio Section ---
st.markdown("###  Active Test Portfolio")

def fetch_valid_pools():
    """Load fully-stablecoin pools from local DB — only battle-tested stables"""
    db = TimeseriesDB()
    pools = db.get_latest_yields(
        stablecoin_only=True,
        chains=['Ethereum', 'Base'],
        min_tvl=1_000_000,
        max_apy=50  # Cap at 50% — anything higher on stables is suspicious
    )
    
    # Tier 1: Major, battle-tested stablecoins only
    TRUSTED_STABLES = {
        # USD-pegged (proven)
        'USDC', 'USDT', 'DAI', 'FRAX', 'LUSD', 'GHO', 'USDS',
        'CRVUSD', 'PYUSD', 'GUSD', 'USDP', 'FRXUSD', 'USD0',
        # Yield-bearing wrappers of trusted stables
        'USDE', 'SUSDE', 'SDAI', 'SFRXUSD',
        # EUR stables (Circle-backed)
        'EURC', 'EUROC',
        # Liquity v2
        'BOLD',
    }
    
    # Trusted protocols (proven, audited, not rugs)
    TRUSTED_PROTOCOLS = {
        'aave-v3', 'aave-v2', 'compound-v3', 'compound-v2',
        'curve-dex', 'convex-finance', 'uniswap-v3',
        'morpho', 'morpho-blue', 'sparklend', 'sky',
        'maker', 'makerdao', 'yearn-finance', 'lido',
        'aerodrome-v2', 'aerodrome-slipstream',
        'fluid', 'euler', 'euler-v2',
        'pendle', 'ethena', 'frax-ether',
        'stargate', 'across', 'beefy',
        'stake-dao', 'merkl',
    }
    
    def is_fully_stable(symbol: str) -> bool:
        """Check if ALL tokens in a pair are trusted stablecoins"""
        tokens = symbol.upper().replace('/', '-').replace('_', '-').split('-')
        return all(t.strip() in TRUSTED_STABLES for t in tokens if t.strip())
    
    filtered = []
    for p in pools:
        if not is_fully_stable(p['symbol']):
            continue
        # Optional: also filter by trusted protocol
        protocol = p.get('protocol', '').lower()
        if protocol and not any(tp in protocol for tp in TRUSTED_PROTOCOLS):
            continue
        filtered.append({
            "name": f"{p['symbol']} ({p['protocol']}) - {p['chain']}",
            "id": p['pool_id'],
            "apy": p['apy'],
            "tvl": p['tvl_usd']
        })
    
    return sorted(filtered, key=lambda x: x['apy'], reverse=True)

try:
    valid_pools = fetch_valid_pools()
    if not valid_pools:
        st.warning("⚠️ No pools in database. Run: `python -m src.scheduler.collector --once`")
        st.stop()
        
    pool_options = {p['name']: p['id'] for p in valid_pools}
    pool_names = list(pool_options.keys())
    
    c1, c2, c3 = st.columns(3)

    with c1:
        # Default to a safe low-yield asset (usually near bottom of sorted list)
        ideal_index = len(pool_names) - 5
        # Ensure index is within valid bounds [0, len-1]
        safe_index = max(0, min(len(pool_names) - 1, ideal_index))
        
        current_asset_name = st.selectbox("Current Asset (Holding)", pool_names, index=safe_index)
        current_asset_id = pool_options[current_asset_name]

    with c2:
        # Default to high yield (top of list -> index 0)
        target_asset_name = st.selectbox("Target Asset (Opportunity)", pool_names, index=0)
        target_asset_id = pool_options[target_asset_name]

    with c3:
        capital_input = st.number_input("Total Capital ($)", min_value=1000, value=100000, step=1000)

    # Current Allocations (Dynamic)
    allocations = {
        current_asset_id: capital_input,
        target_asset_id: 0
    }

except Exception as e:
    st.error(f"Failed to load pools: {e}")
    st.stop()


# Display Context (from DB data, no API call)
if valid_pools:
    c_data = next((p for p in valid_pools if p['id'] == current_asset_id), None)
    t_data = next((p for p in valid_pools if p['id'] == target_asset_id), None)
    
    if c_data and t_data:
        st.info(f" **Context:** You hold **${capital_input:,.0f}** in {current_asset_name} earning **{c_data['apy']:.2f}%**. The opportunity is {target_asset_name} at **{t_data['apy']:.2f}%**.")
    
    # Show last update time
    db = TimeseriesDB()
    last_update = db.get_last_updated()
    if last_update:
        st.caption(f"Data as of: {last_update} UTC")


# Helper: Fetch History (from DB, no API call)
def get_apy_history(pool_id):
    """Get 30-day APY history from local DB"""
    db = TimeseriesDB()
    history = db.get_pool_history(pool_id, days=30)
    return history if history else []

# --- Sidebar Scanner ---
with st.sidebar:
    
    
    # Refresh button
    st.markdown("---")
    if st.button("Refresh Pool Data", key="refresh_data"):
        with st.spinner("Fetching fresh data from DefiLlama..."):
            import subprocess, sys
            result = subprocess.run(
                [sys.executable, "-m", "src.scheduler.collector", "--once"],
                capture_output=True, text=True, timeout=30,
                stdin=subprocess.DEVNULL,
                cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            )
            if result.returncode == 0:
                st.success("Data refreshed!")
                st.rerun()
            else:
                st.error(f"Refresh failed: {result.stderr[-200:]}")
    
    last_ts = TimeseriesDB().get_last_updated()
    if last_ts:
        st.caption(f"Last update: {last_ts} UTC")

# --- Tabs Layout ---
tab1, tab2, tab3 = st.tabs([
    " AI Decision Engine", 
    " Autonomous Monitor",
    " System Logs"
])

with tab2:
    
    with st.expander("ℹ️ How it works: Autonomous Monitor"):
        st.markdown("""
        **What it is:** The real-time operations center for the AI Yield Rebalancer service. 
        **How it works:** 
        1. **Live Feed:** Directly tails the `rebalancer.log` to show you exactly what the AI is thinking *right now*.
        2. **Atomic Lifecycle Log:** Every decision (SUCCESS, HOLD, or ABORT) is recorded with a 4-phase audit trail:
           - **Phase 1 (Opportunity Gap):** Identifying yield differentials in the market.
           - **Phase 2 (Go/No-Go):** Calculating gas, slippage, and ROI to ensure profitability.
           - **Phase 3 (Atomic Execution):** The Flashbots/Anvil transaction sequence.
           - **Phase 4 (Monitoring):** Post-execution health checks and trend tracking.
        """)
    st.markdown("###  Autonomous Rebalancer Monitor")
    st.caption("Real-time monitoring of the autonomous rebalancer service during the long-duration test.")

    try:
        tracker = PredictionTracker()
        accuracy = tracker.get_accuracy_stats()
        
        db = TimeseriesDB()

        # Prediction Feed
        st.markdown("#### Autonomous Decision Feed")
        st.caption(f"Live Feed • Last Polled: {datetime.utcnow().strftime('%H:%M:%S')} UTC")
        
        if "feed_limit" not in st.session_state:
            st.session_state["feed_limit"] = 15
            
        preds = tracker.get_all_predictions(limit=st.session_state["feed_limit"])
        
        if preds:
            for p in preds:
                status = p['prediction_type']
                icon = "🟡" # Default Hold
                if status in ["REBALANCE", "SUCCESS"]: icon = "🟢"
                elif status == "ABORTED": icon = "🔴"
                
                with st.expander(f"{icon} Cycle {p.get('id', 0)} - {p['timestamp'][11:16]} - {status}"):
                    # Parse Market Context
                    ctx = {}
                    try:
                        if p.get('market_context'):
                            ctx = json.loads(p['market_context'])
                        if not isinstance(ctx, dict): ctx = {}
                    except: ctx = {}

                    # Fix logical ghost: Use context's current_pool_symbol
                    cur_sym = ctx.get('current_pool_symbol', 'CASH')

                    
                    # --- NEW: Financial Performance (User Requested) ---
                    st.markdown("#### 💸 Financial Performance")
                    fp0, fp1, fp2, fp3, fp4 = st.columns(5)
                    
                    metrics = ctx.get('metrics', {})
                    if not isinstance(metrics, dict): metrics = {}
                    

                    # 1. Net ROI % (The User's Requested ROI)
                    net_roi = metrics.get('net_roi_pct', 0.0)
                    fp0.metric("Net ROI", f"{net_roi:+.5f}%", help="Total return vs Initial Capital after all fees")

                    # 2. Net Gain (Yield Gap)
                    net_gain = (p.get('target_pool_apy', 0)) - (p.get('current_pool_apy', 0))
                    gain_label = "TVL"
                    fp1.metric(gain_label, f"{p.get('current_pool_tvl', 0):.2f}")

                    # 3. ROI Period (Days)
                    raw_roi = metrics.get('roi_days') or metrics.get('break_even_days')
                    
                    # Safe Type Conversion
                    try:
                        if raw_roi is None:
                            roi_val = None
                        else:
                            roi_val = float(raw_roi)
                    except Exception:
                        roi_val = None
                    
                    # Logic
                    if roi_val is None:
                        roi_str = "N/A"
                        label_str = "Break-Even"
                    elif roi_val == float('inf') or roi_val == float('-inf'):
                        roi_str = "Loss" if net_gain < 0 else "Infinite"
                        label_str = "Status"
                    elif roi_val > 3650:
                        roi_str = "> 10 Years"
                        label_str = "Break-Even"
                    else:
                        roi_str = f"{roi_val:.1f} Days"
                        label_str = "Break-Even"

                    fp2.metric(label_str, roi_str)
                    
                    # 4. Target APY
                    fp3.metric("Target APY", f"{(p.get('target_pool_apy', 0)):.1f}%")

                    # 5. Pool Impact (Slippage)
                    impact = metrics.get('estimated_slippage', 0.0005)
                    fp4.metric("Slippage", f"-{impact*100:.2f}%")
                    
                    # --- Cost & Profit Analysis (New Section) ---
                    metrics = ctx.get('metrics', {})
                    if not isinstance(metrics, dict): metrics = {}
                    
                    gas = sum([metrics.get(k, 0) for k in ['gas_exit', 'gas_bridge', 'gas_enter']])
                    fees = metrics.get('swap_fees', 0)
                    roi = metrics.get('roi_days')

                    if status in ["ABORTED", "HOLD"] and gas == 0 and fees == 0:
                        st.info("**No Friction**: Execution was aborted before transaction scheduling. No gas or swap fees were modeled.")
                    elif gas > 0 or fees > 0:
                        st.divider()
                        st.markdown("#### 💸 Cost & Profit Analysis")
                        mp1, mp2, mp3, mp4 = st.columns(4)
                        
                        mp1.metric("⛽ Gas", f"${gas:.2f}")
                        mp2.metric("🔄 Fees", f"${fees:.2f}")
                        mp3.metric("📉 Total Cost", f"${metrics.get('total_conversion_loss', gas+fees):.2f}")
                        
                        if roi is not None and roi < 365:
                            mp4.metric("📅 ROI", f"{roi:.1f} days", delta="Breakeven", delta_color="normal")
                        else:
                            mp4.metric("📅 ROI", "N/A" if roi is None else "> 1 Year", delta="Computation" if roi is None else "SLOW", delta_color="off")

                    # Row 2: Comparison Matrix
                    st.markdown("#### Comparison ")
                    mc1, mc2, mc3 = st.columns(3)
                    
                    # Current
                    mc1.markdown(f"**Current: {cur_sym}**")
                    mc1.write(f"APY: {(p.get('current_pool_apy', 0)):.2f}%")
                    
                    # Target
                    target_meta = ctx.get('target_metadata', {})
                    if not isinstance(target_meta, dict): target_meta = {}
                    target_symbol = target_meta.get('symbol', 'Unknown')
                    mc2.markdown(f"**Target: {target_symbol}**")
                    mc2.write(f"APY: {(p.get('target_pool_apy')):.2f}%")

                    # Phase Log
                    if ctx.get('phase_logs'):
                        st.divider()
                        st.markdown("#### 📜 Atomic Lifecycle Log")
                        for log in ctx['phase_logs']:
                            # Style based on phase emoji
                            if "🔍" in log: st.info(log)
                            elif "📊" in log: st.warning(log)
                            elif "⚡" in log: st.success(log)
                            elif "💓" in log: st.info(log)
                            else: st.write(log)
                    
                    # Delta
                    t_apy = p.get('target_pool_apy', 0)
                    c_apy = p.get('current_pool_apy', 0)
                    gain = t_apy - c_apy
                    gas_cost = p.get('gas_cost', 0)
                    mc3.write(f"Net Gain: :green[+{gain:.2f}% APY]")

                  
                    # Detailed Analysis Charts
                    if st.checkbox(f"View Graphs for Cycle {p.get('id', 0)}", key=f"analysis_{p.get('id', 0)}"):
                        dc1, dc2 = st.columns(2)
                        
                        # A. Runner Ups Table
                        with dc1:
                            st.markdown("##### 🏁 Runner-Ups")
                            runners = ctx.get('runner_ups', [])
                            if runners:
                                st.dataframe(pd.DataFrame(runners), use_container_width=True, hide_index=True)

                        # B. Gas Efficiency Meter
                        with dc2:
                            st.markdown("##### ⛽ Efficiency")
                            monthly_profit = ((p.get('capital_usd',100000) or 100000) * (gain/100) / 12)
                            if monthly_profit > 0:
                                efficiency = (metrics.get('total_conversion_loss', 0) / monthly_profit) * 100
                                if efficiency < 5: st.success(f"Cost Impact: {efficiency:.1f}% of monthly gain")
                                elif efficiency < 15: st.warning(f"Cost Impact: {efficiency:.1f}% of monthly gain")
                                else: st.error(f"Cost Impact: {efficiency:.1f}% (Too High)")

                    # --- NEW: Execution Monitor (Block & Method) ---
                    st.markdown("#### 🛠️ Execution Monitor")
                    em1, em2, em3 = st.columns(3)
                    # Fallback to heartbeat block if execution didn't happen
                    block = metrics.get('execution_block') or metrics.get('heartbeat_block') or 'N/A'
                    method = metrics.get('execution_method', 'HOLD' if status == "ABORTED" else "SWAP")
                    
                    em1.metric("Status Block", str(block))
                    em2.metric("Execution Method", method)
                    em3.metric("Signal Status", "LIVE" if status == "SUCCESS" else "SIMULATED")

                    st.info(f"**Reason:** {p['reason']}")
            
            if len(preds) >= st.session_state["feed_limit"]:
                if st.button("Load More History...", use_container_width=True):
                    st.session_state["feed_limit"] += 15
                    st.rerun()
        else:
            st.info("Waiting for the first autonomous cycle to complete...")
            
        # Live Log Simulation
        st.markdown("#### Rebalancer Service Logs")
        if st.checkbox("Show Detailed Logs", value=True):
            log_path = "data/rebalancer.log"
            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    lines = f.readlines()
                    tail = "".join(lines[-50:])
                    st.code(tail)
            else:
                st.info("Log file not found yet.")
            
            if st.button("🔄 Refresh Logs"):
                st.rerun()
            
    except Exception as e:
        st.error(f"Failed to load stability test data: {e}")

with tab1:
    # Fetch AI Advice from latest prediction
    advice_text = "AI is currently auditing the market. Wait for the next heartbeat."
    if latest_preds:
        last = latest_preds[0]
        ctx = json.loads(last['market_context']) if last.get('market_context') else {}
        metrics = ctx.get('metrics', {})
        if not isinstance(metrics, dict): metrics = {}
        
        # Generate Human Readable Advice
        if last['prediction_type'] == "HOLD":
            advice_text = f"**AI SUGESTS WAIT.** {last['reason']}. Movement now would incur ${metrics.get('total_costs_usd', 0):.2f} in friction for only {last.get('target_pool_apy', 0) - last.get('current_pool_apy', 0):+.2f}% yield gain."
        elif last['prediction_type'] == "REBALANCE":
            advice_text = f"**AI SUGGESTS REBALANCE.** {last['reason']}. Projected break-even: {metrics.get('break_even_days', 0):.1f} days."
        else:
            # Handle Aborted/Emergency states
            r_msg = last['reason']
            if "[Liquidity Toxic]" in r_msg or "Slippage too high" in r_msg:
                advice_text = f"**LIQUIDITY TRAP detected.** AI blocked a move because the target pool is too shallow for your portfolio size. Rebalancing now would cause excessive slippage."
            elif "[ABORT_ORACLE_FAILURE]" in r_msg:
                advice_text = "**ORACLE FAILURE.** Protocols reporting $0 TVL. Halting for data integrity."
            else:
                advice_text = f"{r_msg}"

    st.markdown(f"""
        <div style="background: rgba(255, 255, 255, 0.05); padding: 25px; border-radius: 10px; margin-top: 20px; border: 1px solid rgba(255,255,255,0.1);">
            <h2 style="margin-top:0;"> Last ML Decision</h2>
            <p style="font-size:1.1em; line-height:1.6;">{advice_text}</p>
        </div>
    """, unsafe_allow_html=True)

    st.divider()
    st.subheader("Manual Sandbox")
    sandbox_cols = st.columns([2, 1])
    with sandbox_cols[0]:
        st.info("Adjust parameters to see how AI recaluclates Net ROI in real-time.")
        s_target = st.selectbox("Simulate Target", pool_names, index=0, key="sb_target")
        s_cap = st.slider("Simulate Portfoio Size ($)", 1000, 1000000, int(capital_input), key="sb_cap")
    
    with sandbox_cols[1]:
        st.markdown("#### Real-time Recalc")
        # Dummy recalc for sandbox
        t_pool = next((p for p in valid_pools if p['name'] == s_target), None)
        c_pool = next((p for p in valid_pools if p['id'] == current_asset_id), None)
        if t_pool and c_pool:
            gain = float(t_pool['apy'] or 0) - float(c_pool['apy'] or 0)
            st.write(f"Yield Delta: **{gain:+.2f}%**")
            st.write(f"Est. Monthly Gain: **${(s_cap * gain / 100 / 12):,.2f}**")
            st.button("🚀 Execute on Fork", disabled=True, help="Disabled in monitor mode")
    
    col_ask1, col_ask2 = st.columns([3, 1])
    with col_ask2:
        force_exec = st.checkbox("Force Executive Override", help="Simulate a forced rebalance (Bypass Risk Checks)")
    
    with col_ask1:
        if st.button("Ask Brain for Decision", type="primary"):
            with st.spinner("Analyzing market data, risk models, and gas costs..."):
                # Payload must use IDs as keys for allocations
                allocations_payload = {
                    current_asset_id: float(capital_input),
                    target_asset_id: 0.0
                }
                
                payload = {
                    "portfolio_id": "dashboard_live_test",
                    "current_allocations": allocations_payload,
                    "force_execution": force_exec
                }
            
            try:
                response = requests.post(f"http://{api_host}:8000/inference/predict", json=payload)
                if response.status_code != 200:
                    st.error(f"API Error {response.status_code}: {response.text}")
                else:
                    result = response.json()
                    st.session_state['last_decision'] = result
                    st.session_state['last_decision_response'] = result 
    
            except Exception as e:
                st.error(f"Error communicating with Brain: {e}")

    # --- Reusable Display Function ---
    def display_brain_result(result):
        # Display Result
        c1, c2, c3 = st.columns(3)
        
        # Color code action
        action_color = "green" if result['action'] == "REBALANCE" else "red"
        c1.markdown(f"#### Action: :{action_color}[{result['action']}]")
        
        c2.metric("Confidence Score", f"{result['confidence']*100:.0f}%")
        c3.metric("Net APY Gain", f"{result['net_apy_gain']*100:.2f}%")
        
        st.info(f"**Reasoning:** {result['reason']}")
        
        # Row 2: Pool Metadata
        with st.expander("📋 Pool Metadata (Addresses & Chain)", expanded=True):
            meta = result.get('market_context', {}).get('target_metadata', {})
            if meta:
                mp1, mp2 = st.columns(2)
                pool_addr = meta.get('pool', 'N/A')
                chain = meta.get('chain', 'Ethereum')
                
                # Helper for Etherscan links
                def get_scan_link(addr, chain_name):
                    prefix = "https://etherscan.io/address"
                    if chain_name.lower() == 'base': prefix = "https://basescan.org/address"
                    return f"[{addr[:6]}...{addr[-4:]}]({prefix}/{addr})"

                mp1.markdown(f"**Pool**: {get_scan_link(pool_addr, chain)}")
                mp1.markdown(f"**Chain**: {chain}")
                mp1.markdown(f"**TVL**: ${meta.get('tvlUsd', 0):,.2f}")
                
                tokens = meta.get('underlyingTokens', [])
                if tokens:
                    token_links = [get_scan_link(t, chain) for t in tokens]
                    mp2.markdown(f"**Tokens**: {', '.join(token_links)}")
            else:
                st.info("No detailed metadata available.")

        st.markdown("### 💸 Cost & Profit Analysis")
        m = result.get('metrics')
        if m:
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("⛽ Gas Cost", f"${m.get('gas_exit',0) + m.get('gas_bridge',0) + m.get('gas_enter',0):.2f}")
            mc2.metric("🔄 Swap Fees", f"${m.get('swap_fees',0):.2f}")
            mc3.metric(" Total Loss", f"${m.get('total_conversion_loss',0):.2f}", help="Gas + Swap Fees")
            
            roi = m.get('roi_days', 999)
            if roi < 365:
                st.success(f"📅 **ROI Period:** Breakeven in **{roi:.1f} days**.")
            else:
                st.warning(f"📅 **ROI Period:** >1 Year ({roi:.1f} days). Not recommended.")
        else:
            st.info("Metrics not calculated (likely due to missing data or 0 confidence).")

        with st.expander("Raw API Response"):
            st.json(result)

    # --- Render Result (Either Fresh or Persisted) ---
    if 'last_decision_response' in st.session_state:
        display_brain_result(st.session_state['last_decision_response'])

    st.markdown("###  Historical Yield Analysis (30 Days)")
    
    if current_asset_id and target_asset_id:
        with st.spinner("Fetching historical data..."):
            hist_current = get_apy_history(current_asset_id)
            hist_target = get_apy_history(target_asset_id)
            
            if hist_current and hist_target:
                # Process Data
                df_c = pd.DataFrame(hist_current)
                df_c['date'] = pd.to_datetime(df_c['timestamp'], format='mixed')
                df_c = df_c.set_index('date').sort_index()
                
                df_t = pd.DataFrame(hist_target)
                df_t['date'] = pd.to_datetime(df_t['timestamp'], format='mixed')
                df_t = df_t.set_index('date').sort_index()
                
                # Combine
                combined = pd.DataFrame({
                    "Current Asset (APY%)": df_c['apy'],
                    "Target Asset (APY%)": df_t['apy']
                })
                
                st.line_chart(combined)
                
                # --- AI Graph Insights ---
                def generate_insights(series, name):
                    insights = []
                    if series.empty: return ["No data available."]
                    
                    # 1. Data Quality Check
                    missing_ratio = series.isna().sum() / len(series)
                    if missing_ratio > 0.2:
                        insights.append(f"⚠️ **Data Gaps**: {name} is missing {missing_ratio*100:.0f}% of data points (Illiquid/ unstable feed).")
                    
                    # 2. Volatility Check
                    median = series.median()
                    maximum = series.max()
                    if median > 0 and (maximum - median) > (0.5 * median):
                        insights.append(f" **Spike Alert**: {name} had a massive spike to {maximum:.2f}% (median: {median:.2f}%). Likely artificial.")
                        
                    # 3. Crash Detection
                    current = series.iloc[-1] if not series.empty else 0
                    if maximum > 0 and current < (0.5 * maximum):
                        insights.append(f"🔻 **Crash Warning**: {name} is down {((maximum-current)/maximum)*100:.0f}% from its 30-day peak.")
                        
                    # 4. Trend
                    recent_avg = series.tail(3).mean()
                    long_avg = series.mean()
                    if recent_avg > (long_avg * 1.1):
                        insights.append(f" **Uptrend**: {name} is trending up (+{(recent_avg/long_avg - 1)*100:.1f}% vs 30d avg).")
                    elif recent_avg < (long_avg * 0.9):
                        insights.append(f" **Downtrend**: {name} is cooling off (-{(1 - recent_avg/long_avg)*100:.1f}% vs 30d avg).")
                        
                    return insights

                st.markdown("###  AI Chart Analysis")
                c_insights = generate_insights(df_c['apy'], current_asset_name)
                t_insights = generate_insights(df_t['apy'], target_asset_name)
                
                for i in c_insights + t_insights:
                    if "⚠️" in i or "🔻" in i:
                        st.warning(i)
                    else:
                        st.info(i)

                # Volatility/Risk Scorecard
                st.markdown("###  Risk Scorecard")
                
                # Calculate Metrics
                def calc_metrics(series):
                    if series.empty: return 0, 0, 0
                    vol = series.std()
                    mean_apy = series.mean()
                    sharpe = (mean_apy / vol) if vol > 0 else 0
                    
                    # Drawdown: Drop from Peak
                    maximum = series.max()
                    current = series.iloc[-1]
                    max_dd = ((maximum - current) / maximum * 100) if maximum > 0 else 0
                    
                    return vol, sharpe, max_dd

                c_vol, c_sharpe, c_dd = calc_metrics(combined["Current Asset (APY%)"])
                t_vol, t_sharpe, t_dd = calc_metrics(combined["Target Asset (APY%)"])

                rc1, rc2, rc3 = st.columns(3)
                
                rc1.metric(f"Volatility (StdDev)", f"{t_vol:.2f}%", delta=f"{(t_vol-c_vol):.2f}%", delta_color="inverse", help="Lower is better")
                rc2.metric(f"Sharpe Ratio (Yield/Risk)", f"{t_sharpe:.2f}", delta=f"{(t_sharpe-c_sharpe):.2f}", help="Higher is better")
                rc3.metric(f"Yield Decay (Peak-to-Now)", f"{t_dd:.2f}%", delta=f"{(t_dd-c_dd):.2f}%", delta_color="inverse", help="How much yield has dropped from 30d High")

            else:
                st.warning("Historical data not available for one or both assets.")


    # st.divider()
    # st.markdown("### Portfolio Details")
    # st.markdown("### Portfolio Allocations")
    
    # # Current single-pool allocation
    # if 'last_decision' in st.session_state:
    #     decision = st.session_state['last_decision']
    #     if decision['action'] == "REBALANCE":
    #         st.success("✅ **Proposed Rebalancing Active**")
    #         data = {
    #             "Asset": [current_asset_name, target_asset_name],
    #             "Allocation ($)": [capital_input, capital_input],
    #             "State": ["Current", "Proposed"]
    #         }
    #         st.bar_chart(pd.DataFrame(data).set_index("State"), stack=False)
    #     else:
    #         st.info("⏸️ **No Rebalancing Proposed (HOLD)**")
    #         st.metric("Current Position", f"${capital_input:,.2f}", f"100% {current_asset_name}")
    # else:
    #     st.info("Run 'Ask Brain' in the Live Decision tab to see proposed changes.")
    

with tab3:
    st.markdown("### Platform Operations Center")
    st.caption("Real-time telemetry from the rebalancer's internal engines.")
    
    col_logs_1, col_logs_2 = st.columns(2)
    
    with col_logs_1:
        # 1. Anvil & Fork Manager
        with st.expander("⛓️ 1. Anvil & Fork Manager", expanded=True):
            st.markdown("#### Local Fork Health")
            st.write("Monitoring `anvil_reset` cycles and contract redeployments.")
            
            log_files = ["data/anvil.log", "data/anvil_output.log"]
            selected_log = st.selectbox("Source:", log_files, key="log_sel_anvil")
            
            if os.path.exists(selected_log):
                with open(selected_log, "r") as f:
                    lines = f.readlines()
                    st.code("".join(lines[-50:]), language="text")
            else:
                st.warning(f"Waiting for {selected_log}...")

    with col_logs_2:
        # 2. ML Audit (LSTM/XGBoost)
        with st.expander("🧠 2. LSTM & XGBoost Intelligence", expanded=True):
            st.markdown("#### ML Conviction Stream")
            st.write("Filtering `rebalancer.log` for APY projections and risk scores.")
            
            rebalancer_log = "data/rebalancer.log"
            if os.path.exists(rebalancer_log):
                with open(rebalancer_log, "r") as f:
                    lines = f.readlines()
                    ml_lines = [l for l in lines if any(k in l.upper() for k in ["ML", "AUDIT", "BRAIN", "PREDICT", "CONVICTION", "LSTM", "XGB", "APY"])]
                    if ml_lines:
                        st.code("".join(ml_lines[-50:]), language="text")
                    else:
                        st.info("No ML signals recorded in current session.")
            else:
                 st.warning("Rebalancer log offline")

    # 3. Blocks & Decisions (Full Width)
    with st.expander("📡 3. Rebalancing Decisions & Block Heartbeat", expanded=True):
        st.markdown("#### Atomic Lifecycle Stream")
        st.write("Monitoring block-by-block evaluations and execution verdicts.")
        
        if os.path.exists(rebalancer_log):
            with open(rebalancer_log, "r") as f:
                lines = f.readlines()
                dec_lines = [l for l in lines if any(k in l.upper() for k in ["BLOCK", "DECISION", "CYCLE", "VERDICT", "GAS", "REBALANCE", "SYNC"])]
                if dec_lines:
                    st.code("".join(dec_lines[-100:]), language="text")
                else:
                    st.info("No decision heartbeats found.")
        else:
            st.warning("Rebalancer log offline")

    st.divider()
    st.caption("Logs automatically tail the last 50-100 entries. For full history, inspect the `data/` directory.")
