"""
ROI Data Flow & Architecture Diagram

This document explains how ROI data flows through the system
and how it's displayed in the dashboard.
"""

# ==============================================================================
# SYSTEM DATA FLOW
# ==============================================================================

"""
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AI YIELD REBALANCER - ROI FLOW                      │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────┐
│  KEEPER SERVICE  │
│  (keeper_service │
│      .py)        │
└────────┬─────────┘
         │
         │ 1. Executes Rebalance
         │    - Gets current portfolio value
         │    - Sends transaction to blockchain
         │    - Receives gas cost & tx hash
         │
         ▼
┌──────────────────────────────────────────┐
│ ROI ENTRY POINT RECORDING                │
│ record_rebalance_entry()                 │
│                                          │
│ • tx_hash: 0x123abc...                  │
│ • entry_value: $100,000                 │
│ • allocation_from: {Aave: 50, ...}     │
│ • allocation_to: {Aave: 70, ...}       │
│ • gas_cost: $50                         │
│ • slippage: 0.1%                        │
│                                          │
│ → Stores in rebalance_roi table         │
│ → Returns ROI ID for tracking           │
└────────┬─────────────────────────────────┘
         │
         │ 2. Tracks Over Time
         │
         ▼
┌──────────────────────────────────────────┐
│ SCHEDULED ROI UPDATES (every 5 min)      │
│ calculate_roi_snapshot()                 │
│                                          │
│ • Queries protocol yields earned        │
│ • Calculates unrealized P&L             │
│ • Updates current portfolio value       │
│ • Calculates ROI % & APY                │
│                                          │
│ → Stores in roi_snapshots table         │
│ → Latest snapshot = dashboard data      │
└────────┬─────────────────────────────────┘
         │
         │ 3. Daily Aggregation
         │    (scheduled at midnight)
         │
         ▼
┌──────────────────────────────────────────┐
│ DAILY ROI SUMMARY                        │
│ update_daily_roi_summary()               │
│                                          │
│ • Count rebalances in day                │
│ • Sum yields earned                      │
│ • Calculate best/worst/avg ROI           │
│ • Aggregate gas costs                    │
│                                          │
│ → Stores in roi_summary table            │
└────────┬─────────────────────────────────┘
         │
         │ 4. Display in Dashboard
         │
         ▼
┌──────────────────────────────────────────┐
│ STREAMLIT DASHBOARD                      │
│ dashboard/app.py                         │
│                                          │
│ ┌─────────────────────────────────────┐ │
│ │ 📈 NET ROI PERFORMANCE              │ │
│ ├─────────────────────────────────────┤ │
│ │ ROI: 1.23% (APY: 450%)              │ │
│ │ P&L: $800 gain                      │ │
│ │                                     │ │
│ │ Yield Earned: $500                  │ │
│ │ Gas Cost: $50                       │ │
│ │ Entry Value: $100,000               │ │
│ │                                     │ │
│ │ 30-DAY SUMMARY:                     │ │
│ │ Rebalances: 12                      │ │
│ │ Cumulative ROI: 2.5%                │ │
│ │ Total Gain: $2,500                  │ │
│ └─────────────────────────────────────┘ │
│                                          │
│ Auto-refreshes every 5 seconds           │
│ (fetches latest snapshot from DB)        │
└──────────────────────────────────────────┘


DATABASE SCHEMA
═══════════════════════════════════════════════════════════════════════════════

rebalance_roi (Entry Point)
────────────────────────────────────────────────────────────────────────────
├─ id                          (PRIMARY KEY)
├─ tx_hash                     (Transaction ID)
├─ entry_portfolio_value_usd   ($100,000)
├─ entry_timestamp             (When rebalance happened)
├─ allocation_from             (Previous allocation)
├─ allocation_to               (New allocation)
├─ gas_cost_usd                ($50)
├─ slippage_percent            (0.1%)
└─ net_cost_usd                ($50.10)


roi_snapshots (Time Series)
────────────────────────────────────────────────────────────────────────────
├─ id                          (PRIMARY KEY)
├─ rebalance_roi_id            (FK to rebalance_roi)
├─ snapshot_date               (2026-02-16 12:30:00)
├─ current_portfolio_value_usd ($101,500)
├─ yield_earned_usd            ($1,500)
├─ realized_gains_usd          ($0)
├─ unrealized_gains_usd        ($0)
├─ total_gain_loss_usd         ($1,500)
├─ roi_percent                 (1.50%)
├─ apy_achieved_percent        (547.5%)
└─ status                      ('active')


roi_summary (Daily Aggregate)
────────────────────────────────────────────────────────────────────────────
├─ id                          (PRIMARY KEY)
├─ summary_date                (2026-02-16)
├─ total_rebalances_count      (12)
├─ cumulative_portfolio_gain_usd ($18,000)
├─ cumulative_roi_percent      (1.8%)
├─ net_yield_earned_usd        ($18,000)
├─ total_gas_cost_usd          ($600)
├─ best_rebalance_roi_percent  (2.5%)
├─ worst_rebalance_roi_percent (-0.5%)
└─ avg_rebalance_roi_percent   (1.5%)


CALCULATION FORMULAS
═══════════════════════════════════════════════════════════════════════════════

ROI Percentage:
───────────────
ROI% = (Total Gain/Loss ÷ Entry Portfolio Value) × 100
     = ($1,500 ÷ $100,000) × 100
     = 1.5%


APY (Annualized Percentage Yield):
─────────────────────────────────
Days Held = (Now - Entry Time) / 86400
APY% = (ROI% ÷ Days Held) × 365
     = (1.5% ÷ 1 day) × 365
     = 547.5%


Total P&L:
──────────
Total P&L = Yield Earned + Realized Gains + Unrealized Gains - Gas Cost
          = $1,500 + $0 + $0 - $50
          = $1,450 net gain


Slippage Impact:
────────────────
Slippage Cost = Entry Value × Slippage%
              = $100,000 × 0.1%
              = $100


Net Cost:
─────────
Net Cost = Gas Cost + Slippage Cost
         = $50 + $100
         = $150 total cost


INTEGRATION POINTS
═══════════════════════════════════════════════════════════════════════════════

1. KEEPER SERVICE (src/execution/keeper_service.py)
   ├─ After rebalance execution:
   │  └─ roi_calculator.record_rebalance_entry()
   └─ Logs ROI ID for tracking

2. SCHEDULER (suggested new component)
   ├─ Every 5 minutes:
   │  ├─ Fetch current portfolio value
   │  ├─ Query protocol yields
   │  └─ roi_calculator.calculate_roi_snapshot()
   └─ Updates roi_snapshots table

3. DAILY JOB (suggested new component)
   ├─ At midnight:
   │  └─ roi_calculator.update_daily_roi_summary()
   └─ Aggregates daily statistics

4. DASHBOARD (dashboard/app.py)
   ├─ On page load & every 5 seconds:
   │  ├─ roi_calculator.get_latest_roi_snapshot()
   │  ├─ roi_calculator.get_cumulative_roi()
   │  └─ Render metrics in sidebar
   └─ Auto-refresh enabled


DASHBOARD DISPLAY HIERARCHY
═══════════════════════════════════════════════════════════════════════════════

SIDEBAR
└─ System Status
├─ Agent Wallet
│  ├─ Total: $100,000
│  ├─ Gas Tank: 0.5 ETH
│  └─ Asset Breakdown
│
├─ 📈 NET ROI PERFORMANCE  ← NEW SECTION
│  ├─ Current Rebalance
│  │  ├─ ROI %: 1.23%
│  │  └─ P&L: $800
│  ├─ ROI Breakdown (Expandable)
│  │  ├─ Yield Earned: $500
│  │  ├─ Gas Cost: $50
│  │  ├─ Entry Value: $100,000
│  │  ├─ Snapshot Time: 2026-02-16 12:30
│  │  └─ Current Allocation: Aave 70%, Compound 30%
│  └─ 30-Day Summary
│     ├─ Total Rebalances: 12
│     ├─ Cumulative ROI: 2.50%
│     ├─ Total Gain: $2,500
│     └─ Total Gas Cost: $125
│
├─ Agent Controls
│  ├─ Active Strategy: USDC/USDT
│  └─ SMART Status: 🟢 HEALTHY
│
└─ Risk Parameters (Expandable)


API ENDPOINTS NEEDED (Optional REST API)
═══════════════════════════════════════════════════════════════════════════════

GET /api/roi/latest
──────────────────
Returns: Latest ROI snapshot
{
  "roi_percent": 1.23,
  "apy_achieved_percent": 450.0,
  "total_gain_loss_usd": 800.0,
  "yield_earned_usd": 500.0,
  "gas_cost_usd": 50.0,
  "entry_portfolio_value_usd": 100000.0,
  "snapshot_date": "2026-02-16T12:30:00Z",
  "allocation": {"Aave": 70, "Compound": 30}
}


GET /api/roi/cumulative?days=30
────────────────────────────────
Returns: 30-day cumulative stats
{
  "total_rebalances": 12,
  "cumulative_roi_percent": 2.50,
  "best_roi_percent": 3.5,
  "worst_roi_percent": -0.5,
  "cumulative_gain_usd": 2500.0,
  "total_yield_earned_usd": 2500.0,
  "total_gas_cost_usd": 125.0,
  "avg_slippage_percent": 0.12
}


POST /api/roi/record
───────────────────
Records a new rebalance entry
{
  "rebalance_id": 1,
  "tx_hash": "0x123...",
  "entry_portfolio_value_usd": 100000.0,
  "allocation_from": {"Aave": 50, "Compound": 50},
  "allocation_to": {"Aave": 70, "Compound": 30},
  "gas_cost_usd": 50.0,
  "slippage_percent": 0.1
}
Returns: { "roi_id": 123 }


TESTING WORKFLOW
═══════════════════════════════════════════════════════════════════════════════

1. Run test script:
   python scripts/test_roi_system.py

2. Verify database tables created:
   psql -d yield_rebalancer -c "\\dt"
   
3. Check sample data:
   SELECT * FROM rebalance_roi ORDER BY id DESC LIMIT 5;
   SELECT * FROM roi_snapshots ORDER BY snapshot_date DESC LIMIT 5;
   SELECT * FROM roi_summary ORDER BY summary_date DESC LIMIT 5;

4. Test dashboard display:
   streamlit run dashboard/app.py
   → Check sidebar for "📈 NET ROI PERFORMANCE" section

5. Integration test with keeper:
   python src/execution/keeper_service.py --network base_sepolia --once
   → Should log ROI ID after successful rebalance


MONITORING & ALERTING
═══════════════════════════════════════════════════════════════════════════════

Critical Metrics to Monitor:

1. ROI Threshold Alerts
   ├─ CRITICAL: ROI < -5% (Stop-loss)
   ├─ WARNING: ROI < 0% (Negative returns)
   └─ INFO: ROI > 2% (Strong performance)

2. APY Threshold Alerts
   ├─ CRITICAL: APY < expected baseline
   └─ INFO: APY > 100% (Excellent)

3. Gas Cost Alerts
   ├─ WARNING: Gas cost > 2% of portfolio value
   └─ INFO: Gas cost > 10% of gains

4. Rebalance Frequency Alerts
   ├─ WARNING: < 2 rebalances per day (Keeper may be down)
   └─ INFO: > 10 rebalances per day (High activity)

5. Data Quality Alerts
   ├─ WARNING: No ROI snapshot for 1 hour
   └─ ERROR: No ROI data for 24 hours


PERFORMANCE OPTIMIZATION
═══════════════════════════════════════════════════════════════════════════════

Database Queries:
├─ Index on roi_snapshots(rebalance_roi_id, snapshot_date DESC)
├─ Index on roi_summary(summary_date DESC)
└─ Partition roi_snapshots by month if > 1M rows

Caching:
├─ Cache latest ROI snapshot (refresh every 5 min)
├─ Cache cumulative ROI (refresh every hour)
└─ Use Redis for real-time metrics

Batch Updates:
├─ Calculate ROI snapshots in batches (every 5 min)
├─ Update daily summaries in single transaction
└─ Archive old snapshots (> 90 days) to archive table


DEPLOYMENT CHECKLIST
═══════════════════════════════════════════════════════════════════════════════

□ Run schema.sql to create tables
□ Test roi_calculator.py with test_roi_system.py
□ Integrate into keeper_service.py ✓ (done)
□ Add ROI display to dashboard ✓ (done)
□ Set up 5-minute scheduler for ROI snapshots
□ Set up daily job for ROI summaries
□ Configure email/Slack alerts for thresholds
□ Document ROI metrics in README
□ Set up monitoring dashboard (Grafana)
□ Train ops team on ROI interpretation
□ Set up backup/restore procedures
□ Performance test with 1M rows

"""

print(__doc__)
