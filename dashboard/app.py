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
from streamlit_autorefresh import st_autorefresh

# Auto-refresh the page every 30 seconds to keep stats live without manual refresh
# This helps in monitoring the rebalancer's logs and predictions in real-time.
st_autorefresh(interval=30 * 1000, key="datarefresh")

st.set_page_config(page_title="AI Yield Brain - Live Status", layout="wide")

st.title(" AI Yield Rebalancer: Live Brain Status")

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
st.sidebar.subheader("🤖 Agent Wallet")

try:
    # Connect to DB to get latest state
    tracker = PredictionTracker()
    latest_preds = tracker.get_all_predictions(limit=1)
    
    if latest_preds:
        latest = latest_preds[0]
        ctx = json.loads(latest['market_context']) if latest.get('market_context') else {}
        metrics = ctx.get('metrics', {})
        
        # 1. Balance
        # Default to 0 if not yet recorded in latest cycle
        balance = metrics.get('wallet_balance_eth', 0.0)
        st.sidebar.metric("ETH Balance", f"{balance:.4f} ETH")
        
        # 2. Current Position
        current_pool = latest.get('current_pool_id', 'Unknown')
        # Try to get symbol if available
        if ctx.get('current_pool_symbol'):
            current_pool = ctx['current_pool_symbol']
            
        st.sidebar.metric("Active Strategy", current_pool)
        
        # 3. Last Heartbeat
        last_time = latest.get('timestamp', '').split('T')[-1][:5]
        st.sidebar.caption(f"Last Update: {last_time} UTC")
        
    else:
        st.sidebar.warning("No Agent History Found")
        
except Exception as e:
    st.sidebar.error(f"Wallet Error: {str(e)}")

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
                cwd="/app"
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
tab1, tab2, tab3, tab4 = st.tabs([
    "🧠 AI Decision Engine", 
    "📡 Autonomous Monitor",
    "📈 Analysis & Backtest", 
    "🧪 Simulation Lab"
])

