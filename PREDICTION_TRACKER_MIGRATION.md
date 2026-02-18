# PredictionTracker Migration: SQLite → PostgreSQL/TimescaleDB

## Completion Status: ✅ COMPLETE

### Overview
The `PredictionTracker` class has been fully refactored from SQLite-based persistence to PostgreSQL/TimescaleDB with connection pooling, enabling production-grade prediction validation and accuracy tracking.

---

## Key Architectural Changes

### 1. Database Backend
| Aspect | Before (SQLite) | After (PostgreSQL) |
|--------|-----------------|-------------------|
| Connection Model | File-based, sync | Connection pool with psycopg2 |
| Timestamp Type | TEXT (ISO format) | TIMESTAMPTZ (server-managed UTC) |
| Financial Precision | REAL (32-bit float) | DOUBLE PRECISION (64-bit float) |
| JSON Storage | TEXT + json.loads() | JSONB (native, fully queryable) |

### 2. Class Constructor
**Before:**
```python
def __init__(self, db_path: str = "predictions.db", validation_days: int = 1)
```

**After:**
```python
def __init__(self, conn_pool: psycopg2.pool.SimpleConnectionPool, validation_days: int = 1)
```

Enables shared connection pooling in FastAPI startup events without connection leaks.

### 3. Financial Calculations
All monetary values now use `DOUBLE PRECISION` instead of REAL:
- **Pro-rated APY**: `gain = capital * (apy / 100) * (days / 365)`
- **Cost-adjusted profitability**: `net_profit = gain_move - gain_hold - (gas_cost + slippage)`
- **Prediction correctness**:
  - **HOLD**: Correct if `actual_current >= actual_target` OR `net_move_profit <= 0`
  - **REBALANCE**: Correct if `net_move_profit > 0`

---

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS ai_predictions (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prediction_type VARCHAR(20) NOT NULL,
    current_pool_id VARCHAR(100),
    target_pool_id VARCHAR(100),
    current_pool_apy DOUBLE PRECISION,
    target_pool_apy DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    reason TEXT,
    capital_usd DOUBLE PRECISION,
    validated BOOLEAN DEFAULT FALSE,
    validation_date TIMESTAMPTZ,
    actual_current_apy DOUBLE PRECISION,
    actual_target_apy DOUBLE PRECISION,
    was_correct BOOLEAN,
    profit_if_followed DOUBLE PRECISION,
    market_context JSONB,
    gas_cost DOUBLE PRECISION,
    slippage DOUBLE PRECISION,
    predicted_apy DOUBLE PRECISION,
    notes TEXT
);

CREATE INDEX idx_pred_valid ON ai_predictions(validated);
CREATE INDEX idx_pred_time ON ai_predictions(timestamp DESC);
```

---

## Method Signatures (PostgreSQL Version)

### record_prediction(data: Dict) → int
Records a new AI prediction. Accepts Dict with keys:
- `prediction_type`: "HOLD" or "REBALANCE"
- `current_pool_id`, `target_pool_id`: Pool identifiers
- `current_pool_apy`, `target_pool_apy`, `confidence`: Numeric values (DOUBLE PRECISION)
- `reason`: Text explanation
- `capital_usd`: USD amount
- `market_context`: Optional dict (stored as JSONB)
- `gas_cost`, `slippage`, `predicted_apy`: Numeric values
- `notes`: Optional text

**Returns**: Prediction ID (int)

### validate_prediction(pred_id: int, actual_current_apy: float, actual_target_apy: float) → Dict
Validates a prediction against actual market data. Calculates:
- Whether the prediction was correct
- Profit if the recommendation was followed
- Accounts for gas costs and slippage

**Returns**: 
```python
{
    'id': int,
    'prediction_type': str,
    'was_correct': bool,
    'profit_if_followed': float,
    'validation_date': datetime
}
```

### auto_validate_from_db() → Dict
Finds predictions ready for validation and automatically validates them using yield_logs table. This is the **closed-loop validation** that enables the AI feedback system.

**Returns**:
```python
{
    'validated': int,      # Count validated this run
    'pending': int,        # Count still awaiting validation
    'status': str
}
```

### get_accuracy_stats() → Dict
Overall accuracy snapshot.

**Returns**:
```python
{
    'total_predictions': int,
    'validated': int,
    'correct': int,
    'accuracy_percent': float,
    'pending': int,
    'total_profit': float,
    'total_friction': float,
    'net_profit': float
}
```

### get_accuracy_by_type() → List[Dict]
Grouped accuracy metrics by HOLD vs REBALANCE.

**Returns**:
```python
[
    {
        'prediction_type': 'HOLD',
        'total': int,
        'validated': int,
        'correct': int,
        'accuracy_percent': float,
        'avg_confidence_percent': float,
        'total_profit': float
    },
    ...
]
```

### get_pending_validations() → List[Dict]
Predictions ready for validation (older than validation_days).

---

## Integration with FastAPI

### Startup Event Pattern
```python
from psycopg2 import pool

