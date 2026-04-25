#!/bin/sh
set -eu

DATA_DIR="${ELEMENTS_DATA_DIR:-/data/elements}"
CONF_FILE="${ELEMENTS_CONF_FILE:-/etc/elements/elements.conf}"
NETWORK="${ELEMENTS_NETWORK:-elementsregtest}"
RPC_USER="${ELEMENTS_RPC_USER:-user}"
RPC_PASSWORD="${ELEMENTS_RPC_PASSWORD:-pass}"
RPC_PORT="${ELEMENTS_RPC_PORT:-7041}"
WALLET_NAME="${ELEMENTS_WALLET_NAME:-platform}"
BOOTSTRAP_MARKER="${DATA_DIR}/.${WALLET_NAME}.bootstrapped"

cli() {
  elements-cli -chain="${NETWORK}" -rpcuser="${RPC_USER}" -rpcpassword="${RPC_PASSWORD}" -rpcport="${RPC_PORT}" "$@"
}

shutdown() {
  if [ -n "${ELEMENTS_PID:-}" ] && kill -0 "${ELEMENTS_PID}" >/dev/null 2>&1; then
    kill "${ELEMENTS_PID}"
    wait "${ELEMENTS_PID}" || true
  fi
}

trap shutdown INT TERM

mkdir -p "${DATA_DIR}"

elementsd \
  -chain="${NETWORK}" \
  -datadir="${DATA_DIR}" \
  -conf="${CONF_FILE}" \
  -validatepegin=0 \
  -server=1 \
  -txindex=1 \
  -fallbackfee=0.00001 \
  -rpcbind=0.0.0.0 \
  -rpcallowip=0.0.0.0/0 \
  -rpcport="${RPC_PORT}" \
  -rpcuser="${RPC_USER}" \
  -rpcpassword="${RPC_PASSWORD}" \
  -printtoconsole &
ELEMENTS_PID=$!

until cli getblockchaininfo >/dev/null 2>&1; do
  if ! kill -0 "${ELEMENTS_PID}" >/dev/null 2>&1; then
    wait "${ELEMENTS_PID}"
    exit 1
  fi
  sleep 2
done

if [ -n "${WALLET_NAME}" ]; then
  if ! cli -rpcwallet="${WALLET_NAME}" getwalletinfo >/dev/null 2>&1; then
    cli loadwallet "${WALLET_NAME}" >/dev/null 2>&1 || \
    cli createwallet "${WALLET_NAME}" >/dev/null 2>&1 || true
  fi
fi

if [ "${NETWORK}" = "elementsregtest" ] && [ -n "${WALLET_NAME}" ] && [ ! -f "${BOOTSTRAP_MARKER}" ]; then
  MINE_ADDRESS="$(cli -rpcwallet="${WALLET_NAME}" getnewaddress "bootstrap" "bech32" 2>/dev/null || true)"
  if [ -n "${MINE_ADDRESS}" ]; then
    cli -rpcwallet="${WALLET_NAME}" generatetoaddress 101 "${MINE_ADDRESS}" >/dev/null 2>&1 || true
    touch "${BOOTSTRAP_MARKER}"
  fi
fi

wait "${ELEMENTS_PID}"
