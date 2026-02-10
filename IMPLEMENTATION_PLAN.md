# Implementation Plan: Bridging Architecture & Code

This plan adheres to the specific system described by the user, focusing on the missing "Brain" logic and "Executor" components that are currently absent from the codebase.

## 1. Data Layer (The Observer)
**Goal:** Detect opportunities and risks in real-time.

### 1.1. DeFi Llama Integration (New)
*   **Requirement:** Fetch live APY data for lending protocols (Aave, Morpho, Compound).
*   **Implementation:**
    *   Create `src/data/defillama_client.py`.
    *   Implement function `fetch_pool_yields(pool_ids: list)` interacting with `https://yields.llama.fi`.

### 1.2. Chainlink Price Feeds (Safety)
*   **Requirement:** "Circuit Breaker" to bypass logic if stablecoins de-peg (< $0.99).
*   **Implementation:**
    *   **Off-chain:** Update `src/ingestion/live_collector.py` to poll Chainlink price feeds via RPC (reading `latestRoundData`).
    *   **On-chain:** (See Execution Layer) for ultimate safety.

### 1.3. Envio HyperSync (Optional/Later)
*   *Note: The Graph client already exists. Envio is faster but requires new infrastructure. For MVP, we can stick to The Graph unless millisecond latency is critical immediately.*
*   **Action:** Stick to `src/data/graph_client.py` for now to save setup time, but optimize query intervals.

---

## 2. Intelligence Engine (The Brain)
**Goal:** Process data and issue commands.

### 2.1. FastAPI Backend (The Core)
*   **Requirement:** Serve as the central nervous system.
*   **Implementation:**
    *   Create `src/api/server.py` (FastAPI app).
    *   Endpoints: `/health`, `/inference/predict` (connects to L3), `/admin/rebalance`.

### 2.2. Logic Filters (The "Why")
*   **Filter 1: Trend vs. Mean Reversion**
    *   Update `src/ml/yield_predictor.py`.
    *   Add a post-processing layer: If `predicted_apy > current_apy` BUT `model_confidence < threshold`, flag as "Mean Reversion" risk.
*   **Filter 2: Liquidity Check (Vampire Attack)**
    *   Create `src/risk/liquidity.py`.
    *   Logic: Fetch pool depth (from RPC/1inch API); if `trade_size > 0.1% * pool_depth`, reject.
*   **Filter 3: Gas-Aware Optimizer**
    *   Create `src/optimizer/gas.py`.
    *   Logic: Fetch `eth_gasPrice`. Calculate `(APY_diff * Capital) - (Gas * 10)`. Return `True/False`.

---

## 3. Execution Layer (On-Chain)
**Goal:** Execute trades safely.

### 3.1. StrategyHub.sol (Missing Contract)
*   **Requirement:** The modular contract that talks to Aave, Curve, etc.
*   **Implementation:**
    *   Create `contracts/src/StrategyHub.sol`.
    *   **Roles:** Only accepts txs from `AI_RELAYER` address.
    *   **Functions:** `executeRebalance(address[] targets, bytes[] calldata)`, `emergencyWithdraw()`.
    *   **Adapters:** Interfaces for AaveV3 (`supply`, `withdraw`), Curve (`exchange`).

### 3.2. AI Relayer (The Hand)
*   **Requirement:** Secure wallet controlled by FastAPI to sign txs.
*   **Implementation:**
    *   Create `src/execution/relayer.py`.
    *   Use `web3.py`. Load private key from secure env (or AWS KMS later).
    *   Function `submit_tx(calldata)`: Simulates via Tenderly/Alchemy, then broadcasts.

---

## 4. Execution Roadmap

1.  **Step 1**: Write `StrategyHub.sol` (Blocker for on-chain testing).
2.  **Step 2**: Implement `defillama_client.py` (easiest data win).
3.  **Step 3**: Scaffold `src/api/server.py` to link Data -> ML -> Decision.
4.  **Step 4**: Implement the 3 Filters Logic (Python).
5.  **Step 5**: Write `relayer.py` and integrate with `StrategyHub`.
