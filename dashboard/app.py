import streamlit as st
import requests
import pandas as pd
import time
import asyncio
# Import the client directly for the dashboard to browse pools (in a real app, the API would serve this)
from src.data.defillama_client import DefiLlamaClient

st.set_page_config(page_title="AI Yield Brain - Live Status", layout="wide")

st.title("🧠 AI Yield Rebalancer: Live Brain Status")

# --- Sidebar ---
st.sidebar.header("System Status")
try:
    health = requests.get("http://localhost:8000/health", timeout=2).json()
    st.sidebar.success(f"Brain Online (v{health['version']})")
except:
    st.sidebar.error("Brain Offline (Is server.py running?)")

# --- Test Portfolio Section ---
st.markdown("### 💼 Active Test Portfolio")

@st.cache_data(ttl=3600)
def fetch_valid_pools():
    """Fetch real pools from Ethereum/Base with >$1M TVL"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = DefiLlamaClient()
    all_pools = loop.run_until_complete(client.fetch_all_yields())
    
    # Filter for Stablecoins on ETH/Base
    valid_chains = {'Ethereum', 'Base'}
    stable_tokens = {'USDC', 'USDT', 'DAI', 'USDe', 'LUSD', 'crvUSD', 'GHO'}
    
    filtered = []
    for p in all_pools:
        if p['chain'] in valid_chains and p['tvlUsd'] > 1_000_000:
            # Check if symbol contains a stablecoin
            if any(s in p['symbol'] for s in stable_tokens):
                filtered.append({
                    "name": f"{p['symbol']} ({p['project']}) - {p['chain']}",
                    "id": p['pool'],
                    "apy": p['apy'],
                    "tvl": p['tvlUsd']
                })
    
    # Sort by APY descending
    return sorted(filtered, key=lambda x: x['apy'], reverse=True)

try:
    with st.spinner("Fetching active pools from Ethereum & Base..."):
        valid_pools = fetch_valid_pools()
        
    if not valid_pools:
        st.warning("⚠️ No valid stablecoin pools found on Ethereum/Base with >$1M TVL.")
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

# Helper for Async Execution in Streamlit
def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

# Fetch Live Data for Selection immediately to show context
async def get_live_context():
    client = DefiLlamaClient()
    return await client.fetch_pool_yields([current_asset_id, target_asset_id])

context_pools = run_async(get_live_context())

# Display Context
if context_pools:
    c_pool = next((p for p in context_pools if p['pool'] == current_asset_id), None)
    t_pool = next((p for p in context_pools if p['pool'] == target_asset_id), None)
    
    if c_pool and t_pool:
        st.info(f"📊 **Context:** You hold **${capital_input:,.0f}** in {current_asset_name} earning **{c_pool['apy']:.2f}%**. The opportunity is {target_asset_name} at **{t_pool['apy']:.2f}%**.")


# Helper: Fetch History
@st.cache_data(ttl=3600)
def get_apy_history(pool_id):
    """Fetch 30-day APY history"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = DefiLlamaClient()
    try:
        data = loop.run_until_complete(client.fetch_historical_yield(pool_id))
        # API returns: {'data': [{'timestamp': '...', 'tvlUsd': ..., 'apy': ...}]}
        # or list of dicts directly depending on endpoint wrapper. Client wrapper returns list.
        if isinstance(data, dict) and 'data' in data:
            data = data['data']
        return data[-30:] # Last 30 points (usually daily)
    except Exception as e:
        return []

# --- Tabs Layout ---
tab1, tab2, tab3 = st.tabs(["⚡ Live Decision", "📈 Deep Dive Analytics", "🍰 Portfolio Composition"])

