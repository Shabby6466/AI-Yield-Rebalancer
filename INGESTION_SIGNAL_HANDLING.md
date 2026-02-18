# DefiLlama Ingestion: Signal Handling & Graceful Shutdown

## Overview

The ingestion pipeline now implements **graceful shutdown** using Unix signal handlers (SIGINT, SIGTERM). This ensures that:

1. **No data corruption** - Active ingestion tasks complete before shutdown
2. **Clean resource cleanup** - HTTP connections and DB pools properly closed
3. **Production-ready** - Handles Ctrl+C and container termination signals

---

## Architecture

### Signal Flow

```
User presses Ctrl+C (or SIGTERM from container)
    ↓
Signal handler triggered → calls ingestor.shutdown_event.set()
    ↓
Main loop detects shutdown_event is set
    ↓
Waits for current_ingestion_task to complete (if active)
    ↓
Calls await ingestor.close() → closes HTTP client + DB pool
    ↓
Clean exit
```

### Key Components

#### 1. `shutdown_event` (asyncio.Event)
- **Created in**: `YieldIngestor.__init__()`
- **Purpose**: Signal that shutdown is requested
- **Usage**: Checked in main loop's wait timeout, breaks loop when set
- **Pattern**: `await asyncio.wait_for(ingestor.shutdown_event.wait(), timeout=3600.0)`

#### 2. `current_ingestion_task` (asyncio.Task | None)
- **Created in**: `YieldIngestor.__init__()` as None
- **Set during**: `await ingestor.fetch_and_store()` in the main loop
- **Cleared after**: Task completes (finally block in main loop)
- **Purpose**: Tracks active ingestion to prevent mid-task shutdown

#### 3. Signal Handlers
- **SIGINT** (Ctrl+C): Graceful shutdown from terminal
- **SIGTERM** (Container termination): Graceful shutdown from orchestration
- **Both**: Call `ingestor.shutdown_event.set()` and allow main loop to finish iteration

### Implementation Details

#### Main Loop with Graceful Shutdown

```python
async def main_with_signal_handling():
    # Setup signal handlers
    def _signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, setting shutdown event...")
        ingestor.shutdown_event.set()
    
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    
    # Main loop
    while not ingestor.shutdown_event.is_set():
        try:
            logger.info("Starting ingestion run...")
            ingestor.current_ingestion_task = asyncio.current_task()
            await ingestor.fetch_and_store()
        finally:
            ingestor.current_ingestion_task = None
        
        # Wait 1 hour or until shutdown event is set
        if not ingestor.shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    ingestor.shutdown_event.wait(),
                    timeout=3600.0
                )
            except asyncio.TimeoutError:
                # Timeout means 1 hour passed, proceed to next ingestion
                pass
    
    # Cleanup
    logger.info("Ingestion loop exiting...")
    await ingestor.close()
```

#### Resource Cleanup

```python
async def close(self):
    """Close HTTP client and connection pool"""
    logger.info("Closing DefiLlama client...")
    await self.client.close()
    
    if self.conn_pool:
        logger.info("Closing connection pool...")
        self.conn_pool.closeall()
    
    logger.info("Ingestor cleanup complete")
```

---

## Behavior Scenarios

### Scenario 1: Normal Operation
```
Time 0:00 - Start ingestion
Time 0:05 - Fetch 5000 yields, insert 500 records
Time 0:10 - Wait 1 hour (3600s timeout on shutdown_event)
...
Time 1:00 - Timeout expires, start next ingestion
...
```

### Scenario 2: Graceful Shutdown During Wait
```
Time 0:00 - Start ingestion
Time 0:05 - Fetch & insert complete
Time 0:10 - Start waiting for next ingestion (3600s timeout)
Time 0:30 - User presses Ctrl+C
          → Signal handler sets shutdown_event
          → asyncio.wait_for() returns immediately
          → Loop condition (while not shutdown_event.is_set()) is False
          → Exit loop, call await ingestor.close()
          → Clean shutdown
```

### Scenario 3: Graceful Shutdown During Active Ingestion
```
Time 0:00 - Start ingestion
Time 0:02 - During fetch_and_store()
Time 0:03 - User presses Ctrl+C
          → Signal handler sets shutdown_event
          → asyncio.CancelledError raised? No, signal doesn't cancel tasks
          → fetch_and_store() continues to completion
Time 0:05 - fetch_and_store() finishes
          → Check: is shutdown_event set? Yes
          → Skip wait (if condition prevents entering wait block)
          → Exit loop, call await ingestor.close()
          → Clean shutdown
```

