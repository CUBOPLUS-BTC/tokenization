# Wallet Service

Bitcoin and Liquid custody, Lightning Network payments, and balance management.

## Responsibility

- HD wallet derivation for confidential Liquid addresses, encrypted seed storage, and custody posture reporting
- gRPC connection to LND for invoice creation, payment, and routing
- Aggregates Liquid on-chain + Lightning + token balances per user
- Exposes hosted fiat-to-BTC on-ramp discovery and external handoff session creation
- Persists every inbound/outbound movement with blockchain proof

## Technology

Python 3.11+ / FastAPI

## Port

`:8001`

## External Dependencies

Bitcoin Core (RPC), LND (gRPC), Elements / Liquid Core (RPC).

## Security

- Key operations are abstracted behind a custody backend so software-managed encryption and HSM-compatible wrapping can share the same business flows.
- Private keys never leave the custody module, and marketplace signer operations use the same abstraction instead of reading raw secrets directly.
- All seed material is wrapped at rest; staging, beta, and production profiles require file-backed custody secrets.
- Wallet operations require JWT + optional 2FA.
- Fiat on-ramp purchases always hand off to an external provider checkout and deliver BTC to a platform-generated on-chain address to preserve wallet compatibility during custody migration.
