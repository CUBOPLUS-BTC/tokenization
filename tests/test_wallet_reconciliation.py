from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


for key, value in {
    "SERVICE_NAME": "wallet",
    "SERVICE_PORT": "8001",
    "WALLET_SERVICE_URL": "http://wallet:8001",
    "TOKENIZATION_SERVICE_URL": "http://tokenization:8002",
    "MARKETPLACE_SERVICE_URL": "http://marketplace:8003",
    "NOSTR_SERVICE_URL": "http://nostr:8005",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "testdb",
    "POSTGRES_USER": "user",
    "DATABASE_URL": "postgresql://user:pass@localhost/testdb",
    "REDIS_URL": "redis://localhost:6379/0",
    "BITCOIN_RPC_HOST": "localhost",
    "BITCOIN_RPC_PORT": "18443",
    "BITCOIN_RPC_USER": "bitcoin",
    "BITCOIN_NETWORK": "regtest",
    "LND_GRPC_HOST": "localhost",
    "LND_GRPC_PORT": "10009",
    "LND_MACAROON_PATH": "tests/fixtures/admin.macaroon",
    "LND_TLS_CERT_PATH": "tests/fixtures/tls.cert",
    "TAPD_GRPC_HOST": "localhost",
    "TAPD_GRPC_PORT": "10029",
    "TAPD_MACAROON_PATH": "tests/fixtures/admin.macaroon",
    "TAPD_TLS_CERT_PATH": "tests/fixtures/tls.cert",
    "NOSTR_RELAYS": "wss://relay.example.com",
    "JWT_SECRET": "test-secret",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
    "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
    "TOTP_ISSUER": "Platform",
    "LOG_LEVEL": "INFO",
    "WALLET_ENCRYPTION_KEY": "00" * 32,
}.items():
    os.environ.setdefault(key, value)


from services.wallet import reconciliation as wallet_reconciliation


class _RowStub:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _ResultStub:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, results):
        self._results = list(results)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, statement):
        if not self._results:
            raise AssertionError(f"Unexpected execute() call for statement: {statement}")
        return self._results.pop(0)


class _ConnectContext:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return _ConnectContext(self._conn)


def test_reconcile_deposits_credits_confirmed_utxo_once():
    wallet_id = uuid.uuid4()
    wallet_address_id = uuid.uuid4()
    deposit_id = uuid.uuid4()
    conn = _FakeConn(
        [
            _ResultStub(None),
            _ResultStub(_RowStub(id=deposit_id, wallet_id=wallet_id)),
            _ResultStub(_RowStub(id=deposit_id)),
            _ResultStub(),
            _ResultStub(),
        ]
    )
    engine = _FakeEngine(conn)
    settings = SimpleNamespace(bitcoin_network="regtest")
    rpc = MagicMock()
    rpc.listunspent = AsyncMock(
        return_value=[
            {
                "address": "bcrt1ptestdeposit",
                "txid": "a" * 64,
                "vout": 0,
                "amount": 0.001,
                "confirmations": 1,
            }
        ]
    )

    with (
        patch.object(wallet_reconciliation, "get_bitcoin_rpc", return_value=rpc),
        patch.object(
            wallet_reconciliation,
            "list_imported_wallet_addresses",
            AsyncMock(return_value=[_RowStub(id=wallet_address_id, address="bcrt1ptestdeposit", wallet_id=wallet_id)]),
        ),
        patch.object(wallet_reconciliation, "record_business_event") as record_event,
    ):
        asyncio.run(wallet_reconciliation.reconcile_deposits(engine, settings))

    assert conn.commit.await_count == 2
    record_event.assert_called_once()
    assert record_event.call_args.args[0] == "wallet_onchain_deposit"


def test_reconcile_deposits_does_not_double_credit_existing_utxo():
    wallet_id = uuid.uuid4()
    wallet_address_id = uuid.uuid4()
    conn = _FakeConn(
        [
            _ResultStub(
                _RowStub(
                    id=uuid.uuid4(),
                    wallet_id=wallet_id,
                    wallet_address_id=wallet_address_id,
                    txid="a" * 64,
                    vout=0,
                    amount_sat=100_000,
                    confirmations=2,
                    status="credited",
                )
            ),
            _ResultStub(),
        ]
    )
    engine = _FakeEngine(conn)
    settings = SimpleNamespace(bitcoin_network="regtest")
    rpc = MagicMock()
    rpc.listunspent = AsyncMock(
        return_value=[
            {
                "address": "bcrt1ptestdeposit",
                "txid": "a" * 64,
                "vout": 0,
                "amount": 0.001,
                "confirmations": 2,
            }
        ]
    )

    with (
        patch.object(wallet_reconciliation, "get_bitcoin_rpc", return_value=rpc),
        patch.object(
            wallet_reconciliation,
            "list_imported_wallet_addresses",
            AsyncMock(return_value=[_RowStub(id=wallet_address_id, address="bcrt1ptestdeposit", wallet_id=wallet_id)]),
        ),
        patch.object(wallet_reconciliation, "record_business_event") as record_event,
    ):
        asyncio.run(wallet_reconciliation.reconcile_deposits(engine, settings))

    assert conn.commit.await_count == 1
    record_event.assert_not_called()


def test_sync_wallet_lightning_state_confirms_settled_invoice_and_recomputes_balance():
    wallet_id = str(uuid.uuid4())
    pending_row = _RowStub(id=uuid.uuid4(), ln_payment_hash="b" * 64)
    conn = MagicMock()
    lnd_client = MagicMock()
    lnd_client.lookup_invoice.return_value = _RowStub(state=1, settle_date=1_700_000_000)

    with (
        patch.object(wallet_reconciliation, "list_pending_lightning_receives", AsyncMock(return_value=[pending_row])),
        patch.object(wallet_reconciliation, "update_transaction_status", AsyncMock()) as update_status,
        patch.object(wallet_reconciliation, "recompute_lightning_balance", AsyncMock(return_value=2_500)) as recompute_balance,
    ):
        balance_sat = asyncio.run(wallet_reconciliation.sync_wallet_lightning_state(conn, wallet_id, lnd_client))

    assert balance_sat == 2_500
    update_status.assert_awaited_once()
    recompute_balance.assert_awaited_once_with(conn, wallet_id)


def test_sync_lightning_balance_refreshes_each_wallet_individually():
    conn = MagicMock()
    engine = _FakeEngine(conn)
    wallet_rows = [_RowStub(id=uuid.uuid4()), _RowStub(id=uuid.uuid4())]
    lnd_client = MagicMock()
    lnd_client.channel_balance.return_value = _RowStub(local_balance=_RowStub(sat=50_000))

    with (
        patch.object(wallet_reconciliation, "list_wallets", AsyncMock(return_value=wallet_rows)),
        patch.object(wallet_reconciliation, "sync_wallet_lightning_state", AsyncMock(side_effect=[10_000, 12_500])) as sync_wallet_state,
        patch.object(wallet_reconciliation, "record_business_event") as record_event,
    ):
        asyncio.run(wallet_reconciliation.sync_lightning_balance(engine, lnd_client))

    assert sync_wallet_state.await_count == 2
    record_event.assert_called_once()
    assert record_event.call_args.args[0] == "wallet_lightning_balance_sync"
