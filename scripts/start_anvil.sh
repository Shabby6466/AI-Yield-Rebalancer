#!/bin/bash
# start_anvil.sh
echo "Killing any existing anvil processes..."
pkill anvil || true
sleep 2

echo "Starting Anvil with Alchemy..."
RPC_URL="https://eth-mainnet.g.alchemy.com/v2/bckJfvBFZyIs8OKOXSpwi"
BLOCK_ARG=""
if [ ! -z "$1" ]; then
  BLOCK_ARG="--fork-block-number $1"
  echo "Forking at block: $1"
fi
/Users/Akmal/.foundry/bin/anvil --fork-url $RPC_URL --port 8545 --host 127.0.0.1 --fork-retry-backoff 3000 $BLOCK_ARG > /Users/Akmal/Desktop/projects/defi\ rebalancing/AI-Yield-Rebalancer/contracts/anvil_run.log 2>&1 &

echo "Waiting for Anvil to start..."
MAX_RETRIES=60
COUNT=0
while ! curl -s -m 2 -H "Content-Type: application/json" -X POST --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' http://127.0.0.1:8545 | grep -q "result"; do
    sleep 2
    COUNT=$((COUNT+1))
    echo "Still waiting... ($COUNT/$MAX_RETRIES)"
    if [ $COUNT -ge $MAX_RETRIES ]; then
        echo "Anvil failed to start after $((MAX_RETRIES*2)) seconds."
        cat /Users/Akmal/Desktop/projects/defi\ rebalancing/AI-Yield-Rebalancer/contracts/anvil_run.log
        exit 1
    fi
done

echo "Anvil is ready at http://127.0.0.1:8545"
exit 0
