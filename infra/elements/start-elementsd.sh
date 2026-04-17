#!/bin/sh
set -eu

DATA_DIR="${ELEMENTS_DATA_DIR:-/data/elements}"
CONF_FILE="${ELEMENTS_CONF_FILE:-/etc/elements/elements.conf}"
NETWORK="${ELEMENTS_NETWORK:-elementsregtest}"
RPC_USER="${ELEMENTS_RPC_USER:-user}"
RPC_PASSWORD="${ELEMENTS_RPC_PASSWORD:-pass}"
RPC_PORT="${ELEMENTS_RPC_PORT:-7041}"
WALLET_NAME="${ELEMENTS_WALLET_NAME:-platform}"
BOOTSTRAP_MARKER="${DATA_DIR}/.${WALLET_NAME:-wallet}.bootstrapped"

cli() {
  elements-cli -chain="${NETWORK}" -rpcuser="${RPC_USER}" -rpcpassword="${RPC_PASSWORD}" -rpcport="${RPC_PORT}" "$@"
}

shutdown() {
  if [ -n "${ELEMENTS_PID:-}" ] && kill -0 "${ELEMENTS_PID}" >/dev/null 2>&1; then
    kill "${ELEMENTS_PID}"
    wait "${ELEMENTS_PID}"
  fi
}

trap shutdown INT TERM

mkdir -p "${DATA_DIR}"
elementsd -datadir="${DATA_DIR}" -conf="${CONF_FILE}" -printtoconsole &
ELEMENTS_PID=$!

until cli getblockchaininfo >/dev/null 2>&1; do
  if ! kill -0 "${ELEMENTS_PID}" >/dev/null 2>&1; then
    wait "${ELEMENTS_PID}"
  fi
  sleep 2
done

if [ -n "${WALLET_NAME}" ] && ! cli -rpcwallet="${WALLET_NAME}" getwalletinfo >/dev/null 2>&1; then
  cli loadwallet "${WALLET_NAME}" >/dev/null 2>&1 || cli createwallet "${WALLET_NAME}" >/dev/null
  cli -rpcwallet="${WALLET_NAME}" rescanblockchain >/dev/null 2>&1 || true
fi

if [ -n "${WALLET_NAME}" ] && [ ! -f "${BOOTSTRAP_MARKER}" ]; then
  MINE_ADDRESS="$(cli -rpcwallet="${WALLET_NAME}" getnewaddress "bootstrap" "bech32")"
  cli -rpcwallet="${WALLET_NAME}" generatetoaddress 101 "${MINE_ADDRESS}" >/dev/null 2>&1 || \
    cli -rpcwallet="${WALLET_NAME}" generate 101 >/dev/null 2>&1 || true
  touch "${BOOTSTRAP_MARKER}"
fi

wait "${ELEMENTS_PID}"
