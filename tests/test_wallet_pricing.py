from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy.dialects import postgresql


class _MappingsResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _MappingsResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _CaptureConn:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _MappingsResult(self.rows)


@pytest.fixture()
def service_settings() -> dict[str, str]:
    return {
        "ENV_PROFILE": "local",
        "WALLET_SERVICE_URL": "http://wallet:8001",
        "TOKENIZATION_SERVICE_URL": "http://tokenization:8002",
        "MARKETPLACE_SERVICE_URL": "http://marketplace:8003",
        "EDUCATION_SERVICE_URL": "http://education:8004",
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
        "JWT_SECRET": "test-secret-key-for-wallet-pricing-tests",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
    }


def test_wallet_balance_query_uses_settled_trade_price_when_available(service_settings):
    with patch.dict(os.environ, service_settings, clear=False):
        for module_name in (
            "services.wallet.db",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.wallet.db as wallet_db

    token_id = uuid.uuid4()
    fake_conn = _CaptureConn(
        rows=[
            {
                "token_id": token_id,
                "asset_name": "Harbor Plaza",
                "balance": 5,
                "unit_price_sat": 125_000,
            }
        ]
    )

    rows = asyncio.run(wallet_db.get_token_balances_for_user(fake_conn, str(uuid.uuid4())))
    compiled_sql = str(fake_conn.statement.compile(dialect=postgresql.dialect())).lower()

    assert "coalesce" in compiled_sql
    assert "trades" in compiled_sql
    assert rows == [
        {
            "token_id": token_id,
            "asset_name": "Harbor Plaza",
            "balance": 5,
            "unit_price_sat": 125_000,
        }
    ]