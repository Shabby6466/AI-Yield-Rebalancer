#!/bin/bash
# Start the DeFi Yield Rebalancing Dashboard

echo "Starting DeFi Yield Rebalancing Dashboard..."
echo ""

cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d "venv/bin" ]; then
    source venv/bin/activate
fi

# Start dashboard
python -m streamlit run dashboard/app.py \
    --server.headless=true \
    --server.port=8501 \
    --browser.gatherUsageStats=false

echo ""
echo "✅ Dashboard is running at: http://localhost:8501"
echo ""
echo "Press Ctrl+C to stop the dashboard"
