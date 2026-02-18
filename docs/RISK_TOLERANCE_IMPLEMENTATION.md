# How Risk Tolerance is Utilized in the System

## Overview
Risk Tolerance is a **numeric multiplier** that adjusts the AI's reward function to penalize or encourage risky allocations.

---

## 1. Configuration & Entry Points

### Environment Variable
```bash
# In .env file
RISK_TOLERANCE=0.1  # 1.0=Balanced, 0.1=Aggressive, 10.0=Conservative

# Interpretation:
# 0.1  = Aggressive (willing to take risks for higher yield)
# 1.0  = Balanced (standard risk/reward tradeoff)
# 10.0 = Conservative (heavily penalizes risky pools)
```

### Dashboard Control
```python
# In dashboard/app.py (line 221)
with st.sidebar.expander("Risk Parameters", expanded=False):
    risk_tol = st.slider("Risk Tolerance", 0.1, 1.0, 0.8)
    # Slider: 0.1 (left) ← → 1.0 (right)
    # User can adjust on-the-fly
    
    min_gain = st.slider("Min Yield Gain (%)", 0.0, 10.0, 1.0)
    max_slip = st.slider("Max Slippage (%)", 0.1, 5.0, 2.0)
    max_gas = st.number_input("Max Gas (Gwei)", 10, 500, 50)
    
    if st.button("Update Config"):
        st.success("Config Updated")
```

### Core Service Initialization
```python
# In src/core/rebalancer_service.py (line 98)
self.risk_tolerance = float(os.getenv("RISK_TOLERANCE", 1.0))
logger.info(f"Using Risk Tolerance: {self.risk_tolerance} (1.0=Balanced, <1.0=Aggressive)")

# Passed to the RL Environment
self.env = DefiRebalanceEnv(
    pool_data=[],
    risk_tolerance=self.risk_tolerance,  # ← Used here
    max_pools=10
)
```

---

## 2. RL Agent Implementation

### The Reward Function (src/ml/rl_agent.py, line 100-120)

```python
def _calculate_reward(self, weights: np.ndarray, obs: np.ndarray) -> float:
    """
    Reward = Yield - Risk Penalty - Gas Cost + Diversification Bonus
    """
    
    apys = obs[:, 0]  # APY of each pool
    risk_scores = obs[:, 11]  # Risk Score (0-100) for each pool
    
    # 1. PREDICTED YIELD (positive reward)
    predicted_yield = np.sum(weights * apys)
    # Example: [30% in pool1(5%), 70% in pool2(8%)]
    # = 0.3*5 + 0.7*8 = 6.95%
    
    # 2. RISK PENALTY (negative reward scaled by risk_tolerance)
    risk_penalty = np.sum(weights * (risk_scores / 100) ** 2) * 10.0 * self.risk_tolerance
    #                                                               ^^^^^^^^^^^^^^^^^^^^^^^^
    #                                          THIS IS THE KEY LINE - applies risk_tolerance
    
    # Example breakdown:
    # Pool1: weight=0.3, risk=20 → penalty = 0.3 * (20/100)^2 * 10.0 * risk_tol
    #      = 0.3 * 0.04 * 10.0 * 0.1 = 0.012 (if aggressive)
    #      = 0.3 * 0.04 * 10.0 * 1.0 = 0.120 (if balanced)
    
    # Pool2: weight=0.7, risk=80 → penalty = 0.7 * (80/100)^2 * 10.0 * risk_tol
    #      = 0.7 * 0.64 * 10.0 * 0.1 = 0.448 (if aggressive)
    #      = 0.7 * 0.64 * 10.0 * 1.0 = 4.480 (if balanced)
    
    # 3. CHURN PENALTY (approximates gas cost)
    churn_penalty = 0.5  # Cost of rebalancing
    
    # 4. DIVERSIFICATION BONUS
    diversification_bonus = -np.sum(weights * np.log(weights + 1e-8)) * 0.1
    
    # FINAL REWARD
    reward = predicted_yield - risk_penalty - churn_penalty + diversification_bonus
    
    return reward
```

### Impact Visualization

**Scenario**: Choosing between two portfolios

```
PORTFOLIO A: 100% in high-risk pool (APY: 50%, Risk Score: 90)
PORTFOLIO B: 70% safe (APY: 5%) + 30% risky (APY: 50%, Risk: 90)

AGGRESSIVE MODE (risk_tol = 0.1):
─────────────────────────────────
Portfolio A:
  Yield: 50% * 1.0 = 50%
  Risk Penalty: (90/100)^2 * 10.0 * 0.1 = 0.81
  Reward: 50 - 0.81 = 49.19 ✅ SELECTED

Portfolio B:
  Yield: 70% * 5% + 30% * 50% = 19%
  Risk Penalty: (5/100)^2 * 10 * 0.1 * 0.7 + (90/100)^2 * 10 * 0.1 * 0.3
              = 0.00035 + 0.243 = 0.24
  Reward: 19 - 0.24 = 18.76 ❌ NOT SELECTED


BALANCED MODE (risk_tol = 1.0):
─────────────────────────────────
Portfolio A:
  Yield: 50%
  Risk Penalty: 0.81 * 10 = 8.1
  Reward: 50 - 8.1 = 41.9 ❌ NOT SELECTED (too risky)

Portfolio B:
  Yield: 19%
  Risk Penalty: 0.24 * 10 = 2.4
  Reward: 19 - 2.4 = 16.6 ✅ SELECTED (more balanced)
```

---

## 3. How the AI Uses Risk Tolerance

### During Training
```python
# The PPO (Proximal Policy Optimization) agent learns:
# "When risk_tolerance=0.1, allocate aggressively in high-yield pools"
# "When risk_tolerance=1.0, prefer diversified, lower-risk pools"

agent = PPORebalancer(env)
agent.train(timesteps=100000)  # Learns with current risk_tolerance setting
```