with tab2:
    with st.expander("ℹ️ How it works: Autonomous Monitor"):
        st.markdown("""
        **What it is:** The real-time operations center for the AI Yield Rebalancer service. 
        **How it works:** 
        1. **Live Feed:** Directly tails the `rebalancer.log` to show you exactly what the AI is thinking *right now*.
        2. **Atomic Lifecycle Log:** Every decision (SUCCESS, HOLD, or ABORT) is recorded with a 4-phase audit trail:
           - 🔍 **Phase 1 (Opportunity Gap):** Identifying yield differentials in the market.
           - 📊 **Phase 2 (Go/No-Go):** Calculating gas, slippage, and ROI to ensure profitability.
           - ⚡ **Phase 3 (Atomic Execution):** The Flashbots/Anvil transaction sequence.
           - 💓 **Phase 4 (Monitoring):** Post-execution health checks and trend tracking.
        """)
    st.markdown("### 📡 Autonomous Rebalancer Monitor")
    st.caption("Real-time monitoring of the autonomous rebalancer service during the long-duration test.")

    try:
        tracker = PredictionTracker()
        accuracy = tracker.get_accuracy_stats()
        
        # Dashboard Overview
        lc1, lc2, lc3, lc4 = st.columns(4)
        lc1.metric("Cycles Processed", accuracy['total_predictions'])
        
        # Get latest for status
        latest_preds = tracker.get_all_predictions(limit=1)
        latest_status = latest_preds[0]['prediction_type'] if latest_preds else "Idle"
        lc2.metric("Latest Status", latest_status)
        
        lc3.metric("System Uptime", "Active")
        lc4.metric("Accuracy (7d)", f"{accuracy['accuracy_pct']}%")

        # Prediction Feed
        st.markdown("#### Autonomous Decision Feed")
        
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
                    except: pass

                    # Fix logical ghost: Use context's current_pool_symbol
                    cur_sym = ctx.get('current_pool_symbol', 'CASH')

                    # Row 1: Dashboard Overlays
                    st.markdown(f"#### 🧠 Brain Intelligence (Confidence: {p.get('confidence',0)*100:.1f}%)")
                    c1, c2, c3 = st.columns(3)
                    
                    # A. Inference Factors
                    with c1:
                        st.markdown("**Inference Factors**")
                        cb = ctx.get('confidence_breakdown', {})
                        st.caption(f"📈 Yield Momentum: {cb.get('yield_momentum', 0.5):.2f}")
                        st.caption(f"🛡️ Stability Score: {cb.get('stability_score', 0.5):.2f}")
                        st.caption(f"🔗 Correlation: {cb.get('token_correlation', 0.2):.2f}")
                    
                  
                    # C. Market Depth
                    with c2:
                        st.markdown("**Market Depth**")
                        tm = ctx.get('target_metadata', {})
                        st.caption(f"💰 TVL: ${tm.get('tvlUsd',0)/1e6:.1f}M")
                        st.caption(f"🔄 Route: 1inch Aggregator")

                    st.divider()
                    
                    # --- NEW: Financial Performance (User Requested) ---
                    st.markdown("#### 💸 Financial Performance")
                    fp0, fp1, fp2, fp3, fp4 = st.columns(5)
                    
                    metrics = ctx.get('metrics', {})
                    
                    # 0. Dynamic Managed Capital
                    capital = p.get('capital_usd', 100000.0)
                    fp0.metric("Managed Capital", f"${capital:,.0f}", help="Real portfolio size used for AI decision math")

                    # 1. Net Gain (APY %) (Moved Up)
                    net_gain = (p.get('target_pool_apy', 0) or 0) - (p.get('current_pool_apy', 0) or 0)
                    fp2.metric("Net Gain", f"{net_gain:+.2f}%", help="Yield difference (Target - Current APY)")

                    # 2. ROI Period (Days)
                    raw_roi = metrics.get('roi_days')
                    
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
                        label_str = "ROI Period"
                    elif roi_val == float('inf') or roi_val == float('-inf'):
                        roi_str = "Loss" if net_gain < 0 else "Infinite"
                        label_str = "ROI Status"
                    elif roi_val > 3650:
                        roi_str = "> 10 Years"
                        label_str = "ROI Period"
                    else:
                        roi_str = f"{roi_val:.1f} Days"
                        label_str = "ROI Period"

                    fp1.metric(label_str, roi_str, help="Time to recover gas/fees")
                    # 3. APR (Use Target APY for simplicity as requested)
                    fp3.metric("Target APR", f"{(p.get('target_pool_apy', 0) or 0):.2f}%", help="Projected annual rate")

                    # 4. Pool Impact (Slippage)
                    # Try to get slippage, default 0.05%
                    impact = metrics.get('estimated_slippage', 0.0005)
                    fp4.metric("Pool Impact", f"-{impact*100:.2f}%", help="Estimated price impact on liquidity pool")

                    st.divider()

                    # Row 2: Comparison Matrix
                    st.markdown("#### ⚖️ Comparison Matrix")
                    mc1, mc2, mc3 = st.columns(3)
                    
                    # Current
                    mc1.markdown(f"**Current: {cur_sym}**")
                    mc1.write(f"APY: {(p.get('current_pool_apy', 0) or 0):.2f}%")
                    
                    # Target
                    target_meta = ctx.get('target_metadata', {})
                    target_symbol = target_meta.get('symbol', 'Unknown')
                    mc2.markdown(f"**Target: {target_symbol}**")
                    mc2.write(f"APY: {(p.get('target_pool_apy', 0) or 0):.2f}%")

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
                    t_apy = p.get('target_pool_apy', 0) or 0
                    c_apy = p.get('current_pool_apy', 0) or 0
                    gain = t_apy - c_apy
                    gas_cost = p.get('gas_cost', 0) or 0
                    st.markdown(f"**The Delta**")
                    mc3.write(f"Net Gain: :green[+{gain:.2f}% APY]")
                    st.caption(f"Profit Velocity: :blue[+${((p.get('capital_usd',100000) or 100000) * gain/100/12):.0f}/mo]")

                    # --- Cost & Profit Analysis (New Section) ---
                    metrics = ctx.get('metrics', {})
                    if metrics:
                        st.divider()
                        st.markdown("#### 💸 Cost & Profit Analysis")
                        mp1, mp2, mp3, mp4 = st.columns(4)
                        
                        gas = metrics.get('gas_exit',0) + metrics.get('gas_bridge',0) + metrics.get('gas_enter',0)
                        fees = metrics.get('swap_fees',0)
                        roi = metrics.get('roi_days', 999)
                        
                        mp1.metric("⛽ Gas", f"${gas:.2f}")
                        mp2.metric("🔄 Fees", f"${fees:.2f}")
                        mp3.metric("📉 Total Cost", f"${metrics.get('total_conversion_loss', gas+fees):.2f}")
                        
                        if roi < 365:
                            mp4.metric("📅 ROI", f"{roi:.1f} days", delta="Breakeven", delta_color="normal")
                        else:
                            mp4.metric("📅 ROI", "> 1 Year", delta="SLOW", delta_color="inverse")

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
    with st.expander("ℹ️ How it works: AI Decision Engine"):
        st.markdown("""
        **What it is:** A high-fidelity sandbox for manual asset comparison and strategy validation.
        **How it works:**
        1. **Live Inference:** Select any two assets to trigger a real-time call to the AI Inference Engine.
        2. **Risk Scrutiny:** The AI runs a 'Risk Scorecard' analyzing 30-day volatility, Sharpe Ratios, and Yield Decay.
        3. **Cost Sensitivity:** It calculates a full 'ROI Break-even' period by factoring in live Gas prices and expected Slippage for your specific portfolio size.
        4. **Multi-Pool Sizing:** Use the **Trade Sizer** tool to calculate a diversified allocation (Max Pools / Max Cap) to minimize single-protocol risk.
        """)
    
    col_ask1, col_ask2 = st.columns([3, 1])
    with col_ask2:
        force_exec = st.checkbox("Force Executive Override", help="Simulate a forced rebalance (Bypass Risk Checks)")
    
    with col_ask1:
        if st.button("🧠 Ask Brain for Decision", type="primary"):
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


    st.divider()
    st.markdown("### Portfolio Details")
    st.markdown("### Portfolio Allocations")
    
    # Current single-pool allocation
    if 'last_decision' in st.session_state:
        decision = st.session_state['last_decision']
        if decision['action'] == "REBALANCE":
            st.success("✅ **Proposed Rebalancing Active**")
            data = {
                "Asset": [current_asset_name, target_asset_name],
                "Allocation ($)": [capital_input, capital_input],
                "State": ["Current", "Proposed"]
            }
            st.bar_chart(pd.DataFrame(data).set_index("State"), stack=False)
        else:
            st.info("⏸️ **No Rebalancing Proposed (HOLD)**")
            st.metric("Current Position", f"${capital_input:,.2f}", f"100% {current_asset_name}")
    else:
        st.info("Run 'Ask Brain' in the Live Decision tab to see proposed changes.")
    
    # --- AI Trade Sizer ---
    st.markdown("---")
    st.markdown("### AI Trade Sizing (Multi-Pool)")
    st.caption("Optimal capital allocation across top pools based on risk-adjusted scoring.")
    
    sz1, sz2 = st.columns(2)
    with sz1:
        sz_max_alloc = st.slider("Max Allocation per Pool (%)", 20, 80, 50, 5, key="sz_max")
    with sz2:
        sz_max_pools = st.slider("Max Pools", 1, 5, 3, 1, key="sz_pools")
    
    if st.button("Calculate Optimal Sizing", key="run_sizer"):
        with st.spinner("Scoring pools and calculating allocations..."):
            try:
                sizer = TradeSizer(
                    max_single_allocation=sz_max_alloc / 100,
                    max_pools=sz_max_pools
                )
                
                # Use the cached pool list
                pool_dicts = []
                for p in valid_pools:
                    pool_dicts.append({
                        'pool': p['id'],
                        'symbol': p['name'].split(' (')[0],
                        'chain': p['name'].split('- ')[-1] if '- ' in p['name'] else '',
                        'project': p['name'].split('(')[1].split(')')[0] if '(' in p['name'] else '',
                        'apy': p['apy'],
                        'tvlUsd': p['tvl'],
                        'stablecoin': True,
                        'apyPct1D': 0
                    })
                
                result = sizer.recommend(pool_dicts, total_capital=capital_input)
                
                if result['allocations']:
                    st.success(result['summary'])
                    
                    # Metrics row
                    sc1, sc2 = st.columns(2)
                    sc1.metric("Weighted APY", f"{result['weighted_apy']:.2f}%")
                    sc2.metric("Diversification", f"{result['diversification_score']}/100")
                    
                    # Allocation table
                    alloc_data = []
                    for a in result['allocations']:
                        alloc_data.append({
                            "Pool": a.symbol,
                            "Protocol": a.protocol,
                            "APY": f"{a.apy:.2f}%",
                            "Score": f"{a.score}/100",
                            "Allocation": f"{a.allocation_pct:.1f}%",
                            "Amount": f"${a.allocation_usd:,.0f}"
                        })
                    st.dataframe(pd.DataFrame(alloc_data), use_container_width=True, hide_index=True)
                    
                    # Bar chart
                    chart_df = pd.DataFrame({
                        "Pool": [a.symbol for a in result['allocations']],
                        "Allocation ($)": [a.allocation_usd for a in result['allocations']]
                    }).set_index("Pool")
                    st.bar_chart(chart_df)
                else:
                    st.warning("No suitable pools found for allocation.")
            except Exception as e:
                st.error(f"Sizing failed: {e}")