with tab1:
    st.markdown("### 🤖 AI Brain Decision")
    
    if st.button("⚡ Ask Brain for Decision", type="primary"):
        with st.spinner("Analyzing market data, risk models, and gas costs..."):
            # Payload must use IDs as keys for allocations
            allocations_payload = {
                current_asset_id: float(capital_input),
                target_asset_id: 0.0
            }
            
            payload = {
                "portfolio_id": "dashboard_live_test",
                "current_allocations": allocations_payload
            }
            
            try:
                response = requests.post("http://localhost:8000/inference/predict", json=payload)
                if response.status_code != 200:
                    st.error(f"API Error {response.status_code}: {response.text}")
                else:
                    result = response.json()
                    
                    # Display Result
                    c1, c2, c3 = st.columns(3)
                    
                    # Color code action
                    action_color = "green" if result['action'] == "REBALANCE" else "red"
                    c1.markdown(f"#### Action: :{action_color}[{result['action']}]")
                    
                    c2.metric("Confidence Score", f"{result['confidence']*100:.0f}%")
                    c3.metric("Net APY Gain", f"{result['net_apy_gain']*100:.2f}%")
                    
                    st.info(f"**Reasoning:** {result['reason']}")
                    
                    if 'metrics' in result and result['metrics']:
                        st.markdown("### 💸 Cost Breakdown")
                        m = result['metrics']
                        
                        # Visual Breakdown
                        mc1, mc2, mc3 = st.columns(3)
                        mc1.metric("⛽ Gas Cost", f"${m.get('gas_exit',0) + m.get('gas_bridge',0) + m.get('gas_enter',0):.2f}")
                        mc2.metric("🔄 Swap Fees", f"${m.get('swap_fees',0):.2f}")
                        mc3.metric("📉 Total Loss", f"${m.get('total_conversion_loss',0):.2f}", help="Gas + Swap Fees")
                        
                        roi = m.get('roi_days', 999)
                        if roi < 365:
                            st.success(f"📅 **ROI Period:** Breakeven in **{roi:.1f} days**.")
                        else:
                            st.warning(f"📅 **ROI Period:** >1 Year ({roi:.1f} days). Not recommended.")

                    with st.expander("Raw API Response"):
                        st.json(result)
                    
                    # Save result to session state for other tabs
                    st.session_state['last_decision'] = result
                    
            except Exception as e:
                st.error(f"Error communicating with Brain: {e}")

with tab2:
    st.markdown("### 📈 Historical Yield Analysis (30 Days)")
    
    if current_asset_id and target_asset_id:
        with st.spinner("Fetching historical data..."):
            hist_current = get_apy_history(current_asset_id)
            hist_target = get_apy_history(target_asset_id)
            
            if hist_current and hist_target:
                # Process Data
                df_c = pd.DataFrame(hist_current)
                df_c['date'] = pd.to_datetime(df_c['timestamp'])
                df_c = df_c.set_index('date').sort_index()
                
                df_t = pd.DataFrame(hist_target)
                df_t['date'] = pd.to_datetime(df_t['timestamp'])
                df_t = df_t.set_index('date').sort_index()
                
                # Combine
                combined = pd.DataFrame({
                    "Current Asset (APY%)": df_c['apy'],
                    "Target Asset (APY%)": df_t['apy']
                })
                
                st.line_chart(combined)
                
                # Volatility/Risk Scorecard
                st.markdown("### 🛡️ Risk Scorecard")
                rc1, rc2 = st.columns(2)
                
                vol_c = df_c['apy'].std()
                vol_t = df_t['apy'].std()
                
                rc1.metric(f" volatility ({current_asset_name})", f"{vol_c:.2f}%", help="Standard Deviation of APY")
                rc2.metric(f" volatility ({target_asset_name})", f"{vol_t:.2f}%", delta=f"{vol_t-vol_c:.2f}%", delta_color="inverse")
            else:
                st.warning("Historical data not available for one or both assets.")

with tab3:
    st.markdown("### 🍰 Portfolio Allocations")
    
    # Current Allocation
    labels = [current_asset_name, "Cash/Other"]
    values = [capital_input, 0]
    
    # If we have a proposed allocation from the last decision
    if 'last_decision' in st.session_state:
        decision = st.session_state['last_decision']
        if decision['action'] == "REBALANCE":
            st.success("✅ **Proposed Rebalancing Active**")
            target_labels = [target_asset_name]
            target_values = [capital_input]
            
            # Simple bar chart comparison since Streamlit native charts are limited for Pies
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

# --- Market Data Section ---
st.markdown("---")
st.markdown("### 🌍 Real-Time Market Data (DeFi Llama)")
# ... (Limit table refresh to manual or separate call) ...
async def get_market_data():
    client = DefiLlamaClient()
    pools = await client.fetch_pool_yields([current_asset_id, target_asset_id])
    return pools

try:
    pools_data = run_async(get_market_data())
    df = pd.DataFrame(pools_data)
    if not df.empty:
        display_df = df[['symbol', 'project', 'chain', 'apy', 'tvlUsd']].copy()
        display_df['apy'] = display_df['apy'].apply(lambda x: f"{x:.2f}%")
        display_df['tvlUsd'] = display_df['tvlUsd'].apply(lambda x: f"${x:,.0f}")
        st.table(display_df)
except Exception:
    pass
