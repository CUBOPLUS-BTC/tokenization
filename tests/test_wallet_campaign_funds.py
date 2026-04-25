from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest


@pytest.fixture()
def fake_settings():
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
        "NOSTR_RELAYS": "wss://relay.example.com",
        "JWT_SECRET": "test-secret-wallet-campaign-funds",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
        "WALLET_ENCRYPTION_KEY": "00" * 32,
    }


@pytest.fixture()
def client(fake_settings):
    fake_conn = AsyncMock()

    async def _fake_connect():
        yield fake_conn

    fake_engine = MagicMock()
    fake_engine.connect = AsyncMock(return_value=fake_conn)
    fake_engine.dispose = AsyncMock()

    with patch.dict(os.environ, fake_settings, clear=False):
        for module_name in ("services.wallet.main", "wallet.main", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.wallet.main as wallet_main

        wallet_main._engine = fake_engine
        wallet_main.app.router.lifespan_context = None
        wallet_main.app.dependency_overrides[wallet_main.get_db_conn] = _fake_connect
        yield TestClient(wallet_main.app, raise_server_exceptions=True), wallet_main
        wallet_main.app.dependency_overrides = {}


def test_internal_campaign_reserve_requires_internal_token(client):
    app_client, _wallet_main = client
    response = app_client.post(
        "/internal/campaign-funds/reserve",
        json={"campaign_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "amount_sat": 1000},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_internal_campaign_reserve_returns_confirmed_funding(client):
    app_client, wallet_main = client
    campaign_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    funding_id = uuid.uuid4()

    wallet_main.get_campaign_by_id = AsyncMock(return_value={"id": uuid.UUID(campaign_id)})
    wallet_main.get_wallet_by_user_id = AsyncMock(return_value={"id": uuid.uuid4()})
    wallet_main.reserve_campaign_balance_from_wallet = AsyncMock(
        return_value={
            "id": funding_id,
            "status": "confirmed",
            "amount_sat": 2000,
            "confirmed_at": datetime.now(tz=timezone.utc),
        }
    )
    wallet_main.record_audit_event = AsyncMock()

    response = app_client.post(
        "/internal/campaign-funds/reserve",
        headers={"X-Internal-Token": wallet_main.settings.jwt_secret},
        json={"campaign_id": campaign_id, "user_id": user_id, "amount_sat": 2000},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["campaign_id"] == campaign_id
    assert body["status"] == "confirmed"
    assert body["amount_sat"] == 2000


def test_internal_campaign_invoice_returns_payment_request(client):
    app_client, wallet_main = client
    campaign_id = str(uuid.uuid4())
    funding_id = uuid.uuid4()
    wallet_main.get_campaign_by_id = AsyncMock(return_value={"id": uuid.UUID(campaign_id)})
    wallet_main.create_external_campaign_funding = AsyncMock(
        return_value={
            "id": funding_id,
            "status": "pending",
            "amount_sat": 5000,
        }
    )
    wallet_main.record_audit_event = AsyncMock()
    wallet_main.lnd_client = SimpleNamespace(
        create_invoice=MagicMock(return_value=SimpleNamespace(payment_request="lnbc1campaign", r_hash=b"\x01\x02"))
    )

    response = app_client.post(
        "/internal/campaign-funds/invoice",
        headers={"X-Internal-Token": wallet_main.settings.jwt_secret},
        json={"campaign_id": campaign_id, "amount_sat": 5000},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["payment_request"] == "lnbc1campaign"
    assert body["payment_hash"] == "0102"
    assert body["status"] == "pending"
