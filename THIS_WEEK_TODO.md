# ✅ Improvement Action Checklist

## START HERE: This Week's Quick Wins

These 5 items take <20 hours total and unblock everything else:

### 1. ✅ Diagnose Keeper Service Issue (2-4 hours)
**Status:** keeper_service.py exits with code 1 - BLOCKING  
**Effort:** 2-4 hours  
**Blocker:** YES - prevents automated rebalancing

**Immediate Actions:**
```bash
# Step 1: Run keeper service with verbose logging
cd /Users/Akmal/Desktop/projects/rebalancing/AI-Yield-Rebalancer
python -u src/execution/keeper_service.py 2>&1 | tee keeper_debug.log

# Step 2: Check for obvious errors
grep -i "error\|exception\|traceback" keeper_debug.log

# Step 3: Check Python import issues
python -c "from src.execution.keeper_service import KeeperService; print('OK')"
python -c "from src.execution.ml_prediction_service import MLPredictionService; print('OK')"
python -c "from src.execution.contract_manager import ContractManager; print('OK')"
python -c "from src.execution.roi_calculator import ROICalculator; print('OK')"

# Step 4: Check environment variables
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('ENV_VARS:', [k for k in os.environ.keys() if 'KEEPER' in k or 'DB' in k or 'RPC' in k])"

# Step 5: Check database connectivity
python -c "import psycopg2; conn = psycopg2.connect('dbname=defi_yield_db user=postgres'); print('DB OK'); conn.close()"
```

**Expected Outcome:**  
- [ ] Keeper service runs without crashing  
- [ ] Detailed logs explain any errors  
- [ ] Can execute rebalances manually

---

### 2. ✅ Add Comprehensive Error Handling (3-4 hours)
**Status:** No try-catch in keeper main loop  
**Effort:** 3-4 hours  
**Blocker:** YES - system can crash mid-operation

**Code Changes Required:**
```python
# File: src/execution/keeper_service.py
# Around line 50 in main execution loop

def run_loop(self):
    """Main keeper loop with error handling"""
    logger.info("🚀 Keeper service starting...")
    
    while True:
        try:
            # 1. Check if should rebalance
            should_rebalance = self.check_rebalance_conditions()
            
            if should_rebalance:
                # 2. Execute rebalance
                tx_hash = self.execute_rebalance()
                logger.info(f"✅ Rebalance executed: {tx_hash}")
            else:
                logger.debug("⏸️  Not time to rebalance yet")
            
            # 3. Wait before next check
            time.sleep(self.interval_minutes * 60)
            
        except KeyboardInterrupt:
            logger.info("👋 Keeper service stopped by user")
            break
        except Exception as e:
            # CRITICAL: Don't crash, log and continue
            logger.error(f"❌ ERROR in keeper loop: {type(e).__name__}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            # Try to recover
            try:
                time.sleep(60)  # Wait 1 minute before retry
                logger.info("🔄 Attempting to recover...")
            except:
                pass
```

**Expected Outcome:**  
- [ ] Keeper continues running even on error  
- [ ] All errors logged with full traceback  
- [ ] System auto-recovers after 1 minute

---

### 3. ✅ Create Health Check Endpoint (2 hours)
**Status:** No way to verify system health remotely  
**Effort:** 2 hours  
**Blocker:** YES - can't monitor system status

