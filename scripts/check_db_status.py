import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def check():
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("No DATABASE_URL found")
        return

    try:
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM protocols")
            print(f"Protocols count: {cur.fetchone()[0]}")
            
            cur.execute("SELECT name FROM protocols")
            print(f"Protocols: {[r[0] for r in cur.fetchall()]}")
            
            cur.execute("SELECT count(*) FROM protocol_yields")
            print(f"Protocol yields count: {cur.fetchone()[0]}")
            
            if cur.fetchone(): # Just check if there's at least one
                cur.execute("SELECT asset, apy_percent, recorded_at FROM protocol_yields LIMIT 5")
                print("Sample yields:")
                for r in cur.fetchall():
                    print(r)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check()