### During Inference (Rebalancing Decision)
```python
# When making allocation decisions:
current_pools = get_top_pools()  # Fetch current pools
observations = feature_engineer(current_pools)  # Create 32-dim features

# Agent predicts weights based on learned policy
# Policy has learned to adjust allocations based on risk_tolerance
weights = agent.predict(observations)

# If risk_tol is high → weights favor low-risk, stable pools
# If risk_tol is low → weights concentrate in high-yield pools (higher risk)
```

---

## 4. Real-World Execution Flow

### Step 1: Read Configuration
```python
# src/core/rebalancer_service.py (line 98)
risk_tolerance = float(os.getenv("RISK_TOLERANCE", 1.0))
# → 0.1 (aggressive), 1.0 (balanced), or 10.0 (conservative)
```

### Step 2: Initialize RL Environment
```python
# Pass risk_tolerance to environment
env = DefiRebalanceEnv(
    pool_data=pools,
    risk_tolerance=risk_tolerance,  # ← Stored in env
    max_pools=10
)

# Environment uses it in reward calculations
```

### Step 3: Train/Load Agent
```python
brain = PPORebalancer(env)
if os.path.exists("models/ppo_rebalancer_v1.zip"):
    brain.load("models/ppo_rebalancer_v1.zip")
    # Loaded agent already trained with this risk_tolerance
```

### Step 4: Make Decision
```python
# Get current pool observations
obs = env._get_observation(current_step)

# Predict allocation weights
action = brain.predict(obs)

# Normalize to sum to 1
weights = action / np.sum(action)

# Apply weights to pools
allocations = weights * portfolio_value
```

### Step 5: Calculate Reward
```python
# Environment calculates reward with risk_tolerance applied
reward = env._calculate_reward(weights, next_obs)
# This reward includes risk penalty scaled by risk_tolerance
```

---

## 5. Configuration in Dashboard

### User Updates
```python
# User adjusts slider in sidebar
risk_tol = st.slider("Risk Tolerance", 0.1, 1.0, 0.8)
#                                      min  max  default

# When user clicks "Update Config"
if st.button("Update Config"):
    # Update environment
    env.risk_tolerance = risk_tol
    
    # Alternatively, update .env file for persistence
    with open('.env', 'w') as f:
        f.write(f"RISK_TOLERANCE={risk_tol}")
    
    st.success("Config Updated")
```

---

## 6. Complete Data Flow Diagram

```
┌─────────────────────┐
│  User Input         │
│  Risk Tolerance     │
│  (Dashboard: 0-1)   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Environment Variable           │
│  RISK_TOLERANCE=0.1/1.0/10.0   │
│  (In .env file)                │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  RebalancerService.__init__()   │
│  self.risk_tolerance =          │
│  float(os.getenv(...))          │
└──────────┬──────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  DefiRebalanceEnv               │
│  env.risk_tolerance = 0.1       │
│  (Stored in environment)        │
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────┐
│  _calculate_reward() Method                      │
│                                                  │
│  risk_penalty = ... * 10.0 * self.risk_tolerance│
│                              ^^^^^^^^^^^^^^^^^  │
│  (Risk penalty scaled by this multiplier)       │
└──────────┬───────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────┐
│  PPO Agent Training/Inference                    │
│                                                  │
│  Learns to maximize reward with given            │
│  risk_tolerance setting                          │
│                                                  │
│  Outputs: allocation weights                    │
│  [0.3, 0.4, 0.2, 0.1]                           │
└──────────┬───────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────┐
│  Rebalance Execution                             │
│                                                  │
│  Apply weights to actual portfolio:              │
│  Pool A: 30% of capital                         │
│  Pool B: 40% of capital                         │
│  Pool C: 20% of capital                         │
│  Pool D: 10% of capital                         │
│                                                  │
│  (Allocation respects risk_tolerance setting)   │
└──────────────────────────────────────────────────┘
```

---

## 7. Key Takeaway

Risk Tolerance acts as a **scaling factor for risk penalties** in the reward function:

| Setting | Interpretation | Risk Penalty Multiplier | Expected Behavior |
|---------|-----------------|------------------------|-------------------|
| **0.1** | Aggressive | 1.0x (low) | Chases high yields, accepts risky pools |
| **0.5** | Moderate | 5.0x (medium) | Balances yield vs safety |
| **1.0** | Balanced | 10.0x (high) | Prefers safe, stable yields |
| **10.0** | Conservative | 100x (very high) | Heavily penalizes any risky allocation |

**Formula**: `Final Reward = Yield - (Risk Penalty × risk_tolerance) - Gas + Diversification`

The higher the `risk_tolerance` multiplier, the **more heavily** the AI penalizes allocating capital to risky protocols, resulting in more conservative portfolios.

---

## 8. Practical Example

### Configuration A (Aggressive)
```python
RISK_TOLERANCE = 0.1

# Agent chooses:
Portfolio = {
    "High-APY-High-Risk": 0.6,    # 60% 
    "Med-APY-Med-Risk": 0.3,       # 30%
    "Low-APY-Safe": 0.1            # 10%
}
# Reason: Low risk_tolerance minimizes penalty for risky allocations
```

### Configuration B (Conservative)
```python
RISK_TOLERANCE = 10.0

# Agent chooses:
Portfolio = {
    "High-APY-High-Risk": 0.1,    # 10%
    "Med-APY-Med-Risk": 0.3,       # 30%
    "Low-APY-Safe": 0.6            # 60%
}
# Reason: High risk_tolerance heavily penalizes risky allocations
```

Both configurations aim to maximize reward, but risk_tolerance fundamentally changes what the agent considers "maximum."
