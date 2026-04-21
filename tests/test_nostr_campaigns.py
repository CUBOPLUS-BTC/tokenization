from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from services.auth.jwt_utils import issue_token_pair


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
        "JWT_SECRET": "test-secret-key-for-nostr-campaigns",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
    }


def _issue_access_token(*, role: str, secret: str, user_id: str) -> str:
    return issue_token_pair(user_id=user_id, role=role, wallet_id=None, secret=secret).access_token


@pytest.fixture()
def client(fake_settings):
    fake_conn = AsyncMock()

    async def _fake_db():
        yield fake_conn

    with pytest.MonkeyPatch.context() as mp:
        for key, value in fake_settings.items():
            mp.setenv(key, value)
        for module_name in ("services.nostr.main", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.nostr.main as nostr_main

        nostr_main.app.router.lifespan_context = None
        nostr_main.app.dependency_overrides[nostr_main.get_db_conn] = _fake_db
        nostr_main._connector = SimpleNamespace(
            probe_relays=AsyncMock(return_value={"wss://relay.example.com": True}),
            publish=AsyncMock(return_value={"wss://relay.example.com": True}),
        )
        nostr_main._wallet_client = SimpleNamespace(
            reserve_campaign_funds=AsyncMock(return_value={"status": "confirmed"}),
            create_campaign_funding_invoice=AsyncMock(
                return_value={
                    "funding_id": str(uuid.uuid4()),
                    "status": "pending",
                    "amount_sat": 25_000,
                    "payment_hash": "ab" * 32,
                    "payment_request": "lnbc1campaign",
                }
            ),
            sync_campaign_funding=AsyncMock(return_value={"status": "confirmed"}),
        )
        yield TestClient(nostr_main.app, raise_server_exceptions=True), nostr_main
        nostr_main.app.dependency_overrides = {}


def _campaign_row(*, user_id: str, campaign_id: str | None = None, status: str = "draft", reserved_sat: int = 0):
    now = datetime.now(tz=timezone.utc)
    return {
        "id": uuid.UUID(campaign_id) if campaign_id else uuid.uuid4(),
        "user_id": uuid.UUID(user_id),
        "name": "Zap campaign",
        "status": status,
        "funding_mode": "intraledger",
        "reward_amount_sat": 1000,
        "budget_total_sat": 10_000,
        "budget_reserved_sat": reserved_sat,
        "budget_spent_sat": 0,
        "budget_refunded_sat": 0,
        "max_rewards_per_user": 1,
        "start_at": None,
        "end_at": None,
        "created_at": now,
        "updated_at": now,
    }


def test_create_campaign_returns_created_resource(client):
    app_client, nostr_main = client
    user_id = str(uuid.uuid4())
    token = _issue_access_token(role="user", secret=nostr_main.settings.jwt_secret, user_id=user_id)
    row = _campaign_row(user_id=user_id)

    nostr_main.create_campaign = AsyncMock(return_value=row)
    nostr_main.list_campaign_triggers = AsyncMock(
        return_value=[
            {
                "id": uuid.uuid4(),
                "trigger_type": "hashtag",
                "operator": "equals",
                "value": "bitcoin",
                "case_sensitive": False,
                "created_at": datetime.now(tz=timezone.utc),
            }
        ]
    )
    nostr_main.list_campaign_fundings = AsyncMock(return_value=[])

    response = app_client.post(
        "/campaigns",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Zap campaign",
            "funding_mode": "intraledger",
            "reward_amount_sat": 1000,
            "budget_total_sat": 10000,
            "triggers": [{"trigger_type": "hashtag", "operator": "equals", "value": "bitcoin"}],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Zap campaign"
    assert body["status"] == "draft"
    assert body["triggers"][0]["value"] == "bitcoin"


def test_fund_intraledger_calls_wallet_client_and_returns_refreshed_campaign(client):
    app_client, nostr_main = client
    user_id = str(uuid.uuid4())
    campaign_id = str(uuid.uuid4())
    token = _issue_access_token(role="user", secret=nostr_main.settings.jwt_secret, user_id=user_id)
    draft = _campaign_row(user_id=user_id, campaign_id=campaign_id, reserved_sat=0)
    funded = _campaign_row(user_id=user_id, campaign_id=campaign_id, status="funding_pending", reserved_sat=5_000)

    nostr_main.get_campaign_row = AsyncMock(side_effect=[draft, funded])
    nostr_main.list_campaign_triggers = AsyncMock(return_value=[])
    nostr_main.list_campaign_fundings = AsyncMock(return_value=[])

    response = app_client.post(
        f"/campaigns/{campaign_id}/fund/intraledger",
        headers={"Authorization": f"Bearer {token}"},
        json={"amount_sat": 5000},
    )

    assert response.status_code == 200
    assert response.json()["budget_reserved_sat"] == 5000
    nostr_main._wallet_client.reserve_campaign_funds.assert_awaited_once()


def test_activate_campaign_requires_reserved_balance(client):
    app_client, nostr_main = client
    user_id = str(uuid.uuid4())
    campaign_id = str(uuid.uuid4())
    token = _issue_access_token(role="user", secret=nostr_main.settings.jwt_secret, user_id=user_id)

    nostr_main.get_campaign_row = AsyncMock(return_value=_campaign_row(user_id=user_id, campaign_id=campaign_id, reserved_sat=0))

    response = app_client.post(
        f"/campaigns/{campaign_id}/activate",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "campaign_not_funded"
