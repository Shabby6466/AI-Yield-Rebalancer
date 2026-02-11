#!/usr/bin/env python3
"""Quick script to check collected data"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    dbname=os.getenv("DB_NAME", "defi_yield_db"),
    user=os.getenv("DB_USER", "faizan"),
    password=os.getenv("DB_PASSWORD", ""),
    host=os.getenv("DB_HOST", "localhost"),
    port=os.getenv("DB_PORT", "5432")
)
cur = conn.cursor()

# Count total records
cur.execute('SELECT COUNT(*) FROM protocol_yields')
total = cur.fetchone()[0]
print(f"\n Total records collected: {total}")

# Get latest collection time
cur.execute('SELECT MAX(recorded_at) FROM protocol_yields')
latest = cur.fetchone()[0]
print(f"⏰ Latest collection: {latest}")

# Count by protocol
cur.execute("""
    SELECT protocol_id, COUNT(*) 
    FROM protocol_yields 
    GROUP BY protocol_id 
    ORDER BY protocol_id
""")
print("\n Records by protocol:")
for protocol_id, count in cur.fetchall():
    print(f"   Protocol {protocol_id}: {count} records")

# Show latest yields
cur.execute("""
    SELECT asset, apy_percent, recorded_at 
    FROM protocol_yields 
    ORDER BY recorded_at DESC 
    LIMIT 10
""")
print("\n💰 Latest 10 yields:")
for asset, apy, recorded_at in cur.fetchall():
    print(f"   {asset:20s} {apy:8.3f}% at {recorded_at}")

conn.close()
print("\n✅ Data check complete!")
