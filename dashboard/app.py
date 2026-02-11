import streamlit as st
import requests
import pandas as pd
import time
import asyncio
# Import the client directly for the dashboard to browse pools (in a real app, the API would serve this)
from src.data.defillama_client import DefiLlamaClient
from src.backtest.engine import BacktestEngine
from src.data.timeseries_db import TimeseriesDB
from src.optimizer.trade_sizer import TradeSizer
from src.backtest.prediction_tracker import PredictionTracker

st.set_page_config(page_title="AI Yield Brain - Live Status", layout="wide")

st.title(" AI Yield Rebalancer: Live Brain Status")

# --- Sidebar ---
st.sidebar.header("System Status")
try:
    health = requests.get("http://localhost:8000/health", timeout=2).json()
    st.sidebar.success(f"Brain Online (v{health['version']})")
except:
    st.sidebar.error("Brain Offline (Is server.py running?)")

# --- Test Portfolio Section ---
st.markdown("###  Active Test Portfolio")

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
        st.info(f" **Context:** You hold **${capital_input:,.0f}** in {current_asset_name} earning **{c_pool['apy']:.2f}%**. The opportunity is {target_asset_name} at **{t_pool['apy']:.2f}%**.")


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

# --- Sidebar Scanner ---
with st.sidebar:
    st.markdown("###  Market Scanner")
    st.markdown("Top Stablecoin Yields (ETH/Base)")
    
    # Async Fetch for Sidebar
    async def get_top_opps():
        client = DefiLlamaClient()
        return await client.fetch_top_pools(limit=5)
    
    try:
        top_pools = run_async(get_top_opps())
        if top_pools:
            for p in top_pools:
                st.markdown(f"**{p['symbol']}** ({p['project'].title()})")
                st.caption(f"**{p['apy']:.2f}%** | TVL: ${p['tvlUsd']/1e6:.1f}M")
                st.divider()
        else:
            st.info("Scanning...")
    except Exception:
        st.caption("Scanner offline")

# --- Tabs Layout ---
tab1, tab2, tab3, tab4 = st.tabs([" Live Decision", " Deep Dive Analytics", " Portfolio Composition", " Backtest"])

with tab1:
    st.markdown("### AI Brain Decision")
    
    if st.button(" Ask Brain for Decision", type="primary"):
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
                        mc3.metric(" Total Loss", f"${m.get('total_conversion_loss',0):.2f}", help="Gas + Swap Fees")
                        
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
    st.markdown("###  Historical Yield Analysis (30 Days)")
    
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

with tab3:
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

with tab4:
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
    st.caption("Tracks every Brain decision and validates against actual outcomes.")
    
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

# --- Market Data Section ---
st.markdown("---")
st.markdown("### Real-Time Market Data (DeFi Llama)")
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
