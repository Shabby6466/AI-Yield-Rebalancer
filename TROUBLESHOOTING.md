# AI Yield Rebalancer: Troubleshooting & Deployment Guide

This document tracks common issues encountered during the implementation of yield harvesting and ROI tracking, along with the commands used to fix them.

---

## 1. Smart Contract Connectivity Issues

### Issue: "Could not transact with/call contract function"
**Cause:** The rebalancer address in `.env` (derived from older deployments) does not match the fresh address in `contracts/deployed_address.txt` after an Anvil fork reset.

**Solution:**
Ensure the system reads from the latest deployment file.
```bash
# Verify the current live hub address
cat contracts/deployed_address.txt

# Manually update .env if priority logic isn't applied yet
# (Re-run deployment script to sync everything)
docker compose exec rebalancer python scripts/start_local_fork.py
```

---

## 2. Docker Container & Code Refresh

### Issue: API returns `404 Not Found` or `500 Server Error` after code changes
**Cause:** The `brain` (API) container bakes code into the image at build time. Restarting is not enough; a rebuild is required.

**Solution:**
```bash
# Force a rebuild of the API container
docker compose up -d --build brain
```

---

## 3. Git Operations & Merge Conflicts

### Issue: `git pull` fails due to `system_state.json`
**Cause:** The server's live `system_state.json` (runtime state) conflicts with the version in the repo.

**Solution:**
1. Stash or reset the local changes:
```bash
git checkout --theirs data/system_state.json
git add data/system_state.json
git pull
```
2. **Preventative Fix:** Added `data/system_state.json` to `.gitignore`.

---

## 4. Python Runtime Errors

### Issue: `IndentationError` in `rebalancer_service.py`
**Cause:** Inconsistent spacing (mixing 19 and 20 spaces) in the rebalance transaction loop.

**Fix:** Verified and normalized all indentations to 4-space multiples.

### Issue: `NameError: name 'state' is not defined` in `server.py`
**Cause:** Attempting to access `state.get(...)` before the `StateStore` was loaded.

**Fix:**
```python
from src.core.state_store import StateStore
state = StateStore().load_state()
```

---

## 5. Yield Simulation & Harvesting

### Issue: Harvest script fails because Anvil fork was reset
**Cause:** Rebuilding containers can wipe the ephemeral Anvil state.

**Solution:**
If the fork is reset, you must re-run the allocation script to redeploy funds before harvesting:
```bash
# 1. Start fork & deploy
docker compose exec rebalancer python scripts/start_local_fork.py

# 2. Re-allocate funds to protocols (Aave/Compound)
docker compose exec rebalancer python scripts/allocate_to_hub.py

# 3. Simulate 30 days of yield
docker compose exec rebalancer python scripts/harvest_yield.py --days 30
```

---

## 6. Deployment Workflow (Cheatsheet)

To push changes from Local to Server and ensure they work:
```bash
# Local
git add .
git commit -m "feat: your change"
git push

# Server
git pull
docker compose up -d --build brain
docker compose restart dashboard rebalancer
```