**Quick Implementation:**
```python
# File: src/api/health_check.py
import json
import psycopg2
from datetime import datetime
from web3 import Web3

def get_system_health():
    """Return system health status"""
    health = {
        "timestamp": datetime.utcnow().isoformat(),
        "status": "healthy",  # or "degraded" or "critical"
        "components": {}
    }
    
    # 1. Check database
    try:
        conn = psycopg2.connect(
            dbname="defi_yield_db",
            user="postgres",
            password=os.getenv("DB_PASSWORD"),
            host="localhost"
        )
        conn.close()
        health["components"]["database"] = "✅ OK"
    except Exception as e:
        health["components"]["database"] = f"❌ {str(e)}"
        health["status"] = "critical"
    
    # 2. Check RPC connection
    try:
        w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
        assert w3.is_connected()
        block = w3.eth.block_number
        health["components"]["rpc"] = f"✅ OK (Block: {block})"
    except Exception as e:
        health["components"]["rpc"] = f"❌ {str(e)}"
        health["status"] = "critical"
    
    # 3. Check ML models
    try:
        models_path = "models/lstm_predictor_final.ckpt"
        assert os.path.exists(models_path)
        health["components"]["ml_models"] = "✅ OK"
    except Exception as e:
        health["components"]["ml_models"] = f"❌ {str(e)}"
        health["status"] = "degraded"
    
    # 4. Check keeper service
    try:
        # Check if keeper process is running
        import subprocess
        result = subprocess.run(
            ["pgrep", "-f", "keeper_service"],
            capture_output=True
        )
        if result.returncode == 0:
            health["components"]["keeper"] = "✅ Running"
        else:
            health["components"]["keeper"] = "⚠️ Not running"
            health["status"] = "degraded"
    except:
        health["components"]["keeper"] = "❓ Unknown"
    
    return health

# Export as JSON
if __name__ == "__main__":
    print(json.dumps(get_system_health(), indent=2))
```

**Test it:**
```bash
# Can call from anywhere to check health
python src/api/health_check.py

# Expected output:
# {
#   "timestamp": "2026-02-17T10:30:45.123456",
#   "status": "healthy",
#   "components": {
#     "database": "✅ OK",
#     "rpc": "✅ OK (Block: 19547321)",
#     "ml_models": "✅ OK",
#     "keeper": "✅ Running"
#   }
# }
```

**Expected Outcome:**  
- [ ] Can run health check anytime  
- [ ] Get status of all critical components  
- [ ] Know what's broken immediately

---

### 4. ✅ Setup Daily Backup (2 hours)
**Status:** No backups = total loss if DB crashes  
**Effort:** 2 hours  
**Blocker:** NO - but critical for safety

**Implementation:**
```bash
#!/bin/bash
# File: scripts/backup_database.sh
# Make executable: chmod +x scripts/backup_database.sh

BACKUP_DIR="/Users/Akmal/Desktop/projects/rebalancing/AI-Yield-Rebalancer/backups"
DB_NAME="defi_yield_db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/defi_yield_backup_$TIMESTAMP.sql"

# Create backup directory if needed
mkdir -p "$BACKUP_DIR"

# Dump database
pg_dump "$DB_NAME" > "$BACKUP_FILE"

# Compress
gzip "$BACKUP_FILE"

# Log backup
echo "✅ Backup created: $BACKUP_FILE.gz" >> "$BACKUP_DIR/backup.log"
echo "   Size: $(du -h "$BACKUP_FILE.gz" | cut -f1)" >> "$BACKUP_DIR/backup.log"

# Keep only last 30 days of backups
find "$BACKUP_DIR" -name "*.gz" -mtime +30 -delete

# Report status
echo "$(date): Backup complete. Latest: $(ls -1t "$BACKUP_DIR"/*.gz | head -1)"
```

**Add to crontab for daily backups:**
```bash
# Add to your crontab (crontab -e)
# Run backup every day at 2 AM
0 2 * * * /Users/Akmal/Desktop/projects/rebalancing/AI-Yield-Rebalancer/scripts/backup_database.sh
```

**Expected Outcome:**  
- [ ] Daily automated backups  
- [ ] 30-day retention  
- [ ] Can restore from any point in last month

---

### 5. ✅ Add Slack Alert for Critical Errors (3 hours)
**Status:** Errors go unnoticed  
**Effort:** 3 hours  
**Blocker:** NO - but critical for awareness

