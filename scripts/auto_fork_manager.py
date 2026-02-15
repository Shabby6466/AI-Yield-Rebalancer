
import logging
import os
import psutil
import requests
import shutil
import signal
import subprocess
import sys
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
log_file = "data/anvil.log"
os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ForkManager")

# Configuration
UPDATE_INTERVAL_SECONDS = 300  # 5 minutes
ANVIL_PORT = 8545
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")

class ForkManager:
    def __init__(self):
        self.anvil_process = None
        self.anvil_path = shutil.which("anvil") or "/root/.foundry/bin/anvil"
        self.fork_url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}" if ALCHEMY_API_KEY else "https://eth.drpc.org"
        
        # Ensure log directory and files exist
        os.makedirs("data", exist_ok=True)
        for f in ["data/anvil_output.log", "data/anvil.log"]:
            if not os.path.exists(f):
                open(f, 'a').close()
        
        # Force a clean start on initialization to capture output logs
        logger.info("Initializing ForkManager: Forcing clean Anvil start...")
        self.kill_process_on_port(ANVIL_PORT)
        self.start_anvil_process()

    def kill_process_on_port(self, port):
        """Find and kill any process listening on specific port using psutil"""
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                for conns in proc.connections(kind='inet'):
                    if conns.laddr.port == port:
                        logger.info(f"🔄 Stopping process {proc.info['name']} (PID: {proc.info['pid']}) on port {port}...")
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            logger.warning(f"⚠️ Process {proc.info['pid']} did not terminate, forcing kill...")
                            proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

    def start_anvil_process(self):
        """Start a fresh Anvil process"""
        logger.info(f"🚀 Launching Anvil process...")
        cmd = [
            self.anvil_path,
            "--fork-url", self.fork_url,
            "--port", str(ANVIL_PORT),
            "--host", "0.0.0.0"
        ]
        
        # Open log file for anvil output
        anvil_log = open("data/anvil_output.log", "a")
        self.anvil_process = subprocess.Popen(
            cmd, 
            stdout=anvil_log, 
            stderr=anvil_log,
            preexec_fn=os.setsid
        )
        
        # Wait for readiness
        for _ in range(30):
            if self.is_anvil_alive():
                logger.info("✅ Anvil process is up and running.")
                return True
            time.sleep(1)
        return False

    def is_anvil_alive(self):
        """Check if Anvil is responding to RPC"""
        try:
            response = requests.post(
                f"http://localhost:{ANVIL_PORT}",
                json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
                timeout=1
            )
            return response.status_code == 200
        except:
            return False

    def reset_via_rpc(self):
        """Try to instantly re-fork using anvil_reset RPC call"""
        try:
            logger.info("⚡ Attempting Instant RPC Reset...")
            response = requests.post(
                f"http://localhost:{ANVIL_PORT}",
                json={
                    "jsonrpc": "2.0", 
                    "method": "anvil_reset", 
                    "params": [{"forking": {"jsonRpcUrl": self.fork_url}}], 
                    "id": 1
                },
                timeout=5
            )
            if response.status_code == 200:
                logger.info("✅ Instant Reset successful (Forked from latest Mainnet block).")
                return True
        except Exception as e:
            logger.error(f"⚠️ RPC Reset unsuccessful: {e}")
        return False

    def deploy_contracts(self):
        """Deploy StrategyHub to the fresh fork state"""
        logger.info("🛠️ Deploying contracts to fork state...")
        # start_local_fork.py handles the logic of checking Anvil and then deploying
        cmd = [sys.executable, "scripts/start_local_fork.py"]
        try:
            subprocess.run(cmd, check=True)
            logger.info("✅ Contracts redeployed successfully.")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Deployment failed: {e}")
            return False

    def refresh_fork(self):
        """Refresh logic: Try RPC Reset first, fallback to Full Restart"""
        logger.info(f"\n--- 🔄 REFRESHING FORK ({time.strftime('%H:%M:%S')}) ---")
        
        success = False
        
        # 1. Try the instant RPC reset
        if self.is_anvil_alive():
            success = self.reset_via_rpc()
        
        # 2. Fallback to full kill/restart if reset failed or node was down
        if not success:
            logger.info("🔌 Falling back to full Anvil restart...")
            if self.anvil_process:
                try:
                    os.killpg(os.getpgid(self.anvil_process.pid), signal.SIGTERM)
                    self.anvil_process.wait(timeout=5)
                except:
                    pass
            self.kill_process_on_port(ANVIL_PORT)
            success = self.start_anvil_process()
            
        # 3. Always redeploy since the state is now a clean Mainnet fork
        if success:
            self.deploy_contracts()
            logger.info(f"✨ Fork cycle complete. Next update in {UPDATE_INTERVAL_SECONDS/60} minutes.")
        else:
            logger.error("🚨 Refresh failed entirely. Will retry next interval.")

    def run(self):
        logger.info(f"🤖 Auto-Fork Manager (Instant Rolling Fork) initialized.")
        logger.info(f"   Refresh Interval: {UPDATE_INTERVAL_SECONDS}s")
        
        try:
            while True:
                self.refresh_fork()
                time.sleep(UPDATE_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("\n🛑 Shutting down Auto-Fork Manager...")
            if self.anvil_process:
                try:
                    os.killpg(os.getpgid(self.anvil_process.pid), signal.SIGTERM)
                except:
                    pass
            self.kill_process_on_port(ANVIL_PORT)

if __name__ == "__main__":
    manager = ForkManager()
    manager.run()