@app.on_event("startup")
async def startup_event():
    # Create connection pool
    db_pool = pool.SimpleConnectionPool(
        1, 5,  # min_connections, max_connections
        dsn=os.getenv("DATABASE_URL")
    )
    
    # Initialize tracker with pool
    app.state.prediction_tracker = PredictionTracker(
        conn_pool=db_pool,
        validation_days=1
    )
    
    # Optional: Start background validation worker
    asyncio.create_task(validation_worker())

@app.on_event("shutdown")
async def shutdown_event():
    app.state.prediction_tracker.pool.closeall()
```

### Background Validation Worker (Optional)
```python
async def validation_worker():
    """Periodically auto-validate predictions from yield_logs"""
    while True:
        try:
            result = app.state.prediction_tracker.auto_validate_from_db()
            logger.info(f"Auto-validation: {result}")
        except Exception as e:
            logger.error(f"Validation worker error: {e}")
        
        await asyncio.sleep(6 * 3600)  # Run every 6 hours
```

---

## Error Handling
All database operations include:
- Try-except blocks catching `psycopg2.Error`
- Connection rollback on failure
- Logging of errors with context
- Proper connection release via `pool.putconn(conn)`

---

## Testing the Migration

### Test 1: Record a Prediction
```python
tracker = PredictionTracker(conn_pool=your_pool)
pred_id = tracker.record_prediction({
    'prediction_type': 'HOLD',
    'current_pool_id': 'aave-usdc-eth',
    'current_pool_apy': 8.5,
    'target_pool_id': 'compound-usdc-base',
    'target_pool_apy': 12.0,
    'confidence': 0.85,
    'reason': 'APY convergence expected',
    'capital_usd': 100000,
    'gas_cost': 50,
    'slippage': 100
})
print(f"Recorded prediction ID: {pred_id}")
```

### Test 2: Validate a Prediction
```python
result = tracker.validate_prediction(
    pred_id=pred_id,
    actual_current_apy=9.2,
    actual_target_apy=11.0
)
print(f"Correct: {result['was_correct']}")
print(f"Profit: ${result['profit_if_followed']:.2f}")
```

### Test 3: Check Accuracy
```python
stats = tracker.get_accuracy_stats()
print(f"Overall Accuracy: {stats['accuracy_percent']}%")

by_type = tracker.get_accuracy_by_type()
for row in by_type:
    print(f"{row['prediction_type']}: {row['accuracy_percent']}%")
```

---

## Migration Checklist
- [x] Replace sqlite3 with psycopg2
- [x] Update schema to use TIMESTAMPTZ and DOUBLE PRECISION
- [x] Refactor all methods to use connection pool
- [x] Implement financial validation logic
- [x] Add auto_validate_from_db() with yield_logs integration
- [x] Add get_accuracy_by_type() for segmented reporting
- [x] Implement proper error handling with rollback
- [x] Clean up legacy SQLite code
- [ ] Test with actual PostgreSQL/TimescaleDB instance
- [ ] Integrate with server.py startup event
- [ ] Deploy background validation worker
- [ ] Monitor accuracy metrics in production

---

## Files Modified
- `/src/backtest/prediction_tracker.py` - Complete refactoring (390+ lines)

## Dependencies
```
psycopg2-binary>=2.9.0
psycopg2>=2.9.0
```

## Next Steps
1. **Test Integration**: Run with actual PostgreSQL database
2. **FastAPI Integration**: Add to server.py startup event
3. **Background Worker**: Deploy auto-validation loop
4. **API Endpoints**: Create REST endpoints for prediction CRUD and accuracy reporting
5. **Monitoring**: Add Prometheus metrics for accuracy tracking

---

## Questions?
See inline comments in `/src/backtest/prediction_tracker.py` for implementation details.
