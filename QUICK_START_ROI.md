# Quick Start: ROI Tracking System

## 📋 What You Get

A complete Net ROI calculation and display system that:
- Tracks profit/loss on every rebalance
- Updates metrics in real-time
- Shows ROI % and APY on the dashboard sidebar
- Displays gas costs and yield earned
- Provides 30-day performance summary

---

## 🚀 Quick Setup (5 minutes)

### Step 1: Create Database Tables
```bash
# Apply schema changes to create ROI tables
psql -d yield_rebalancer -U postgres -f db/schema.sql
```

### Step 2: Test Everything Works
```bash
# Run the test suite
python scripts/test_roi_system.py

# Expected output:
# ✅ ROI SYSTEM TEST COMPLETE
# All core ROI functions are operational
```

### Step 3: Run Dashboard
```bash
# Start the dashboard
streamlit run dashboard/app.py

# Look in the sidebar for the new "📈 NET ROI PERFORMANCE" section
```

### Step 4: Run Keeper
```bash
# Start the keeper service with ROI tracking enabled
python src/execution/keeper_service.py --network base_sepolia

# After first rebalance, logs will show:
# ✅ ROI tracking started: ID 1
```

---

## 📊 Dashboard Display

The sidebar now shows:

```
┌──────────────────────────────────┐
│ 📈 NET ROI PERFORMANCE           │
├──────────────────────────────────┤
│                                  │
│  Current Rebalance:              │
│  ROI %:  1.23%  △ 450% APY       │
│  P&L:    $800                    │
│                                  │
│  [ROI Breakdown ▼]               │
│  • Yield Earned: $500            │
│  • Gas Cost: $50                 │
│  • Entry Value: $100,000         │
│  • Snapshot: 2026-02-16 12:34    │
│  • Allocation: Aave 70%, ...     │
│                                  │
│  30-Day Summary:                 │
│  • Total Rebalances: 12          │
│  • Cumulative ROI: 2.50%         │
│  • Total Gain: $2,500            │
│  • Total Gas: $125               │
│                                  │
└──────────────────────────────────┘
```

---

## 🔧 How ROI Is Calculated

### When Rebalance Happens:
```python
# Entry point recorded
roi_id = roi_calculator.record_rebalance_entry(
    tx_hash="0x...",
    entry_portfolio_value_usd=100000,
    gas_cost_usd=50
)
# Stored in: rebalance_roi table
```

### Every 5 Minutes (Manual or Scheduled):
```python
# Update with new portfolio value & yields
snapshot = roi_calculator.calculate_roi_snapshot(
    rebalance_roi_id=roi_id,
    current_portfolio_value_usd=101500,
    yield_earned_usd=1500
)
# Returns: { roi_percent: 1.5, apy_achieved: 547.5, ... }
# Stored in: roi_snapshots table
```

### For Dashboard Display:
```python
# Get latest snapshot (auto-refreshes every 5 sec)
latest = roi_calculator.get_latest_roi_snapshot()
# Returns latest ROI % and APY
```

### Daily at Midnight:
```python
# Aggregate daily stats
roi_calculator.update_daily_roi_summary()
# Calculates best/worst/avg ROI for the day
# Stored in: roi_summary table
```

---

## 📐 Formulas

**ROI % = (Gain ÷ Entry Value) × 100**
```
Example: ($800 ÷ $100,000) × 100 = 0.8%
```

**APY % = (ROI% ÷ Days Held) × 365**
```
Example: (0.8% ÷ 1 day) × 365 = 292% annualized
```

**Total Gain = Yield + Realized Gains + Unrealized Gains**
```
Example: $500 + $0 + $0 = $500
```

**Net Gain = Total Gain - Gas Cost**
```
Example: $500 - $50 = $450 net
```

---

## 📁 Files Overview

| File | Purpose |
|------|---------|
| `src/execution/roi_calculator.py` | Core ROI calculation engine |
| `db/schema.sql` | Database tables (rebalance_roi, roi_snapshots, roi_summary) |
| `src/execution/keeper_service.py` | Integrated to record ROI on rebalances |
| `dashboard/app.py` | Sidebar display of ROI metrics |
| `scripts/test_roi_system.py` | Test suite to verify everything works |
| `docs/ROI_TRACKING_GUIDE.md` | Complete technical documentation |
| `docs/ROI_DATA_FLOW.py` | Data flow diagrams & architecture |

---

## 🧪 Testing Checklist

- [ ] Schema created: `psql -d yield_rebalancer -c "\\dt rebalance_roi"`
- [ ] Test passes: `python scripts/test_roi_system.py`
- [ ] Dashboard shows ROI: `streamlit run dashboard/app.py`
- [ ] Keeper logs ROI ID: `python src/execution/keeper_service.py --once`
- [ ] Database has data: `psql -d yield_rebalancer -c "SELECT * FROM rebalance_roi"`

