
import os
import sys
import subprocess
import time

def run_deploy():
    print("--- 🚢 AI Yield Rebalancer: Full Stack Deployer ---")
    
    # 1. Environment Verification
    if not os.path.exists(".env"):
        print("❌ Error: .env file missing. Please create it with ALCHEMY_API_KEY.")
        sys.exit(1)
        
    # 2. Path Fixes for Docker
    print("🔍 Optimizing state store for containerized environments...")
    # (We already updated state_store.py to be portable)

    # 3. Launch Stack
    print("🐳 Building and launching Docker stack (DB, Engine, Front-end, Fork)...")
    try:
        # Using 'docker compose' (V2) instead of legacy 'docker-compose'
        # This avoids metadata errors during log streaming and recreation
        subprocess.run(["docker", "compose", "up", "--build", "-d"], check=True)
        print("✅ Stack started successfully in detached mode.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Docker Compose failed: {e}")
        sys.exit(1)

    # 4. Success Info
    print("\n--- 📍 Deployment Map ---")
    print("🖥️ Dashboard: http://localhost:8501")
    print("🔌 Mainnet Fork (RPC): http://localhost:8545")
    print("🗄️ Database (Postgres): localhost:5432")
    print("\n💡 Monitoring: Run 'docker-compose logs -f rebalancer' to see the AI agent's thoughts.")

if __name__ == "__main__":
    run_deploy()