**Implementation:**
```python
# File: src/core/slack_alerter.py
import requests
import os
from datetime import datetime

class SlackAlerter:
    def __init__(self):
        self.webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        if not self.webhook_url:
            print("⚠️  SLACK_WEBHOOK_URL not set, alerts disabled")
    
    def send_alert(self, severity: str, title: str, message: str):
        """Send alert to Slack"""
        if not self.webhook_url:
            return
        
        # Color code by severity
        color_map = {
            "CRITICAL": "#FF0000",  # Red
            "WARNING": "#FFA500",   # Orange
            "INFO": "#0099FF"       # Blue
        }
        
        payload = {
            "text": f"[{severity}] {title}",
            "attachments": [
                {
                    "color": color_map.get(severity, "#000000"),
                    "fields": [
                        {
                            "title": "Time",
                            "value": datetime.utcnow().isoformat(),
                            "short": True
                        },
                        {
                            "title": "Severity",
                            "value": severity,
                            "short": True
                        },
                        {
                            "title": "Message",
                            "value": message,
                            "short": False
                        }
                    ]
                }
            ]
        }
        
        try:
            requests.post(self.webhook_url, json=payload, timeout=5)
        except Exception as e:
            print(f"❌ Failed to send Slack alert: {e}")

# Usage
alerter = SlackAlerter()

# In keeper_service.py:
try:
    execute_rebalance()
except Exception as e:
    alerter.send_alert("CRITICAL", "Rebalance Failed", str(e))
```

**Setup Slack Webhook:**
1. Go to https://api.slack.com/apps
2. Create New App → "From scratch"
3. Name: "AI-Yield-Rebalancer"
4. Go to "Incoming Webhooks" → Activate
5. Create New Webhook to Channel (e.g., #alerts)
6. Copy webhook URL: `https://hooks.slack.com/...`
7. Add to .env:
```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

**Test it:**
```bash
python -c "
from src.core.slack_alerter import SlackAlerter
alerter = SlackAlerter()
alerter.send_alert('WARNING', 'Test Alert', 'If you see this in Slack, webhooks work!')
"
```

**Expected Outcome:**  
- [ ] Alert notifications in Slack  
- [ ] Know immediately when issues occur  
- [ ] Can act quickly on critical problems

---

## Timeline: Complete in 2 Days

**Day 1 (6-8 hours):**
- Morning: Diagnose keeper (2-4h) + add error handling (3-4h)
- Result: Keeper runs without crashing ✅

**Day 2 (4-6 hours):**
- Morning: Health check endpoint (2h) + Slack alerts (3h)
- Afternoon: Test everything works
- Result: Can monitor system remotely ✅

**Extra (optional, 2 hours):**
- Backup automation
- Result: Protected against data loss ✅

---

## Verification Checklist

After completing above items, verify:

- [ ] `keeper_service.py` runs for 10 minutes without crashing
- [ ] All errors appear in `logs/keeper_service.log`
- [ ] Health check shows all green: `python src/api/health_check.py`
- [ ] Slack receives test alert
- [ ] Database backup file exists with recent timestamp
- [ ] ROI is being calculated and stored

---

## Next Steps (After This Week)

Once above complete:

1. **Week 2:** TIER 2 features (ROI scheduler, complete alerts, API)
2. **Week 3:** TIER 3 performance (database optimization, caching)
3. **Week 4:** TIER 4 risk management (kill switch, audit logging)
4. **Week 5+:** TIER 5 enhancements (ML improvements, gas optimization)

---

## 🎯 Success Criteria

### This Week's Goal
- System runs 24/7 without human intervention
- All errors are logged and visible
- Critical issues alert immediately
- Data is backed up

### Check Mark = Ready for Next Phase
✅ Keeper service stable  
✅ Error handling working  
✅ Health checks passing  
✅ Backups automated  
✅ Slack alerts working  

Then you're ready to move to TIER 2 features!

---

**🚀 Start with #1 today. You'll have a stable system by Friday!**
