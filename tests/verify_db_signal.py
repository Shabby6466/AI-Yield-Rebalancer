
import psycopg2
import os
import json
from dotenv import load_dotenv

load_dotenv()

def verify_latest_prediction():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL not set")
        return

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Query the latest prediction
        query = """
        SELECT id, timestamp, prediction_type, current_pool_id, target_pool_id, confidence, reason
        FROM ai_predictions
        ORDER BY timestamp DESC
        LIMIT 1;
        """
        cur.execute(query)
        row = cur.fetchone()
        
        if row:
            print(f"✅ Found Prediction!")
            print(f"ID: {row[0]}")
            print(f"Time: {row[1]}")
            print(f"Type: {row[2]}")
            print(f"Current Pool: {row[3]}")
            print(f"Target Pool: {row[4]}")
            print(f"Confidence: {row[5]}")
            print(f"Reason: {row[6]}")
        else:
            print("⚠️ No predictions found in DB yet.")
            
        cur.close()
        conn.close()

    except Exception as e:
        print(f"❌ DB Query failed: {e}")

if __name__ == "__main__":
    verify_latest_prediction()