with tab3:
    with st.expander("ℹ️ How it works: Analysis & Backtest"):
        st.markdown("""
        **What it is:** A historical validation engine for the rebalancing strategy.
        **How it works:**
        1. **Historical Replay:** We take 30-365 days of actual DeFiLlama yield data and run our rebalancing logic against every daily timestamp.
        2. **Friction Simulation:** Every historical trade includes simulated Gas costs and slippage based on that day's market conditions.
        3. **Performance Metrics:** Calculates the 'Sharpe Ratio' (risk-adjusted return) and 'Max Drawdown' to prove that the AI would have outperformed a simple 'Buy & Hold' strategy.
        4. **Accuracy Tracking:** The **AI Prediction Accuracy** section tracks every *real* decision made by the service and cross-references it with 24-hour outcomes.
        """)
    st.markdown("### Strategy Backtest")
    st.caption("Simulate how the AI rebalancing strategy would have performed on historical data.")
    
    # --- Backtest Controls ---
    bc1, bc2, bc3 = st.columns(3)
    with bc1:
        bt_capital = st.number_input("Starting Capital ($)", min_value=1000, value=100000, step=10000, key="bt_capital")
    with bc2:
        bt_min_improvement = st.slider("Min APY Gain to Rebalance (%)", min_value=0.5, max_value=10.0, value=2.0, step=0.5, key="bt_min")
    with bc3:
        bt_apy_cap = st.slider("Max APY Cap (filter noise)", min_value=50, max_value=500, value=200, step=50, key="bt_cap")
    
    if st.button("Run Backtest", type="primary", key="run_bt"):
        # Check if we have data
        db = TimeseriesDB()
        stats = db.get_stats()
        
        if stats['total_records'] == 0:
            st.error("No historical data found. Run the collector first:\n`python -m src.scheduler.collector --backfill`")
        else:
            with st.spinner(f"Simulating strategy across {stats['unique_dates']} days of data..."):
                try:
                    engine = BacktestEngine(min_apy_improvement=bt_min_improvement)
                    result = engine.run(
                        initial_capital=bt_capital,
                        max_apy_cap=bt_apy_cap
                    )
                    
                    # --- Key Metrics ---
                    st.markdown("### Performance Summary")
                    m1, m2, m3, m4 = st.columns(4)
                    
                    total_ret_color = "normal" if result.total_return_pct > 0 else "inverse"
                    m1.metric("Total Return", f"{result.total_return_pct:.2f}%", f"${result.final_capital - result.initial_capital:,.2f}")
                    m2.metric("Annualized Return", f"{result.annualized_return_pct:.2f}%")
                    m3.metric("Sharpe Ratio", f"{result.sharpe_ratio:.2f}", help=">1 Good, >2 Great, >3 Excellent")
                    m4.metric("Max Drawdown", f"{result.max_drawdown_pct:.2f}%", delta_color="inverse")
                    
                    m5, m6, m7, m8 = st.columns(4)
                    m5.metric("Final Capital", f"${result.final_capital:,.2f}")
                    m6.metric("Total Trades", f"{result.total_trades}")
                    m7.metric("Win Rate", f"{result.win_rate:.0f}%")
                    m8.metric("Total Costs", f"${result.total_costs:,.2f}")
                    
                    # --- Equity Curve ---
                    st.markdown("### Equity Curve")
                    if result.daily_values:
                        eq_df = pd.DataFrame(result.daily_values)
                        eq_df['date'] = pd.to_datetime(eq_df['date'])
                        eq_df = eq_df.set_index('date')
                        
                        # Portfolio value over time
                        st.line_chart(eq_df['value'], use_container_width=True)
                        
                        # APY over time
                        st.markdown("### APY Earned Over Time")
                        st.area_chart(eq_df['apy'], use_container_width=True)
                    
                    # --- Trade Log ---
                    st.markdown("### Trade History")
                    if result.trades:
                        trade_data = []
                        for t in result.trades:
                            if t.action in ["REBALANCE", "ENTER"]:
                                trade_data.append({
                                    "Date": t.date,
                                    "Action": t.action,
                                    "From": t.from_pool,
                                    "From APY": f"{t.from_apy:.2f}%",
                                    "To": t.to_pool,
                                    "To APY": f"{t.to_apy:.2f}%",
                                    "Cost": f"${t.cost:.2f}",
                                    "Reason": t.reason
                                })
                        
                        if trade_data:
                            st.dataframe(pd.DataFrame(trade_data), use_container_width=True, hide_index=True)
                        else:
                            st.info("No trades were executed. The strategy held the initial position.")
                    
                    # --- Period Info ---
                    st.caption(f"Backtest period: {result.start_date} to {result.end_date} | Avg APY earned: {result.avg_apy_earned:.2f}%")
                    
                except Exception as e:
                    st.error(f"Backtest failed: {e}")
                    import traceback
                    st.code(traceback.format_exc())
    else:
        # Show DB stats
        try:
            db = TimeseriesDB()
            stats = db.get_stats()
            if stats['total_records'] > 0:
                st.success(f"Database: **{stats['total_records']:,}** records | **{stats['unique_pools']}** pools | **{stats['unique_dates']}** days ({stats['oldest_record']} to {stats['newest_record']})")
            else:
                st.warning("No historical data. Run: `python -m src.scheduler.collector --backfill`")
        except:
            st.warning("Database not initialized yet.")
    
    # --- Prediction Accuracy ---
    st.markdown("---")
    st.markdown("### AI Prediction Accuracy")
    st.caption("Tracks every Brain decision and validates against actual outcomes after 24 hours.")
    
    try:
        tracker = PredictionTracker()
        accuracy = tracker.get_accuracy_stats()
        
        if accuracy['total_predictions'] > 0:
            pa1, pa2, pa3, pa4 = st.columns(4)
            pa1.metric("Total Predictions", accuracy['total_predictions'])
            pa2.metric("Validated", accuracy['validated'])
            pa3.metric("Accuracy", f"{accuracy['accuracy_pct']}%",
                       help="% of validated predictions that were correct")
            pa4.metric("Pending Validation", accuracy['pending'])
            
            if accuracy['validated'] > 0:
                pb1, pb2, pb3 = st.columns(3)
                pb1.metric("HOLD Accuracy", f"{accuracy['hold_accuracy_pct']}%")
                pb2.metric("REBALANCE Accuracy", f"{accuracy['rebalance_accuracy_pct']}%")
                pb3.metric("Profit (if followed)", f"${accuracy['total_profit_if_followed']:,.2f}")
            
            # Prediction history table
            with st.expander("View All Predictions"):
                preds = tracker.get_all_predictions(limit=20)
                if preds:
                    pred_table = []
                    for p in preds:
                        pred_table.append({
                            "Date": p['timestamp'][:10],
                            "Type": p['prediction_type'],
                            "Confidence": f"{p['confidence']*100:.0f}%",
                            "Current APY": f"{p['current_pool_apy']:.2f}%",
                            "Target APY": f"{p['target_pool_apy']:.2f}%",
                            "Validated": "✅" if p['validated'] else "⏳",
                            "Correct": "✅" if p.get('was_correct') else ("❌" if p['validated'] else "—"),
                            "Reason": p['reason'][:50]
                        })
                    st.dataframe(pd.DataFrame(pred_table), use_container_width=True, hide_index=True)
        else:
            st.info("No predictions recorded yet. Click 'Ask Brain' in the Live Decision tab to generate predictions.")
    except Exception:
        st.info("Prediction tracker initializing...")

