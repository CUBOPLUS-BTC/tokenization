from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import sys
from typing import NamedTuple
from unittest.mock import ANY, AsyncMock, MagicMock, patch
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
    referral_code: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    totp_secret: str | None = None


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
        referral_code="REF1234567",
        created_at=now,
        updated_at=now,
        deleted_at=None,
        totp_secret=totp_secret,
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
    audit_mock = AsyncMock()

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch("services.admin.main.update_user_role", AsyncMock(return_value=updated)),
        patch("services.admin.main.record_audit_event", audit_mock),
    ):
        response = app_client.patch(
            f"/users/{target.id}",
            json={"role": "seller"},
            headers=_auth(token),
        )

    assert response.status_code == 200
    assert response.json()["role"] == "seller"
    audit_mock.assert_awaited_once_with(
        ANY,
        settings=settings,
        request=ANY,
        action="admin.user_role.update",
        actor_id=str(admin.id),
        actor_role="admin",
        target_type="user",
        target_id=target.id,
        metadata={"new_role": "seller"},
    )


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
# POST /treasury/disburse
# ---------------------------------------------------------------------------


def test_admin_can_disburse_treasury_funds(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    entry = _make_treasury_entry(entry_type="disbursement", amount=500000, balance=14500000)
    token = _issue_token(admin, settings.jwt_secret)
    audit_mock = AsyncMock()

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch("services.admin.main.disburse_treasury", AsyncMock(return_value=entry)),
        patch("services.admin.main.record_audit_event", audit_mock),
    ):
        response = app_client.post(
            "/treasury/disburse",
            json={"amount_sat": 500000, "description": "Q2 treasury allocation"},
            headers=_auth(token),
        )

    assert response.status_code == 200
    data = response.json()
    assert data["entry"]["type"] == "disbursement"
    assert data["entry"]["amount_sat"] == 500000
    audit_mock.assert_awaited_once_with(
        ANY,
        settings=settings,
        request=ANY,
        action="admin.treasury.disburse",
        actor_id=str(admin.id),
        actor_role="admin",
        target_type="treasury_entry",
        target_id=entry.id,
        metadata={"amount_sat": 500000, "balance_after_sat": 14500000},
    )


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


def test_admin_can_view_referral_platform_summary(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    token = _issue_token(admin, settings.jwt_secret)

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch(
            "services.admin.main.summarize_referrals_platform",
            AsyncMock(return_value={"referred_users": 3, "active_referrers": 2, "total_reward_sat": 150000}),
        ),
    ):
        response = app_client.get("/referrals/summary", headers=_auth(token))

    assert response.status_code == 200
    assert response.json()["total_reward_sat"] == 150000


def test_admin_can_view_referral_user_summary(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    referred_user = _make_user(role="user")
    reward_row = {
        "id": uuid.uuid4(),
        "referred_user_id": referred_user.id,
        "referred_display_name": referred_user.display_name,
        "referred_email": referred_user.email,
        "reward_type": "signup_bonus",
        "amount_sat": 50000,
        "status": "credited",
        "eligibility_event": "kyc_verified",
        "credited_at": datetime.now(tz=timezone.utc),
        "created_at": datetime.now(tz=timezone.utc),
    }
    token = _issue_token(admin, settings.jwt_secret)

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(side_effect=[admin, admin])),
        patch(
            "services.admin.main.summarize_referrals_for_user",
            AsyncMock(return_value={"referrals_count": 1, "total_reward_sat": 50000}),
        ),
        patch("services.admin.main.list_referral_rewards_for_user", AsyncMock(return_value=[reward_row])),
    ):
        response = app_client.get(f"/referrals/{admin.id}", headers=_auth(token))

    assert response.status_code == 200
    data = response.json()
    assert data["referral_code"] == admin.referral_code
    assert data["rewards"][0]["amount_sat"] == 50000


def test_admin_can_view_yield_platform_summary(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    token = _issue_token(admin, settings.jwt_secret)

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin)),
        patch(
            "services.admin.main.summarize_yield_platform",
            AsyncMock(return_value={"users_with_yield": 2, "yield_tokens": 1, "total_yield_sat": 9000}),
        ),
    ):
        response = app_client.get("/yield/summary", headers=_auth(token))

    assert response.status_code == 200
    assert response.json()["users_with_yield"] == 2


def test_admin_can_view_yield_user_summary(client):
    app_client, settings = client
    admin = _make_user(role="admin")
    token = _issue_token(admin, settings.jwt_secret)
    token_id = uuid.uuid4()

    with (
        patch("services.admin.main.get_user_by_id", AsyncMock(side_effect=[admin, admin])),
        patch(
            "services.admin.main.summarize_yield_for_user",
            AsyncMock(return_value=(7000, [{"token_id": token_id, "asset_name": "Deep Ocean Blue", "total_yield_sat": 7000}])),
        ),
        patch(
            "services.admin.main.get_user_yield_accruals",
            AsyncMock(
                return_value=[
                    {
                        "id": uuid.uuid4(),
                        "token_id": token_id,
                        "asset_name": "Deep Ocean Blue",
                        "amount_sat": 7000,
                        "quantity_held": 100,
                        "reference_price_sat": 2500,
                        "annual_rate_pct": 8.5,
                        "accrued_from": datetime.now(tz=timezone.utc),
                        "accrued_to": datetime.now(tz=timezone.utc),
                        "created_at": datetime.now(tz=timezone.utc),
                    }
                ]
            ),
        ),
    ):
        response = app_client.get(f"/yield/{admin.id}", headers=_auth(token))

    assert response.status_code == 200
    data = response.json()
    assert data["total_yield_sat"] == 7000
    assert data["by_token"][0]["asset_name"] == "Deep Ocean Blue"


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
            json={
                "resolution": "refund_buyer",
                "notes": "Seller failed to deliver.",
                "resolution_txid": "a" * 64,
                "collected_signatures": {},
            },
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
            json={
                "resolution": "refund_buyer",
                "notes": "test",
                "resolution_txid": "b" * 64,
                "collected_signatures": {},
            },
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
            json={
                "resolution": "refund_buyer",
                "notes": "test",
                "resolution_txid": "c" * 64,
                "collected_signatures": {},
            },
            headers=_auth(token),
        )

    assert response.status_code == 403

