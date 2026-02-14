# Anvil Fork Auto-Refresh

## Problem
When running Anvil in fork mode, the forked state becomes stale over time. Chainlink oracle prices, on-chain TVL data, and other critical metrics can be hours old, leading to incorrect portfolio valuations and dangerous rebalancing decisions.

## Solution
The rebalancer now implements **escalating oracle lag warnings**:

### Thresholds
- **1 minute (60s)**: Warning logged, continues in local mode
- **1 hour (3600s)**: Critical warning, portfolio valuation may be ±2% off per hour
- **4 hours (14400s)**: **HARD HALT** - System stops even in local mode

### Auto-Refresh Script
Run this on your server to automatically restart Anvil when data gets stale:

```bash
./scripts/anvil_auto_refresh.sh
```

This script:
1. Monitors rebalancer logs every 5 minutes
2. Checks oracle lag timestamps
3. Automatically runs `docker compose restart anvil` when lag > 4 hours
4. Waits for fork to sync before resuming

### Manual Refresh
If you see the critical halt message, manually refresh:

```bash
docker compose restart anvil
```

Wait 30 seconds for the fork to sync, then the rebalancer will resume automatically.

## Why This Matters
With a **$6M+ portfolio**, even a 2% pricing error = **$120k** miscalculation. Stale data can cause:
- Incorrect portfolio valuations
- Wrong rebalancing decisions
- Missed opportunities or catastrophic moves

The 4-hour hard halt ensures the bot **never operates on dangerously old data**, even in simulation mode.