# --- Tab 5: On-Chain Fork ---
with tab4:
    with st.expander("ℹ️ How it works: Simulation Lab"):
        st.markdown("""
        **What it is:** An on-chain 'Safe Zone' for testing aggressive trades before they hit mainnet.
        **How it works:**
        1. **Mainnet Forking:** Uses Anvil to create a local mirror of the Ethereum blockchain.
        2. **Time Warping:** Allows you to 'Fast Forward' time by days or weeks to see how much interest the StrategyHub contract actually accrues.
        3. **Black Swan Mocking:** You can manually trigger 'Emergency Withdrawals' or simulate market crashes to see how the AI handles risk triggers.
        4. **Asset Snapshots:** Capture the state of the system, run a risky test, and then instantly 'Revert' if it fails—zero real capital lost.
        """)
    st.markdown("###  Local Mainnet Fork Status")
    
    rpc_url = os.getenv("RPC_URL", "http://localhost:8545")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    node_online = False
    try:
        node_online = w3.is_connected()
    except:
        pass

    if not node_online:
        st.warning("Local Fork (Anvil) is not running.")
        if st.button("🚀 Start Local Fork & Deploy Strategy"):
            with st.spinner("Starting Anvil and deploying contracts..."):
                import subprocess
                subprocess.run([sys.executable, "scripts/start_local_fork.py"], check=True, stdin=subprocess.DEVNULL)
                st.success("Fork started and StrategyHub deployed!")
                st.rerun()
    else:
        st.success("Mainnet Fork Online (localhost:8545)")
        
        # Load Deployed Address
        addr_file = "contracts/deployed_address.txt"
        if os.path.exists(addr_file):
            with open(addr_file, "r") as f:
                strategy_addr = f.read().strip()
            
            st.code(f"StrategyHub: {strategy_addr}")
            
            # Show live balances if we can
            try:
                # ABI for getBalances()
                abi = [
                    {"inputs": [], "name": "getBalances", "outputs": [
                        {"internalType": "uint256", "name": "aaveBalance", "type": "uint256"},
                        {"internalType": "uint256", "name": "compoundBalance", "type": "uint256"},
                        {"internalType": "uint256", "name": "idleBalance", "type": "uint256"},
                        {"internalType": "uint256", "name": "total", "type": "uint256"}
                    ], "stateMutability": "view", "type": "function"},
                    {"inputs": [
                        {"internalType": "uint256", "name": "newAaveBps", "type": "uint256"},
                        {"internalType": "uint256", "name": "newCompoundBps", "type": "uint256"}
                    ], "name": "rebalance", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
                    {"inputs": [], "name": "aaveAllocationBps", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
                    {"inputs": [], "name": "compoundAllocationBps", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}
                ]
                
                contract = w3.eth.contract(address=strategy_addr, abi=abi)
                bals = contract.functions.getBalances().call()
                aave_bps = contract.functions.aaveAllocationBps().call()
                comp_bps = contract.functions.compoundAllocationBps().call()
                
                f1, f2, f3, f4 = st.columns(4)
                f1.metric("Aave Balance", f"${bals[0]/1e6:,.2f}", f"{aave_bps/100}% target")
                f2.metric("Compound Balance", f"${bals[1]/1e6:,.2f}", f"{comp_bps/100}% target")
                f3.metric("Idle USDC", f"${bals[2]/1e6:,.2f}")
                f4.metric("Total Value", f"${bals[3]/1e6:,.2f}")
                
                # Rebalance Control
                st.markdown("### ⚡ Manual Rebalance Control")
                col_a, col_b = st.columns(2)
                with col_a:
                    aave_target = st.slider("Target Aave %", 0, 100, aave_bps // 100)
                with col_b:
                    comp_target = 100 - aave_target
                    st.write(f"Remaining Compound: {comp_target}%")
                
                if st.button("Execute On-Chain Rebalance"):
                    with st.spinner("Broadcasting rebalance..."):
                        # Use default anvil account
                        acct = w3.eth.account.from_key("0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
                        tx = contract.functions.rebalance(aave_target * 100, comp_target * 100).build_transaction({
                            'from': acct.address,
                            'nonce': w3.eth.get_transaction_count(acct.address),
                            'gas': 1000000,
                            'gasPrice': w3.to_wei('20', 'gwei')
                        })
                        signed_tx = w3.eth.account.sign_transaction(tx, acct.key)
                        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
                        st.success(f"Rebalance successful! TX: {tx_hash.hex()}")
                        time.sleep(2)
                        st.rerun()
                        
            except Exception as e:
                st.error(f"Failed to read contract: {e}")
        else:
            st.info("Fork is running but StrategyHub is not deployed. Restart the fork to redeploy.")

# --- Tab 6: Simulation Sandbox ---

    st.markdown("### 🧪 On-Chain Simulation Sandbox (Anvil)")
    
    if node_online:
        sim = SimController()
        
        # --- Time Warping ---
        st.markdown("#### ⏳ Time Warping")
        st.info("Advance time on the fork to observe interest accrual.")
        
        c1, c2, c3 = st.columns(3)
        if c1.button("Jump +1 Hour"):
            new_ts = sim.jump_forward(3600)
            st.success(f"Jumped +1 hour. Current TS: {new_ts}")
            st.rerun()
        if c2.button("Jump +1 Day"):
            new_ts = sim.jump_forward(86400)
            st.success(f"Jumped +1 day. Current TS: {new_ts}")
            st.rerun()
        if c3.button("Jump +30 Days"):
            new_ts = sim.jump_forward(30 * 86400)
            st.success(f"Jumped +30 days. Current TS: {new_ts}")
            st.rerun()

        # --- Base Snapshots ---
        st.markdown("#### 📸 State Snapshots")
        st.info("Save current state to jump back after testing strategies.")
        
        col_snap1, col_snap2 = st.columns(2)
        if col_snap1.button("📸 Create Snapshot"):
            snap_id = sim.create_snapshot()
            st.session_state['last_snapshot'] = snap_id
            st.success(f"Snapshot #{snap_id} created.")
        
        last_snap = st.session_state.get('last_snapshot')
        if last_snap:
            if col_snap2.button(f"🔙 Revert to #{last_snap}"):
                sim.revert_to_snapshot(last_snap)
                st.success(f"Reverted to #{last_snap}!")
                st.rerun()

        # --- Security Testing ---
        st.markdown("#### 🚨 Security & Risk Simulation")
        st.info("Test the 'Panic Button' and emergency logic.")
        
        if st.button("🔥 Trigger Emergency Withdrawal", help="Pulls all funds out and pauses strategy"):
            # Load Hub
            addr_file = "contracts/deployed_address.txt"
            if os.path.exists(addr_file):
                with open(addr_file, "r") as f:
                    strategy_addr = f.read().strip()
                
                with open("contracts/out/StrategyHub.sol/StrategyHub.json", "r") as f:
                    hub_abi = json.load(f)["abi"]
                
                contract = w3.eth.contract(address=strategy_addr, abi=hub_abi)
                acct = w3.eth.account.from_key("0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
                
                tx = contract.functions.emergencyWithdrawAll().transact({'from': acct.address})
                st.error(f"EMERGENCY TRIGGERED. TX: {tx.hex()}")
                time.sleep(2)
                st.rerun()
                
    else:
        st.warning("Simulation sandbox requires Local Fork to be online.")