---

## 🔍 Monitoring the System

### Check Latest ROI in Database:
```sql
SELECT 
    roi_percent,
    apy_achieved_percent,
    total_gain_loss_usd,
    snapshot_date
FROM roi_snapshots
ORDER BY snapshot_date DESC
LIMIT 5;
```

### Check Daily Summary:
```sql
SELECT 
    summary_date,
    total_rebalances_count,
    cumulative_roi_percent,
    best_rebalance_roi_percent
FROM roi_summary
ORDER BY summary_date DESC
LIMIT 30;
```

### Python API Usage:
```python
from src.execution.roi_calculator import ROICalculator

calc = ROICalculator()

# Get latest ROI
latest = calc.get_latest_roi_snapshot()
print(f"Current ROI: {latest['roi_percent']}%")

# Get 30-day summary
cum = calc.get_cumulative_roi()
print(f"30-Day Gain: ${cum['cumulative_gain_usd']}")

calc.close()
```

---

## ⚠️ Troubleshooting

**Problem:** "No rebalance_roi table"
```bash
# Solution: Run schema creation
psql -d yield_rebalancer -f db/schema.sql
```

**Problem:** ROI shows $0 gain
```
# Solution: Yields need time to accrue in protocols
# Give it at least 5-10 minutes, then check again
```

**Problem:** Dashboard ROI section missing
```bash
# Solution: Restart Streamlit
# pkill -f streamlit
# streamlit run dashboard/app.py
```

**Problem:** Database connection error
```bash
# Solution: Check env variables
# export DB_HOST=localhost
# export DB_USER=postgres
# export DB_PASSWORD=postgres
```

---

## 🎯 Key Metrics to Watch

| Metric | Good | Warning | Critical |
|--------|------|---------|----------|
| ROI % | > 1% | 0-1% | < 0% |
| APY % | > 100% | 50-100% | < 50% |
| Gas Cost | < 1% of gains | 1-5% | > 10% |
| Rebalances/Day | > 2 | 1-2 | 0 (keeper down) |

---

## 🚨 Setting Up Alerts (Optional)

Create `src/execution/roi_alerts.py`:
```python
from src.execution.roi_calculator import ROICalculator
import smtplib

def check_roi_alerts():
    calc = ROICalculator()
    latest = calc.get_latest_roi_snapshot()
    
    if latest['roi_percent'] < -5:
        send_alert("⚠️ CRITICAL: ROI dropped below -5%")
    elif latest['roi_percent'] < 0:
        send_alert(f"⚠️ WARNING: ROI is negative at {latest['roi_percent']}%")
    
    calc.close()

def send_alert(message):
    # Send email or Slack notification
    print(f"ALERT: {message}")
```

---

## 📈 Performance Optimization

For large datasets (> 1M rows):

```sql
-- Add indexes for faster queries
CREATE INDEX idx_roi_snapshots_date 
    ON roi_snapshots(snapshot_date DESC);

CREATE INDEX idx_roi_snapshots_roi_id 
    ON roi_snapshots(rebalance_roi_id, snapshot_date DESC);

-- Archive old data (> 90 days)
INSERT INTO roi_snapshots_archive
SELECT * FROM roi_snapshots
WHERE snapshot_date < NOW() - INTERVAL '90 days';

DELETE FROM roi_snapshots
WHERE snapshot_date < NOW() - INTERVAL '90 days';
```

---

## ❓ FAQ

**Q: How often is ROI updated?**
A: Dashboard refreshes every 5 seconds. Snapshots should be calculated every 5 minutes via scheduler.

**Q: Does ROI include gas fees?**
A: Yes, net cost (gas + slippage) is subtracted from gains.

**Q: Why is APY so high?**
A: APY is annualized based on gains in first 24 hours. It normalizes over time.

**Q: Can I see historical ROI?**
A: Yes, query `roi_snapshots` table or `roi_summary` for aggregated daily data.

**Q: How do I export ROI data?**
A: Use PostgreSQL export: `psql -d yield_rebalancer -c "COPY roi_summary TO STDOUT" > roi.csv`

---

## 📞 Support

For issues or questions:
1. Check `ROI_TRACKING_GUIDE.md` for detailed reference
2. Review `ROI_DATA_FLOW.py` for architecture diagrams
3. Run `test_roi_system.py` to verify system health
4. Check database directly for troubleshooting

---

**Ready to go! 🎉**

Your AI-Yield-Rebalancer now tracks and displays Net ROI in the dashboard sidebar.
