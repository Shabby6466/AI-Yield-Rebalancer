# DeFi Yield Data Ingestion System

A production-ready data collection system for DeFi yield optimization using DefiLlama API and PostgreSQL.

## 🏗️ Architecture

### Database Schema

**assets** - Static metadata for tracked DeFi assets
- `id`: Primary key
- `symbol`: Asset/pool symbol (USDC, 3pool, etc.)
- `address`: Smart contract address
- `decimals`: Token decimals
- `protocol`: Protocol name (aave_v3, curve, uniswap_v3)
- `chain`: Blockchain (ethereum)

**yield_metrics** - Time-series yield data
- `time`: Timestamp (indexed, part of unique constraint)
- `asset_id`: Foreign key to assets table
- `apy_percent`: Annual Percentage Yield (ML target variable)
- `tvl_usd`: Total Value Locked in USD
- `volume_24h_usd`: 24-hour trading volume
- `utilization_rate`: Lending protocol utilization
- `volatility_24h`: 24-hour price volatility
- `block_number`: Ethereum block number

## 📁 Project Structure

```
src/ingestion/
├── __init__.py
├── backfill_client.py    # Historical data from DefiLlama
├── live_collector.py     # Real-time data collection
├── rpc_collectors.py     # Web3 RPC collectors (alternative)
└── scheduler.py          # Automated collection scheduler

scripts/
├── init_database.sql     # Database schema initialization
```

## Quick Start

### 1. Initialize Database

```bash
# Set up the schema and seed assets
PGPASSWORD=postgres psql -h localhost -U postgres -d defi_yield_db -f scripts/init_database.sql
```

### 2. Backfill Historical Data

```bash
# Fetches 1+ year of daily data from DefiLlama
python src/ingestion/backfill_client.py
```

**Expected Output:**
- Aave V3: ~1,100 records per asset (3 years of data)
- Curve: ~770-1,450 records per pool
- Uniswap V3: ~1,360-1,410 records per pool (4 years)

### 3. Collect Live Data

```bash
# Fetches current APY and TVL for all tracked assets
python src/ingestion/live_collector.py
```

### 4. Run Automated Scheduler

```bash
# Collects live data every 15 minutes
python src/ingestion/scheduler.py
```

##  Data Coverage

### Aave V3 (5 assets)
- USDC: Stablecoin lending
- USDT: Stablecoin lending  
- DAI: Stablecoin lending
- WETH: ETH lending
- WBTC: BTC lending

### Curve (2 pools)
- stETH: Liquid staking pool
- FRAX: Stablecoin pool

### Uniswap V3 (4 pools)
- USDC/WETH: High volume trading pair
- WBTC/WETH: BTC/ETH trading
- USDC/USDT: Stablecoin pair
- DAI/USDC: Stablecoin pair

**Total:** 11 assets tracked

## 🔧 Configuration

Environment variables (`.env`):
```bash
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=defi_yield_db
DB_USER=postgres
DB_PASSWORD=postgres

# Optional: For RPC-based collection (not actively used)
ALCHEMY_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
```

##  Data Quality

### Backfill (DefiLlama Historical)
- **Source:** DefiLlama yields.llama.fi API
- **Frequency:** Daily snapshots
- **History:** 1-4 years depending on protocol
- **Reliability:** ⭐⭐⭐⭐⭐ (Most reliable, free, no rate limits)
- **Coverage:**
  - Aave V3: ~1,100 records per asset (3 years)
  - Curve: ~770-1,450 records per pool (2-4 years)
  - Uniswap V3: ~1,360-1,410 records per pool (4 years)

### Live Collection (DefiLlama Current)
- **Source:** Same API, latest data point
- **Frequency:** Every 15 minutes
- **Latency:** ~1-5 minutes behind on-chain state
- **Reliability:** ⭐⭐⭐⭐⭐ (Production-ready)

### RPC Collection (Optional, Not Recommended)
- **Source:** Direct smart contract calls via Web3.py
- **Issues:** Complex ABIs, rate limits, requires API keys
- **Status:** Code provided but not actively used

## 🗄️ Database Queries

### Check data availability
```sql
SELECT 
    a.protocol, 
    a.symbol, 
    COUNT(*) as records,
    MIN(ym.time) as earliest,
    MAX(ym.time) as latest
FROM yield_metrics ym
JOIN assets a ON ym.asset_id = a.id
GROUP BY a.protocol, a.symbol
ORDER BY a.protocol, a.symbol;
```

