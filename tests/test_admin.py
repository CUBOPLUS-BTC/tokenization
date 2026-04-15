from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import sys
from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from fastapi.testclient import TestClient

from services.auth.jwt_utils import issue_token_pair


# ---------------------------------------------------------------------------
# Fake DB rows
# ---------------------------------------------------------------------------


class FakeUser(NamedTuple):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    totp_secret: str | None = None


class FakeCourse(NamedTuple):
    id: uuid.UUID
    title: str
    description: str
    content_url: str
    category: str
    difficulty: str
    is_published: bool
    created_at: datetime
    updated_at: datetime


class FakeTreasuryEntry(NamedTuple):
    id: uuid.UUID
    type: str
    amount_sat: int
    balance_after_sat: int
    source_trade_id: uuid.UUID | None
    description: str | None
    created_at: datetime


class FakeDispute(NamedTuple):
    id: uuid.UUID
    trade_id: uuid.UUID
    opened_by: uuid.UUID
    reason: str
    status: str
    resolution: str | None
    resolved_by: uuid.UUID | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _make_user(*, role: str = "user", totp_secret: str | None = None) -> FakeUser:
    now = datetime.now(tz=timezone.utc)
    return FakeUser(
        id=uuid.uuid4(),
        email="user@example.com",
        display_name="Test User",
        role=role,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        totp_secret=totp_secret,
    )


def _make_course() -> FakeCourse:
    now = datetime.now(tz=timezone.utc)
    return FakeCourse(
        id=uuid.uuid4(),
        title="Bitcoin 101",
        description="Introduction to Bitcoin.",
        content_url="https://example.com/bitcoin-101",
        category="bitcoin",
        difficulty="beginner",
        is_published=False,
        created_at=now,
        updated_at=now,
    )


def _make_treasury_entry(*, entry_type: str = "fee_income", amount: int = 5000, balance: int = 15000) -> FakeTreasuryEntry:
    return FakeTreasuryEntry(
        id=uuid.uuid4(),
        type=entry_type,
        amount_sat=amount,
        balance_after_sat=balance,
        source_trade_id=None,
        description=None,
        created_at=datetime.now(tz=timezone.utc),
    )


