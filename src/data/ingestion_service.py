"""
Data Ingestion Service
Aggregates data from The Graph, Alchemy RPC
Uses Graph for Uniswap V3, RPC for Aave V3 and Curve Finance
"""

import logging
import asyncio
import psycopg2
from typing import Dict, List, Optional, Any
from datetime import datetime
import os
from dotenv import load_dotenv

from .graph_client import GraphClient
from .alchemy_client import AlchemyClient
from .defillama_client import DefiLlamaClient
from .rpc_collectors import AaveV3Collector, CurveFinanceCollector

logger = logging.getLogger(__name__)
load_dotenv()


class DataAggregator:
    """Main service for data collection and aggregation"""

    def __init__(
        self,
        db_url: str,
        alchemy_api_key: str,
        graph_api_key: Optional[str] = None,
    ):
        """
        Initialize data aggregator

        Args:
            db_url: PostgreSQL connection string
            alchemy_api_key: Alchemy API key
            graph_api_key: The Graph API key (optional, loaded from env)
        """
        self.db_url = db_url
        self.graph_client = GraphClient(api_key=graph_api_key)
        self.alchemy_client = AlchemyClient(api_key=alchemy_api_key, network="eth-mainnet")
        self.defillama_client = DefiLlamaClient()
        
        # RPC-based collectors
        self.aave_collector = AaveV3Collector(self.alchemy_client)
        self.curve_collector = CurveFinanceCollector(self.alchemy_client)

    def get_db_connection(self):
        """Get database connection"""
        return psycopg2.connect(self.db_url)

    async def collect_aave_data(self) -> Dict[str, Any]:
        """Collect Aave V3 data via RPC"""
        logger.info("Collecting Aave V3 data...")
        try:
            markets = await self.aave_collector.collect_all_markets()

            return {
                "protocol": "aave",
                "markets": markets,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error collecting Aave data: {e}")
            raise

    async def collect_uniswap_data(self) -> Dict[str, Any]:
        """Collect Uniswap V3 data from The Graph"""
        logger.info("Collecting Uniswap V3 data...")
        try:
            pools = await self.graph_client.get_uniswap_pools(first=50)
            protocol = await self.graph_client.get_uniswap_protocol_data()

            # normalize pools: Graph client may return either a dict with "pools" key or a list
            if isinstance(pools, dict):
                pools_list = pools.get("pools", [])
            else:
                pools_list = pools or []

            return {
                "protocol": "uniswap",
                "pools": pools_list,
                "protocol_data": protocol,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error collecting Uniswap data: {e}")
            raise

    async def collect_curve_data(self) -> Dict[str, Any]:
        """Collect Curve Finance data via RPC"""
        logger.info("Collecting Curve Finance data...")
        try:
            pools = await self.curve_collector.collect_all_pools()

            return {
                "protocol": "curve",
                "pools": pools,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error collecting Curve data: {e}")
            raise

    async def collect_defillama_data(self) -> Dict[str, Any]:
        """Collect yield data across multiple protocols from DeFiLlama"""
        logger.info("Collecting DeFiLlama yield data...")
        try:
            # Fetch top stablecoin pools (default filter)
            top_pools = await self.defillama_client.fetch_top_pools()
            
            return {
                "source": "defillama",
                "pools": top_pools,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error collecting DeFiLlama data: {e}")
            raise

    async def collect_all_protocol_data(self) -> Dict[str, Any]:
        """
        Collect data from all three protocols in parallel

        Returns:
            Aggregated protocol data
        """
        logger.info("Starting data collection from all protocols...")

        aave, uniswap, curve, defillama = await asyncio.gather(
            self.collect_aave_data(),
            self.collect_uniswap_data(),
            self.collect_curve_data(),
            self.collect_defillama_data(),
            return_exceptions=True,
        )

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "aave": aave if not isinstance(aave, Exception) else {"error": str(aave)},
            "uniswap": (
                uniswap if not isinstance(uniswap, Exception) else {"error": str(uniswap)}
            ),
            "curve": curve if not isinstance(curve, Exception) else {"error": str(curve)},
            "defillama": defillama if not isinstance(defillama, Exception) else {"error": str(defillama)},
        }

    # =========================================================================
    # Database Operations
    # =========================================================================

    def insert_protocol_yields(
        self,
        protocol_name: str,
        yields_data: List[Dict[str, Any]],
    ) -> int:
        """
        Insert protocol yield data into database

        Args:
            protocol_name: Name of protocol ('aave', 'uniswap', 'curve')
            yields_data: List of yield records

        Returns:
            Number of records inserted
        """
        conn = self.get_db_connection()
        cur = conn.cursor()

        try:
            # Map data based on protocol
            if protocol_name == "aave":
                for reserve in yields_data:
                    cur.execute(
                        """
                        INSERT INTO protocol_yields
                        (protocol_id, asset, apy_percent, total_liquidity_usd,
                         available_liquidity_usd, utilization_ratio, variable_borrow_rate)
                        VALUES (
                            (SELECT id FROM protocols WHERE name = 'Aave V3'),
                            %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (protocol_id, asset, recorded_at) DO UPDATE
                        SET apy_percent = EXCLUDED.apy_percent,
                            total_liquidity_usd = EXCLUDED.total_liquidity_usd,
                            available_liquidity_usd = EXCLUDED.available_liquidity_usd,
                            utilization_ratio = EXCLUDED.utilization_ratio,
                            variable_borrow_rate = EXCLUDED.variable_borrow_rate
                        """,
                        (
                            reserve.get("symbol", ""),
                            float(reserve.get("liquidityRate", 0)) / 1e27 * 100,  # Convert to APY %
                            float(reserve.get("totalLiquidity", 0)),
                            float(reserve.get("availableLiquidity", 0)),
                            float(reserve.get("utilizationRate", 0)),
                            float(reserve.get("variableBorrowRate", 0)),
                        ),
                    )

            elif protocol_name == "uniswap":
                for pool in yields_data:
                    # For Uniswap, we track volume and liquidity metrics
                    cur.execute(
                        """
                        INSERT INTO protocol_yields
                        (protocol_id, asset, apy_percent, total_liquidity_usd, volume_24h_usd)
                        VALUES (
                            (SELECT id FROM protocols WHERE name = 'Uniswap V3'),
                            %s, %s, %s, %s
                        )
                        ON CONFLICT (protocol_id, asset, recorded_at) DO UPDATE
                        SET total_liquidity_usd = EXCLUDED.total_liquidity_usd,
                            volume_24h_usd = EXCLUDED.volume_24h_usd
                        """,
                        (
                            f"{pool.get('token0', {}).get('symbol', '')}/"
                            f"{pool.get('token1', {}).get('symbol', '')}",
                            0,  # Uniswap V3 has variable APY based on fees
                            float(pool.get("totalValueLockedUSD", 0)),
                            float(pool.get("volumeUSD", 0)),
                        ),
                    )

            elif protocol_name == "curve":
                for pool in yields_data:
                    cur.execute(
                        """
                        INSERT INTO protocol_yields
                        (protocol_id, asset, apy_percent, total_liquidity_usd, volume_24h_usd)
                        VALUES (
                            (SELECT id FROM protocols WHERE name = 'Curve Finance'),
                            %s, %s, %s, %s
                        )
                        ON CONFLICT (protocol_id, asset, recorded_at) DO UPDATE
                        SET apy_percent = EXCLUDED.apy_percent,
                            total_liquidity_usd = EXCLUDED.total_liquidity_usd,
                            volume_24h_usd = EXCLUDED.volume_24h_usd
                        """,
                        (
                            pool.get("name", ""),
                            float(pool.get("baseYield", 0)) * 100 if pool.get("baseYield") else 0,
                            float(pool.get("totalValueLockedUSD", 0)),
                            float(pool.get("cumulativeVolumeUSD", 0)),
                        ),
                    )
            
            elif protocol_name == "defillama":
                for pool in yields_data:
                    # Map DeFiLlama project name to our protocol names if possible, 
                    # otherwise skip or handle gracefully.
                    # For POC, ensure protocol exists.
                    cur.execute("SELECT id FROM protocols WHERE name ILIKE %s", (f"%{pool.get('project', '')}%",))
                    protocol_id_row = cur.fetchone()
                    
                    if protocol_id_row:
                        protocol_id = protocol_id_row[0]
                        cur.execute(
                            """
                            INSERT INTO protocol_yields
                            (protocol_id, asset, apy_percent, total_liquidity_usd, volume_24h_usd)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (protocol_id, asset, recorded_at) DO UPDATE
                            SET apy_percent = EXCLUDED.apy_percent,
                                total_liquidity_usd = EXCLUDED.total_liquidity_usd
                            """,
                            (
                                protocol_id,
                                pool.get("symbol", "unknown"),
                                float(pool.get("apy", 0)),
                                float(pool.get("tvlUsd", 0)),
                                0 # Volume not always available in same structure
                            ),
                        )

            conn.commit()
            inserted = cur.rowcount
            logger.info(f"Inserted {inserted} records for {protocol_name}")
            return inserted

        except Exception as e:
            conn.rollback()
            logger.error(f"Error inserting {protocol_name} data: {e}")
            raise
        finally:
            cur.close()
            conn.close()

    def insert_risk_scores(
        self,
        protocol_name: str,
        risk_data: Dict[str, Any],
    ) -> int:
        """
        Insert or update protocol risk scores

        Args:
            protocol_name: Name of protocol
            risk_data: Risk score and factors

        Returns:
            Number of records inserted
        """
        conn = self.get_db_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """
                INSERT INTO protocol_risk_scores
                (protocol_id, risk_score, audit_score, exploit_count,
                 security_incidents, contract_age_days)
                VALUES (
                    (SELECT id FROM protocols WHERE name = %s),
                    %s, %s, %s, %s, %s
                )
                ON CONFLICT (protocol_id, recorded_at) DO UPDATE
                SET risk_score = EXCLUDED.risk_score,
                    audit_score = EXCLUDED.audit_score,
                    exploit_count = EXCLUDED.exploit_count,
                    security_incidents = EXCLUDED.security_incidents
                """,
                (
                    protocol_name,
                    risk_data.get("risk_score", 50),
                    risk_data.get("audit_score", 0),
                    risk_data.get("exploit_count", 0),
                    risk_data.get("security_incidents", 0),
                    risk_data.get("contract_age_days", 0),
                ),
            )

            conn.commit()
            inserted = cur.rowcount
            logger.info(f"Inserted risk scores for {protocol_name}")
            return inserted

        except Exception as e:
            conn.rollback()
            logger.error(f"Error inserting risk data: {e}")
            raise
        finally:
            cur.close()
            conn.close()

    def insert_gas_prices(
        self,
        gas_data: Dict[str, float],
    ) -> int:
        """
        Insert current gas price data

        Args:
            gas_data: Gas price metrics

        Returns:
            Number of records inserted
        """
        conn = self.get_db_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """
                INSERT INTO gas_prices
                (chain, safe_gwei, standard_gwei, fast_gwei)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    "ethereum",
                    gas_data.get("safe_gwei", 0),
                    gas_data.get("standard_gwei", 0),
                    gas_data.get("fast_gwei", 0),
                ),
            )

            conn.commit()
            inserted = cur.rowcount
            logger.info("Inserted gas price data")
            return inserted

        except Exception as e:
            conn.rollback()
            logger.error(f"Error inserting gas price data: {e}")
            raise
        finally:
            cur.close()
            conn.close()

    # =========================================================================
    # Full Pipeline
    # =========================================================================

    async def run_collection_pipeline(self) -> Dict[str, Any]:
        """
        Run complete data collection pipeline

        Returns:
            Summary of collection results
        """
        logger.info("Starting full data collection pipeline...")

        start_time = datetime.utcnow()

        try:
            # Collect all protocol data
            all_data = await self.collect_all_protocol_data()

            # Insert Aave data
            if "reserves" in all_data.get("aave", {}):
                self.insert_protocol_yields("aave", all_data["aave"]["reserves"])

            # Insert Uniswap data
            if "pools" in all_data.get("uniswap", {}):
                self.insert_protocol_yields("uniswap", all_data["uniswap"]["pools"])

            # Insert Curve data
            if "pools" in all_data.get("curve", {}):
                self.insert_protocol_yields("curve", all_data["curve"]["pools"])

            # Insert DeFiLlama data
            if "pools" in all_data.get("defillama", {}):
                self.insert_protocol_yields("defillama", all_data["defillama"]["pools"])

            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()

            logger.info(f"Data collection pipeline completed in {duration:.2f}s")

            return {
                "status": "success",
                "duration_seconds": duration,
                "timestamp": end_time.isoformat(),
                "data": all_data,
            }

        except Exception as e:
            logger.error(f"Data collection pipeline failed: {e}")
            raise


# ============================================================================
# Main Entry Point
# ============================================================================


async def main():
    """Main entry point for data collection"""
    # Load configuration from environment
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/defi_yield_db",
    )
    alchemy_key = os.getenv("ALCHEMY_API_KEY", "")
    dune_key = os.getenv("DUNE_API_KEY", "")

    if not alchemy_key:
        logger.error("ALCHEMY_API_KEY not set")
        return

    if not dune_key:
        logger.error("DUNE_API_KEY not set")
        return

    # Initialize aggregator
    aggregator = DataAggregator(db_url, alchemy_key, dune_key)

    # Run pipeline
    result = await aggregator.run_collection_pipeline()
    logger.info(f"Pipeline result: {result}")

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
