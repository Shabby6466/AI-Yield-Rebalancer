"""
Keeper Service - Automated ML-Driven Rebalancing
Runs ML predictions on schedule and executes rebalancing
"""

import os
import sys
import json
import time
import argparse
import schedule
from datetime import datetime
from typing import List, Dict
import logging
from dotenv import load_dotenv

# Import ML service
from ml_prediction_service import MLPredictionService
from contract_manager import ContractManager

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/keeper_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class KeeperService:
    """Automated keeper for ML-driven rebalancing"""
    
    def __init__(self, network: str = "base_sepolia", interval_minutes: int = 5):
        """
        Initialize keeper service
        
        Args:
            network: Network to operate on
            interval_minutes: How often to check for rebalancing (matches REBALANCE_FREQUENCY)
        """
        self.network = network
        self.interval_minutes = interval_minutes
        self.ml_service = MLPredictionService(network)
        self.contract_manager = ContractManager(network)
        
        # Load pool configuration
        self.pools = self._load_pool_config()
        
        logger.info(f"Keeper Service initialized for {network}")
        logger.info(f"Rebalance interval: {interval_minutes} minutes")
        logger.info(f"Monitoring {len(self.pools)} pools")
    
    def _load_pool_config(self) -> List[Dict]:
        """Load pool configuration from deployment"""
        try:
            # Load correct deployment file based on network
            deployment_file = f'deployments/{self.network}_deployment.json'
            with open(deployment_file, 'r') as f:
                deployment = json.load(f)
            
            # Network-specific asset configuration
            if self.network == 'base_sepolia':
                pools = [
                    {
                        'asset': '0x4200000000000000000000000000000000000006',  # WETH on Base Sepolia
                        'asset_name': 'WETH',
                        'adapters': [
                            {
                                'address': deployment['contracts']['AaveAdapter'],
                                'protocol': 'Aave'
                            }
                        ]
                    }
                ]
            else:  # sepolia or other networks
                pools = [
                    {
                        'asset': '0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238',  # USDC on Sepolia
                        'asset_name': 'USDC',
                        'adapters': [
                            {
                                'address': deployment['contracts']['AaveAdapter'],
                                'protocol': 'Aave'
                            }
                        ]
                    }
                ]
            
            logger.info(f"Loaded {len(pools)} asset pools")
            return pools
            
        except Exception as e:
            logger.error(f"Failed to load pool config: {e}")
            return []
    
    def update_predictions(self):
        """Update ML predictions for all pools"""
        logger.info(f"\n{'='*60}")
        logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running ML Prediction Update")
        logger.info(f"{'='*60}\n")
        
        try:
            # Collect all pool/asset pairs
            pool_pairs = []
            for pool_config in self.pools:
                asset = pool_config['asset']
                for adapter in pool_config['adapters']:
                    pool_pairs.append((adapter['address'], asset))
            
            # Generate and update predictions
            success = self.ml_service.update_pool_predictions(pool_pairs)
            
            if success:
                logger.info("✅ Prediction update successful")
            else:
                logger.error("❌ Prediction update failed")
            
            return success
            
        except Exception as e:
            logger.error(f"Prediction update error: {e}")
            return False
    
    def check_and_rebalance(self):
        """Check if rebalancing needed and execute"""
        logger.info(f"\n{'='*60}")
        logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking Rebalancing Status")
        logger.info(f"{'='*60}\n")
        
        try:
            vault = self.contract_manager.contracts.get('YieldVault')
            
            if not vault:
                logger.error("YieldVault contract not available")
                return False
            
            # Check if rebalancing is needed (cooldown passed)
            last_rebalance = vault.functions.lastRebalanceTime().call()
            rebalance_frequency = vault.functions.REBALANCE_FREQUENCY().call()
            current_time = int(time.time())
            
            time_since_rebalance = current_time - last_rebalance
            time_remaining = rebalance_frequency - time_since_rebalance
            
            logger.info(f"Last rebalance: {datetime.fromtimestamp(last_rebalance).strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"Time since rebalance: {time_since_rebalance}s ({time_since_rebalance // 60} minutes)")
            logger.info(f"Rebalance frequency: {rebalance_frequency}s ({rebalance_frequency // 60} minutes)")
            
            if time_since_rebalance < rebalance_frequency:
                logger.info(f"⏰ Cooldown active - {time_remaining}s ({time_remaining // 60} minutes) remaining")
                logger.info(f"Next rebalance available at: {datetime.fromtimestamp(last_rebalance + rebalance_frequency).strftime('%Y-%m-%d %H:%M:%S')}")
                return False
            
            logger.info("✅ Cooldown passed - rebalancing available")
            
            # Get optimal allocation from ML
            for pool_config in self.pools:
                asset = pool_config['asset']
                asset_name = pool_config['asset_name']
                adapters = [a['address'] for a in pool_config['adapters']]
                
                logger.info(f"\nCalculating optimal allocation for {asset_name}...")
                allocation = self.ml_service.get_optimal_allocation(asset, adapters)
                
                # Execute rebalancing
                logger.info(f"\nExecuting rebalancing for {asset_name}...")
                success = self._execute_rebalance(adapters, allocation)
                
                if success:
                    logger.info(f"✅ Rebalancing successful for {asset_name}")
                else:
                    logger.error(f"❌ Rebalancing failed for {asset_name}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Rebalancing check error: {e}")
            return False
    
    def _execute_rebalance(self, pool_addresses: List[str], allocations: Dict[str, int]) -> bool:
        """
        Execute rebalancing transaction
        
        Args:
            pool_addresses: List of pool adapter addresses
            allocations: Dictionary of pool_address → allocation_percentage
            
        Returns:
            True if successful
        """
        try:
            vault = self.contract_manager.contracts.get('YieldVault')
            
            # Prepare transaction data
            allocation_array = [allocations.get(addr, 0) for addr in pool_addresses]
            
            logger.info(f"\nRebalancing to:")
            for i, addr in enumerate(pool_addresses):
                logger.info(f"  Pool {addr[:10]}... → {allocation_array[i]}%")
            
            # Build transaction
            tx = vault.functions.rebalance(
                pool_addresses,
                allocation_array
            ).build_transaction({
                'from': self.contract_manager.account.address,
                'nonce': self.contract_manager.w3.eth.get_transaction_count(
                    self.contract_manager.account.address
                ),
                'gas': 500000,
                'gasPrice': self.contract_manager.w3.eth.gas_price
            })
            
            # Sign and send
            signed_tx = self.contract_manager.account.sign_transaction(tx)
            tx_hash = self.contract_manager.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logger.info(f"\nRebalance transaction sent: {tx_hash.hex()}")
            logger.info("Waiting for confirmation...")
            
            # Wait for confirmation
            receipt = self.contract_manager.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
            
            if receipt['status'] == 1:
                gas_used = receipt['gasUsed']
                gas_cost = gas_used * tx['gasPrice'] / 1e18
                
                logger.info(f"\n✅ Rebalancing successful!")
                logger.info(f"Gas used: {gas_used:,}")
                logger.info(f"Gas cost: {gas_cost:.6f} ETH")
                logger.info(f"Transaction: https://{self.network}.etherscan.io/tx/{tx_hash.hex()}")
                return True
            else:
                logger.error("❌ Rebalancing transaction failed")
                return False
                
        except Exception as e:
            logger.error(f"Rebalancing execution error: {e}")
            return False
    
    def run_cycle(self):
        """Run one complete keeper cycle"""
        logger.info(f"\n{'#'*60}")
        logger.info(f"KEEPER CYCLE START - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'#'*60}\n")
        
        # Step 1: Update predictions
        logger.info("Step 1: Updating ML predictions...")
        pred_success = self.update_predictions()
        
        if not pred_success:
            logger.warning("Prediction update failed - skipping rebalancing")
            return
        
        # Wait a bit for transaction to propagate
        time.sleep(5)
        
        # Step 2: Check and execute rebalancing
        logger.info("\nStep 2: Checking rebalancing status...")
        rebalance_success = self.check_and_rebalance()
        
        logger.info(f"\n{'#'*60}")
        logger.info(f"KEEPER CYCLE END - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Status: {'SUCCESS' if (pred_success and rebalance_success) else 'PARTIAL' if pred_success else 'FAILED'}")
        logger.info(f"{'#'*60}\n")
    
    def start(self):
        """Start the keeper service"""
        logger.info(f"\n{'#'*60}")
        logger.info("KEEPER SERVICE STARTING")
        logger.info(f"{'#'*60}")
        logger.info(f"Network: {self.network}")
        logger.info(f"Interval: {self.interval_minutes} minutes")
        logger.info(f"Wallet: {self.contract_manager.account.address}")
        logger.info(f"Monitoring {len(self.pools)} pools")
        logger.info(f"{'#'*60}\n")
        
        # Run immediately on start
        logger.info("Running initial cycle...")
        self.run_cycle()
        
        # Schedule periodic runs
        schedule.every(self.interval_minutes).minutes.do(self.run_cycle)
        
        logger.info(f"\n⏰ Keeper scheduled to run every {self.interval_minutes} minutes")
        logger.info("Press Ctrl+C to stop\n")
        
        # Keep running
        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n\nKeeper service stopped by user")
            logger.info("Goodbye! 👋\n")


def main():
    """Run keeper service"""
    parser = argparse.ArgumentParser(description='ML-Driven Keeper Service')
    parser.add_argument(
        '--network',
        type=str,
        default='base_sepolia',
        choices=['sepolia', 'base_sepolia', 'mainnet', 'base'],
        help='Network to operate on'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=5,
        help='Rebalance check interval in minutes'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run once and exit (no scheduling)'
    )
    
    args = parser.parse_args()
    
    # Create logs directory
    os.makedirs('logs', exist_ok=True)
    
    # Initialize keeper
    keeper = KeeperService(
        network=args.network,
        interval_minutes=args.interval
    )
    
    if args.once:
        # Run single cycle
        keeper.run_cycle()
    else:
        # Start continuous service
        keeper.start()


if __name__ == "__main__":
    main()