def _make_dispute(*, status: str = "open") -> FakeDispute:
    now = datetime.now(tz=timezone.utc)
    return FakeDispute(
        id=uuid.uuid4(),
        trade_id=uuid.uuid4(),
        opened_by=uuid.uuid4(),
        reason="Seller did not deliver.",
        status=status,
        resolution=None,
        resolved_by=None,
        resolved_at=None,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Env settings fixture
# ---------------------------------------------------------------------------


_ADMIN_SETTINGS = {
    "ENV_PROFILE": "local",
    "WALLET_SERVICE_URL": "http://wallet:8001",
    "TOKENIZATION_SERVICE_URL": "http://tokenization:8002",
    "MARKETPLACE_SERVICE_URL": "http://marketplace:8003",
    "EDUCATION_SERVICE_URL": "http://education:8004",
    "NOSTR_SERVICE_URL": "http://nostr:8005",
    "ADMIN_SERVICE_URL": "http://admin:8006",
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
    "JWT_SECRET": "test-secret-key-for-admin-tests",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
    "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
    "TOTP_ISSUER": "Platform",
    "LOG_LEVEL": "INFO",
}


@pytest.fixture()
def client():
    fake_conn = AsyncMock()

    @asynccontextmanager
    async def _fake_connect():
        yield fake_conn

    fake_engine = MagicMock()
    fake_engine.connect = _fake_connect
    fake_engine.dispose = AsyncMock()

    with patch.dict(os.environ, _ADMIN_SETTINGS, clear=False):
        for module_name in (
            "services.admin.main",
            "services.admin.db",
            "services.admin.schemas",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.admin.main as admin_main

        admin_main._engine = fake_engine
        app = admin_main.app
        app.router.lifespan_context = None

        yield TestClient(app, raise_server_exceptions=True), admin_main.settings


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _issue_token(user: FakeUser, secret: str) -> str:
    return issue_token_pair(
        user_id=str(user.id),
        role=user.role,
        wallet_id=None,
        secret=secret,
    ).access_token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# GET /users
# ---------------------------------------------------------------------------


def test_admin_can_list_users(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    user1 = _make_user(role="user")
    user2 = _make_user(role="seller")
    token = _issue_token(admin, settings.jwt_secret)

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch("services.admin.main.list_users", AsyncMock(return_value=[user1, user2])),
    ):
        response = app_client.get("/users", headers=_auth(token))

    assert response.status_code == 200
    data = response.json()
    assert len(data["users"]) == 2
    assert data["next_cursor"] is None


def test_admin_can_list_users_filtered_by_role(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    seller = _make_user(role="seller")
    token = _issue_token(admin, settings.jwt_secret)

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch("services.admin.main.list_users", AsyncMock(return_value=[seller])) as mock_list,
    ):
        response = app_client.get("/users?role=seller", headers=_auth(token))

    assert response.status_code == 200
    assert len(response.json()["users"]) == 1


def test_non_admin_cannot_list_users(client):
    app_client, settings = client
    regular = _make_user(role="user")
    token = _issue_token(regular, settings.jwt_secret)

    with patch("services.admin.main.get_user_by_id", AsyncMock(return_value=regular)):
        response = app_client.get("/users", headers=_auth(token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}
# ---------------------------------------------------------------------------


def test_admin_can_update_user_role(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    target = _make_user(role="user")
    updated = target._replace(role="seller")
    token = _issue_token(admin, settings.jwt_secret)

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch("services.admin.main.update_user_role", AsyncMock(return_value=updated)),
    ):
        response = app_client.patch(
            f"/users/{target.id}",
            json={"role": "seller"},
            headers=_auth(token),
        )

    assert response.status_code == 200
    assert response.json()["role"] == "seller"


def test_admin_cannot_update_role_to_invalid_value(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    token = _issue_token(admin, settings.jwt_secret)

    with patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)):
        response = app_client.patch(
            f"/users/{uuid.uuid4()}",
            json={"role": "superuser"},
            headers=_auth(token),
        )

    assert response.status_code == 422


def test_non_admin_cannot_update_role(client):
    app_client, settings = client
    regular = _make_user(role="user")
    token = _issue_token(regular, settings.jwt_secret)

    with patch("services.admin.main.get_user_by_id", AsyncMock(return_value=regular)):
        response = app_client.patch(
            f"/users/{uuid.uuid4()}",
            json={"role": "seller"},
            headers=_auth(token),
        )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /courses
# ---------------------------------------------------------------------------


def test_admin_can_create_course(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    course = _make_course()
    token = _issue_token(admin, settings.jwt_secret)

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch("services.admin.main.create_course", AsyncMock(return_value=course)),
    ):
        response = app_client.post(
            "/courses",
            json={
                "title": course.title,
                "description": course.description,
                "content_url": course.content_url,
                "category": course.category,
                "difficulty": course.difficulty,
            },
            headers=_auth(token),
        )

    assert response.status_code == 201
    data = response.json()
    assert data["course"]["title"] == course.title
    assert data["course"]["category"] == "bitcoin"


def test_non_admin_cannot_create_course(client):
    app_client, settings = client
    regular = _make_user(role="user")
    token = _issue_token(regular, settings.jwt_secret)

    with patch("services.admin.main.get_user_by_id", AsyncMock(return_value=regular)):
        response = app_client.post(
            "/courses",
            json={
                "title": "Test",
                "description": "Desc",
                "content_url": "https://example.com",
                "category": "bitcoin",
                "difficulty": "beginner",
            },
            headers=_auth(token),
        )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /treasury/disburse
# ---------------------------------------------------------------------------


def test_admin_can_disburse_treasury_funds(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    entry = _make_treasury_entry(entry_type="disbursement", amount=500000, balance=14500000)
    token = _issue_token(admin, settings.jwt_secret)

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch("services.admin.main.disburse_treasury", AsyncMock(return_value=entry)),
    ):
        response = app_client.post(
            "/treasury/disburse",
            json={"amount_sat": 500000, "description": "Q2 educational program"},
            headers=_auth(token),
        )

    assert response.status_code == 200
    data = response.json()
    assert data["entry"]["type"] == "disbursement"
    assert data["entry"]["amount_sat"] == 500000


def test_treasury_disburse_requires_2fa_when_enabled(client):
    """When user has totp_secret set, X-2FA-Code header must be provided."""
    app_client, settings = client
    admin = _make_user(role="admin", totp_secret="JBSWY3DPEHPK3PXP")
    token = _issue_token(admin, settings.jwt_secret)

    with patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)):
        response = app_client.post(
            "/treasury/disburse",
            json={"amount_sat": 500000, "description": "test"},
            headers=_auth(token),
            # No X-2FA-Code header
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "2fa_required"


def test_non_admin_cannot_disburse_treasury(client):
    app_client, settings = client
    regular = _make_user(role="user")
    token = _issue_token(regular, settings.jwt_secret)

    with patch("services.admin.main.get_user_by_id", AsyncMock(return_value=regular)):
        response = app_client.post(
            "/treasury/disburse",
            json={"amount_sat": 100, "description": "test"},
            headers=_auth(token),
        )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /escrows/{trade_id}/resolve
# ---------------------------------------------------------------------------


def test_admin_can_resolve_dispute(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    dispute = _make_dispute(status="open")
    resolved_dispute = dispute._replace(status="resolved", resolution="refund")
    token = _issue_token(admin, settings.jwt_secret)

    fake_trade = MagicMock()
    fake_escrow = MagicMock()

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch("services.admin.main.get_dispute_by_trade_id", AsyncMock(return_value=dispute)),
        patch(
            "services.admin.main.resolve_dispute",
            AsyncMock(return_value=(resolved_dispute, fake_trade, fake_escrow)),
        ),
    ):
        response = app_client.post(
            f"/escrows/{dispute.trade_id}/resolve",
            json={"resolution": "refund_buyer", "notes": "Seller failed to deliver."},
            headers=_auth(token),
        )

    assert response.status_code == 200
    data = response.json()
    assert data["dispute"]["status"] == "resolved"
    assert data["dispute"]["resolution"] == "refund"


def test_resolve_dispute_not_found(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    token = _issue_token(admin, settings.jwt_secret)

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch("services.admin.main.get_dispute_by_trade_id", AsyncMock(return_value=None)),
    ):
        response = app_client.post(
            f"/escrows/{uuid.uuid4()}/resolve",
            json={"resolution": "refund_buyer", "notes": "test"},
            headers=_auth(token),
        )

    assert response.status_code == 404


def test_non_admin_cannot_resolve_dispute(client):
    app_client, settings = client
    regular = _make_user(role="user")
    token = _issue_token(regular, settings.jwt_secret)

    with patch("services.admin.main.get_user_by_id", AsyncMock(return_value=regular)):
        response = app_client.post(
            f"/escrows/{uuid.uuid4()}/resolve",
            json={"resolution": "refund_buyer", "notes": "test"},
            headers=_auth(token),
        )

    assert response.status_code == 403
