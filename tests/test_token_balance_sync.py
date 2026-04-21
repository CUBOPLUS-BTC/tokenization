from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from sqlalchemy.dialects import postgresql


class _FetchOneResult:
    def __init__(self, row: object) -> None:
        self._row = row

    def fetchone(self) -> object:
        return self._row


@pytest.fixture()
def service_settings() -> dict[str, str]:
    return {
        "ENV_PROFILE": "local",
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
        "TAPD_MACAROON_PATH": "tests/fixtures/tapd.macaroon",
        "TAPD_TLS_CERT_PATH": "tests/fixtures/tapd.cert",
        "NOSTR_RELAYS": "wss://relay.example.com",
        "JWT_SECRET": "test-secret-key-for-token-balance-sync-tests",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
    }


def test_create_asset_token_seeds_owner_balance_with_circulating_supply(service_settings):
    with patch.dict(os.environ, service_settings, clear=False):
        for module_name in (
            "services.tokenization.db",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.tokenization.db as tokenization_db

    asset_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(
        side_effect=[
            _FetchOneResult(object()),
            _FetchOneResult(None),
            _FetchOneResult(None),
        ]
    )
    fake_conn.commit = AsyncMock()
    tokenized_row = SimpleNamespace(id=asset_id)

    with patch.object(tokenization_db, "get_asset_by_id", AsyncMock(return_value=tokenized_row)):
        row = asyncio.run(
            tokenization_db.create_asset_token(
                fake_conn,
                asset_id=asset_id,
                owner_id=owner_id,
                taproot_asset_id="ab" * 32,
                total_supply=1_000,
                circulating_supply=1_000,
                unit_price_sat=100_000,
                issuance_metadata={"issuer": "tapd"},
            )
        )

    balance_insert_statement = fake_conn.execute.await_args_list[2].args[0]
    params = balance_insert_statement.compile(dialect=postgresql.dialect()).params

    assert params["user_id"] == owner_id
    assert params["balance"] == 1_000
    assert row is tokenized_row