### Scenario 4: Container Termination (SIGTERM)
```
Docker/Kubernetes sends SIGTERM to container
         ↓
Signal handler triggered
         ↓
shutdown_event.set()
         ↓
Current ingestion finishes + cleanup proceeds
         ↓
Process exits cleanly before grace period expires
```

---

## Configuration

### Environment Variables

```bash
# Database connection
export DATABASE_URL="postgresql://user:pass@localhost:5432/db"

# Run once then exit (useful for testing)
export INGEST_RUN_ONCE=true

# Connection pool sizing (optional)
# Default: min=2, max=5
```

### Running the Ingestor

**Continuous ingestion with graceful shutdown:**
```bash
python3 src/data/defillama_ingestor.py
# Press Ctrl+C to gracefully shutdown
# Or send SIGTERM from container orchestration
```

**One-time ingest for testing:**
```bash
export INGEST_RUN_ONCE=true
python3 src/data/defillama_ingestor.py
# Runs one fetch_and_store, then exits
```

**Via FastAPI server (recommended for production):**
```bash
# Server starts background ingestor on startup
# Shuts down cleanly on server termination
python3 -m src.api.server  # or: uvicorn src.api.server:app
```

---

## Logging Output Examples

### Startup
```
INFO:__main__:Connection pool initialized: 2-5 connections
INFO:__main__:Starting ingestion run...
INFO:__main__:Fetching data from DeFiLlama...
INFO:__main__:Successfully ingested 487 records.
INFO:__main__:Waiting 1 hour for next snapshot...
```

### Graceful Shutdown
```
^C (Ctrl+C pressed)
INFO:__main__:Received signal 2, setting shutdown event...
INFO:__main__:Ingestion loop exiting...
INFO:__main__:Closing DefiLlama client...
INFO:__main__:Closing connection pool...
INFO:__main__:Ingestor cleanup complete
```

### SIGTERM from Container
```
INFO:__main__:Received signal 15, setting shutdown event...
INFO:__main__:Ingestion loop exiting...
INFO:__main__:Closing DefiLlama client...
INFO:__main__:Closing connection pool...
INFO:__main__:Ingestor cleanup complete
```

---

## Production Deployment Checklist

- [ ] Database URL configured in environment
- [ ] TimescaleDB schema created (`create_yield_snapshots_timescale.sql`)
- [ ] Connection pool size tuned for expected load (min=2, max=5 is reasonable for background task)
- [ ] Logs monitored (watch for "Successfully ingested X records")
- [ ] Signal handlers tested (Ctrl+C should exit cleanly)
- [ ] Container orchestration configured with reasonable grace period (15-30s minimum)

---

## Technical Details

### Why Signal Handlers Matter

Without signal handlers, a direct `KeyboardInterrupt` or process kill would:
- Abort active ingestion mid-insert
- Leave database transaction open
- Not close connection pool (resource leak)
- Potentially corrupt yield_snapshots hypertable

With signal handlers:
- Active ingestion completes before cleanup
- All transactions committed before connection closure
- Pool resources properly released
- Data integrity maintained

### Why Connection Pooling Matters

Connection pooling prevents:
- **Socket exhaustion**: Reusing 2-5 connections vs creating new ones each run
- **Database overload**: Limits concurrent connections
- **Cascading failures**: Graceful fallback if pool exhausted

### Why Persistent Session Matters

Persistent aiohttp.ClientSession prevents:
- **Rate limit issues**: TCP connection reuse respects DeFiLlama's rate limits
- **Latency overhead**: Eliminates SSL handshake per request (~100-300ms)
- **Resource leaks**: Single session properly closed on shutdown

---

## References

- [Python asyncio.Event](https://docs.python.org/3/library/asyncio.html#event)
- [Python signal module](https://docs.python.org/3/library/signal.html)
- [psycopg2 connection pooling](https://www.psycopg.org/psycopg2/docs/extras.html#module-psycopg2.pool)
- [FastAPI Lifespan events](https://fastapi.tiangolo.com/advanced/events/#lifespan)

