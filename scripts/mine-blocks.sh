#!/usr/bin/env bash
set -euo pipefail

# This script mines blocks on the local regtest node.
# Usage: ./mine-blocks.sh [number_of_blocks]
# Default is 1 block.

BLOCKS="${1:-1}"
CONTAINER="${BTC_CONTAINER:-tokenization-local-bitcoind}"

# Ensure a wallet is loaded
if ! docker exec "$CONTAINER" bitcoin-cli -regtest -rpcuser=local_rpc -rpcpassword=local_rpc_password listwallets | grep -q "default"; then
  echo "No 'default' wallet found, creating one..."
  docker exec "$CONTAINER" bitcoin-cli -regtest -rpcuser=local_rpc -rpcpassword=local_rpc_password createwallet "default" > /dev/null
fi

# Create or reuse a mining address
ADDR=$(docker exec "$CONTAINER" bitcoin-cli -regtest -rpcuser=local_rpc -rpcpassword=local_rpc_password getnewaddress "mine" "bech32")
docker exec "$CONTAINER" bitcoin-cli -regtest -rpcuser=local_rpc -rpcpassword=local_rpc_password generatetoaddress "$BLOCKS" "$ADDR"

echo "Mined $BLOCKS block(s) to $ADDR"
