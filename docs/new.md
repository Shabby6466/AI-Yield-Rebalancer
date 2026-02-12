backtest
historical in timeseries
apy trade sizing

test net investment and check for investment


predeection then backtest

3 Modes:
1. Backfill (One-time historical fetch) — Already done 

bash
python -m src.scheduler.collector --backfill --pools 20
This fetched 6,042 historical records for the top 20 pools.

2. Run Once (Single snapshot)

bash
python -m src.scheduler.collector --once
Fetches current yields for all stablecoin pools and stores them in the DB.

3. Continuous Loop (Every 6 hours) ⬅ This is the "real" scheduler

bash
python -m src.scheduler.collector --interval 6
Runs forever, collecting data every 6 hours. You can change the interval (e.g., --interval 1 for hourly).


nohup python -m src.scheduler.collector --interval 6 &

To keep data fresh:
Manual: Click "Refresh Pool Data" in the sidebar
Automatic: Run the scheduler: python -m src.scheduler.collector --interval 1 (hourly updates)