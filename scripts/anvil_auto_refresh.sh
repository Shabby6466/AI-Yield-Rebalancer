#!/bin/bash
# Anvil Fork Auto-Refresh Script
# Monitors oracle lag and restarts Anvil when data becomes stale

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "🔄 Anvil Fork Auto-Refresh Monitor"
echo "=================================="
echo ""

# Check if running in Docker
if [ -f /.dockerenv ]; then
    echo "⚠️  Running inside Docker container - cannot restart Anvil from here"
    echo "   This script should be run on the host machine"
    exit 1
fi

# Function to check oracle lag from logs
check_oracle_lag() {
    # Get last 50 lines of rebalancer logs
    LOGS=$(docker compose logs --tail=50 rebalancer 2>/dev/null || echo "")
    
    # Extract lag seconds if present
    LAG=$(echo "$LOGS" | grep -oP 'Oracle Lag Detected \(\K[0-9]+' | tail -1)
    
    if [ -n "$LAG" ]; then
        LAG_HOURS=$(echo "scale=2; $LAG / 3600" | bc)
        echo "📊 Current Oracle Lag: ${LAG}s (${LAG_HOURS}h)"
        
        # If lag > 4 hours (14400s), restart Anvil
        if [ "$LAG" -gt 14400 ]; then
            echo "🚨 CRITICAL: Oracle lag exceeds 4 hours!"
            echo "   Restarting Anvil with fresh mainnet fork..."
            docker compose restart anvil
            echo "✅ Anvil restarted. Waiting 30s for sync..."
            sleep 30
            echo "✅ Fork refreshed. Rebalancer will resume on next cycle."
            return 0
        elif [ "$LAG" -gt 3600 ]; then
            echo "⚠️  WARNING: Oracle lag exceeds 1 hour"
            echo "   Consider restarting Anvil soon"
        fi
    else
        echo "✓ No oracle lag detected (data is fresh)"
    fi
    
    return 1
}

# Main monitoring loop
echo "Starting monitoring loop (checks every 5 minutes)..."
echo "Press Ctrl+C to stop"
echo ""

while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$TIMESTAMP] Checking oracle health..."
    
    if check_oracle_lag; then
        echo "[$TIMESTAMP] Fork refreshed successfully"
    fi
    
    echo ""
    echo "Next check in 5 minutes..."
    sleep 300
done