### Latest yields
```sql
SELECT * FROM latest_yields;
```

### Get time-series for ML training
```sql
SELECT 
    ym.time,
    a.symbol,
    a.protocol,
    ym.apy_percent,
    ym.tvl_usd,
    ym.utilization_rate
FROM yield_metrics ym
JOIN assets a ON ym.asset_id = a.id
WHERE a.symbol = 'USDC' AND a.protocol = 'aave_v3'
ORDER BY ym.time DESC
LIMIT 100;
```

## 🎯 Design Decisions

### Why DefiLlama over direct RPC?

1. **Free & Unlimited:** No API keys, no rate limits
2. **Already Aggregated:** Historical data pre-computed
3. **Reliable:** Production-grade infrastructure
4. **Simple:** Clean JSON API vs complex smart contract ABIs
5. **Multi-Protocol:** Consistent format across Aave, Curve, Uniswap

### Why PostgreSQL over TimescaleDB?

TimescaleDB is **optional but recommended** for production:
- Install separately if needed
- Provides automatic time-based partitioning
- Optimizes queries on large time-series datasets
- Schema is designed to work with or without it

### Data Storage Strategy

- **Backfill:** Run once or weekly to refresh history
- **Live:** Continuous 15-minute updates for inference
- **Deduplication:** `ON CONFLICT (time, asset_id) DO NOTHING` prevents duplicates
- **Updates:** Live collector uses `DO UPDATE` to refresh latest values

## 📝 Next Steps

1. **Add More Assets:** Edit `POOL_MAPPINGS` in `live_collector.py`
2. **Find Pool IDs:** Use `backfill_client.find_pool_id(protocol, symbol)`
3. **Add to Database:** Insert new rows in `assets` table
4. **Backfill:** Run backfill client to fetch history
5. **Systemd Service:** Set up scheduler as system service for production

## 🐛 Troubleshooting

### "No data found for pool"
- Pool may not exist on DefiLlama
- Try searching: https://defillama.com/yields
- Use correct protocol slug ('aave-v3' not 'aave_v3')

### Database permission errors
```bash
PGPASSWORD=postgres psql -h localhost -U postgres -d defi_yield_db
```
Use `-h localhost` to force TCP connection with password auth

### Empty backfill results
Check if data already exists:
```sql
SELECT COUNT(*) FROM yield_metrics;
```
Backfill uses `ON CONFLICT DO NOTHING` so won't re-insert existing data

## 📚 References

- [DefiLlama Yields API](https://defillama.com/docs/api)
- [Aave V3 Docs](https://docs.aave.com/developers/v/3.0/)
- [Curve Finance](https://curve.fi/)
- [PostgreSQL Time-Series](https://www.timescale.com/)

## ✅ Validation

Current data status (as of latest test):
 Avg APY | Max APY |
|----------|-------|---------|------------|---------|---------|
| Aave V3  | USDC  | 1,103   | 2023-02-07 → 2026-02-10 | 5.01% | 56.68% |
| Aave V3  | USDT  | 1,096   | 2023-02-14 → 2026-02-10 | 4.76% | 68.37% |
| Aave V3  | DAI   | 1,103   | 2023-02-07 → 2026-02-10 | 4.68% | 59.33% |
| Aave V3  | WETH  | 1,103   | 2023-02-07 → 2026-02-10 | 1.87% | 5.84% |
| Aave V3  | WBTC  | 1,103   | 2023-02-07 → 2026-02-10 | 0.08% | 0.95% |
| Curve    | stETH | 1,458   | 2022-02-11 → 2026-02-10 | 2.99% | 14.87% |
| Curve    | FRAX  | 775     | 2024-01-03 → 2026-02-10 | 2.79% | 78.20% |
| Uniswap V3 | USDC/WETH | 1,390 | 2022-03-27 → 2026-02-10 | 31.48% | 471.84% |
| Uniswap V3 | WBTC/WETH | 1,410 | 2022-03-27 → 2026-02-10 | 8.93% | 121.00% |
| Uniswap V3 | USDC/USDT | 1,390 | 2022-03-27 → 2026-02-10 | 5.33% | 145.75% |
| Uniswap V3 | DAI/USDC  | 1,364 | 2022-03-27 → 2026-02-10 | 0.25% | 23.60% |

**Total: 13,297
**Total: 7,723 historical records** ✅
