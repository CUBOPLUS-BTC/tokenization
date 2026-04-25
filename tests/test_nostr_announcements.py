from __future__ import annotations

import sys
import uuid
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
        "NOSTR_RELAYS": "wss://relay.example.com,wss://relay.backup.example.com",
        "JWT_SECRET": "test-secret-key-for-nostr-announcements",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
    }


@pytest.fixture()
def client(fake_settings):
    with pytest.MonkeyPatch.context() as mp:
        for key, value in fake_settings.items():
            mp.setenv(key, value)
        for module_name in ("services.nostr.main", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.nostr.main as nostr_main

        connector = SimpleNamespace(
            publish=AsyncMock(
                return_value={
                    "wss://relay.example.com": True,
                    "wss://relay.backup.example.com": False,
                }
            )
        )
        nostr_main._connector = connector
        app = nostr_main.app
        app.router.lifespan_context = None

        yield TestClient(app, raise_server_exceptions=True), nostr_main, connector


def _issue_access_token(*, role: str, secret: str) -> str:
    return issue_token_pair(
        user_id=str(uuid.uuid4()),
        role=role,
        wallet_id=None,
        secret=secret,
    ).access_token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_seller_can_publish_classified_announcement(client):
    app_client, nostr_main, connector = client
    token = _issue_access_token(role="seller", secret=nostr_main.settings.jwt_secret)

    response = app_client.post(
        "/announcements",
        json={
            "title": "Real Estate Yield Offer",
            "summary": "Quarterly yield backed by tokenized rentals.",
            "content": "Own a fractional slice of rental income.",
            "hashtags": ["real estate", "yield"],
            "location": "San Salvador",
            "price_amount": "25000",
            "price_currency": "usd",
            "reference_url": "https://platform.example/offers/real-estate-yield",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == 30402
    assert body["accepted_relays"] == ["wss://relay.example.com"]
    assert body["failed_relays"] == ["wss://relay.backup.example.com"]
    connector.publish.assert_awaited_once()
    published_event = connector.publish.await_args.args[0]
    assert published_event["kind"] == 30402
    assert ["title", "Real Estate Yield Offer"] in published_event["tags"]
    assert ["price", "25000", "USD"] in published_event["tags"]


def test_regular_user_cannot_publish_announcement(client):
    app_client, nostr_main, _connector = client
    token = _issue_access_token(role="user", secret=nostr_main.settings.jwt_secret)

    response = app_client.post(
        "/announcements",
        json={
            "title": "Unauthorized announcement",
            "content": "This should fail.",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_announcement_returns_502_when_all_relays_fail(client):
    app_client, nostr_main, connector = client
    connector.publish = AsyncMock(side_effect=RuntimeError("all relays down"))
    token = _issue_access_token(role="admin", secret=nostr_main.settings.jwt_secret)

    response = app_client.post(
        "/announcements",
        json={
            "title": "Relay outage announcement",
            "content": "This should surface a publication error.",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "relay_unavailable"


def test_announcement_requires_authentication(client):
    app_client, _nostr_main, _connector = client

    response = app_client.post(
        "/announcements",
        json={
            "title": "Missing auth",
            "content": "This should fail.",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
