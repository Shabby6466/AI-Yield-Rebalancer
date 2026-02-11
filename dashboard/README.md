# DeFi Yield Rebalancing Dashboard

Interactive web dashboard for monitoring and analyzing the AI-Driven DeFi Yield Rebalancing System.

## Features

### 🏠 Overview
- Real-time system status
- Key performance metrics (Return, Sharpe, Win Rate)
- Strategy comparison charts
- Quick stats and achievements

###  Performance Analytics
- Detailed strategy comparisons
- Risk-adjusted metrics (Sharpe ratio, Max drawdown)
- Efficiency metrics (ROI, Cost ratio)
- Win rate analysis

### 💰 Portfolio State
- Current portfolio value and allocation
- Asset distribution visualization
- Portfolio growth over time
- Profit/loss tracking

###  Market Data
- Latest market updates (APY, TVL)
- Protocol distribution analysis
- Top performing assets
- APY trends over time
- Asset-specific analytics

### ⚙️ System Health
- Database status and connectivity
- Data freshness monitoring
- Component health checks
- System metrics and uptime

### 🎯 Backtest Results
- Comprehensive backtest analysis
- Downloadable results
- Comparative performance charts
- Cost vs performance analysis

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Or install in your existing environment
cd /home/faizan/work/Defi-Yield-R&D
pip install streamlit plotly
```

## Usage

### Start the Dashboard

```bash
# From project root
streamlit run dashboard/app.py

# Or with custom port
streamlit run dashboard/app.py --server.port 8501
```

The dashboard will open automatically in your browser at `http://localhost:8501`

### Configuration

Edit `.streamlit/config.toml` (auto-created) to customize:
- Theme colors
- Port number
- Browser behavior
- Cache settings

## Architecture

```
dashboard/
├── app.py                 # Main dashboard application
├── requirements.txt       # Dependencies
└── README.md             # This file

Data Sources:
├── data/defi_yields.db   # Market data database
└── backtest_results.csv  # Latest backtest results
```

## Dashboard Pages

### Navigation
Use the sidebar to switch between different views:
1. Overview - High-level system status
2. Performance Analytics - Detailed metrics
3. Portfolio State - Current holdings
4. Market Data - Real-time yields
5. System Health - Monitoring
6. Backtest Results - Historical analysis

### Auto-Refresh
- Data is cached for 60 seconds (market data)
- Backtest results cached for 5 minutes
- Manual refresh: Press 'R' or use browser refresh

## Data Requirements

The dashboard requires:
- `data/defi_yields.db` - SQLite database with yields table
- `backtest_results.csv` - Latest backtest output

If files are missing, relevant sections will show warnings.

## Troubleshooting

### Port Already in Use
```bash
streamlit run dashboard/app.py --server.port 8502
```

### Database Connection Issues
Verify database exists:
```bash
ls -lh data/defi_yields.db
python scripts/check_data.py
```

### Missing Dependencies
```bash
pip install --upgrade streamlit plotly pandas numpy
```

### Cache Issues
Clear Streamlit cache:
- Press 'C' in the dashboard
- Or delete `.streamlit/cache/`

## Performance Tips

1. **Limit Data Range**: For large datasets, use date filters
2. **Reduce Refresh Rate**: Increase cache TTL in code
3. **Close Unused Tabs**: Each browser tab runs independently
4. **Use Filtering**: Filter data before visualization

## Development

### Adding New Visualizations

```python
def show_new_page():
    st.header("New Page")
    # Your visualization code
    
# Add to navigation in main()
page = st.sidebar.radio("Select View", [
    "🏠 Overview",
    # ... existing pages ...
    "🆕 New Page"
])

if page == "🆕 New Page":
    show_new_page()
```

### Custom Metrics

```python
@st.cache_data(ttl=60)
def load_custom_metric():
    # Your data loading logic
    return metric_value
```

## Production Deployment

For production use:

```bash
# Run with authentication
streamlit run dashboard/app.py \
  --server.port 80 \
  --server.address 0.0.0.0 \
  --server.enableCORS false

# Or use reverse proxy (nginx/Apache)
# See: https://docs.streamlit.io/knowledge-base/deploy
```

## Security Notes

- Dashboard runs locally by default
- No external data transmission
- Database read-only access
- Use authentication for production deployment

## Support

For issues or questions:
1. Check Streamlit docs: https://docs.streamlit.io
2. Review error messages in terminal
3. Check browser console for JS errors

## Next Steps

1. ✅ Start dashboard: `streamlit run dashboard/app.py`
2.  Monitor real-time data collection
3. 🔍 Analyze backtest results
4. 🎯 Review before testnet deployment
