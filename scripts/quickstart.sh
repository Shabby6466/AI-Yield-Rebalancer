#!/bin/bash
# Quick Start Script for DeFi Yield Data Collection

set -e

echo "=================================================="
echo "  DeFi Yield Data Collection - Quick Start"
echo "=================================================="
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check environment
echo -e "${BLUE}📋 Checking environment...${NC}"
if [ ! -f ".env" ]; then
    echo "❌ .env file not found!"
    echo "Please create .env with database credentials:"
    echo "  DB_HOST=localhost"
    echo "  DB_PORT=5432"
    echo "  DB_NAME=defi_yield_db"
    echo "  DB_USER=postgres"
    echo "  DB_PASSWORD=postgres"
    exit 1
fi

# Load environment
source .env

echo -e "${GREEN}✓${NC} Environment loaded"
echo ""

# Initialize database
echo -e "${BLUE}🗄️  Initializing database schema...${NC}"
PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -U $DB_USER -d $DB_NAME -f scripts/init_database.sql > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓${NC} Database initialized"
else
    echo "❌ Database initialization failed"
    exit 1
fi
echo ""

# Check if data exists
RECORD_COUNT=$(PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -U $DB_USER -d $DB_NAME -t -c "SELECT COUNT(*) FROM yield_metrics;" 2>/dev/null | tr -d ' ')

if [ "$RECORD_COUNT" -gt 100 ]; then
    echo -e "${BLUE} Database already contains $RECORD_COUNT records${NC}"
    echo "Skipping backfill (already done)"
    echo ""
else
    # Run backfill
    echo -e "${BLUE}📥 Starting historical data backfill...${NC}"
    echo "This will fetch 1-3 years of data from DefiLlama"
    echo "Expected time: 30-60 seconds"
    echo ""
    
    python src/ingestion/backfill_client.py
    
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}✓${NC} Backfill completed"
    else
        echo "❌ Backfill failed"
        exit 1
    fi
fi
echo ""

# Test live collection
echo -e "${BLUE}🔄 Testing live data collection...${NC}"
python src/ingestion/live_collector.py

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓${NC} Live collection working"
else
    echo "❌ Live collection failed"
    exit 1
fi
echo ""

# Show summary
echo "=================================================="
echo -e "${GREEN}✅ Setup Complete!${NC}"
echo "=================================================="
echo ""

PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "
SELECT 
    COUNT(*) as total_records,
    COUNT(DISTINCT asset_id) as assets_tracked,
    TO_CHAR(MIN(time), 'YYYY-MM-DD') as earliest_data,
    TO_CHAR(MAX(time), 'YYYY-MM-DD') as latest_data
FROM yield_metrics;
"

echo ""
echo "=================================================="
echo "  Next Steps"
echo "=================================================="
echo ""
echo "1️⃣  View latest yields:"
echo "   psql -d $DB_NAME -c 'SELECT * FROM latest_yields;'"
echo ""
echo "2️⃣  Start automated collection (every 15 minutes):"
echo "   python src/ingestion/scheduler.py"
echo ""
echo "3️⃣  Or run in background:"
echo "   nohup python src/ingestion/scheduler.py > logs/scheduler.log 2>&1 &"
echo ""
echo "4️⃣  Check data:"
echo "   psql -d $DB_NAME -c 'SELECT COUNT(*), MAX(time) FROM yield_metrics;'"
echo ""
echo "=================================================="
