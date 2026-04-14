"""Unit tests for auth registration, session controls, and RBAC.

These tests run entirely in-process using an AsyncMock database layer,
so no real PostgreSQL connection is required.

Run with:
    pytest tests/test_auth.py -v
"""
from __future__ import annotations

import os
import sys
import uuid
from collections import namedtuple
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
import bcrypt

from services.auth.jwt_utils import decode_token, issue_token_pair

# ---------------------------------------------------------------------------
# Patch the settings + engine BEFORE importing the app so that it never tries
# to touch a real database or config files.
# ---------------------------------------------------------------------------



# A fake user row that mimics a SQLAlchemy Row
FakeUser = namedtuple(
    "FakeUser",
    [
        "id",
        "email",
        "password_hash",
        "display_name",
        "role",
        "created_at",
        "deleted_at",
    ],
)


def _make_fake_user(email: str, password: str, *, role: str = "user") -> FakeUser:
    return FakeUser(
        id=uuid.uuid4(),
        email=email,
        password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        display_name="Alice",
        role=role,
        created_at=datetime.now(tz=timezone.utc),
        deleted_at=None,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_settings():
    """Environment values required to build auth settings during tests."""
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
        "NOSTR_RELAYS": "wss://relay.example.com",
        "JWT_SECRET": "test-secret-key-for-unit-tests",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
    }


@pytest.fixture()
def client(fake_settings):
    """TestClient with all external dependencies patched."""

    # We need a fake async engine whose .connect() is an async context manager
    # returning a fake AsyncConnection.
    fake_conn = AsyncMock()

    @asynccontextmanager
    async def _fake_connect():
        yield fake_conn

    fake_engine = MagicMock()
    fake_engine.connect = _fake_connect
    fake_engine.dispose = AsyncMock()

    with patch.dict(os.environ, fake_settings, clear=False):
        for module_name in ("services.auth.main", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.auth.main as auth_main

        auth_main._engine = fake_engine

        with (
            patch.object(auth_main, "create_refresh_session", AsyncMock()),
            patch.object(auth_main, "rotate_refresh_session", AsyncMock(return_value=True)),
            patch.object(auth_main, "revoke_refresh_session", AsyncMock(return_value=True)),
        ):
            app = auth_main.app

            # Override lifespan so it doesn't rebuild the engine
            app.router.lifespan_context = None  # disable lifespan for sync TestClient

            yield TestClient(app, raise_server_exceptions=True), fake_conn, auth_main.settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_token_structure(tokens: dict):
    assert "access_token" in tokens
    assert "refresh_token" in tokens
    assert tokens["expires_in"] == 900
    assert tokens["access_token"].startswith("eyJ")
    assert tokens["refresh_token"].startswith("eyJ")


def _assert_user_structure(user: dict, email: str):
    assert "id" in user
    assert user["email"] == email
    assert "display_name" in user
    assert user["role"] == "user"
    assert "created_at" in user


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _issue_access_token(fake_user: FakeUser, secret: str) -> str:
    return issue_token_pair(
        user_id=str(fake_user.id),
        role=fake_user.role,
        wallet_id=None,
        secret=secret,
    ).access_token


class InMemoryRefreshSessions:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, object]] = {}

    async def create(
        self,
        conn,
        *,
        user_id: str,
        token_jti: str,
        expires_at: datetime,
    ) -> None:
        self._sessions[token_jti] = {
            "user_id": user_id,
            "expires_at": expires_at,
            "revoked_at": None,
            "replaced_by_jti": None,
        }

    async def rotate(
        self,
        conn,
        *,
        user_id: str,
        current_token_jti: str,
        replacement_token_jti: str,
        replacement_expires_at: datetime,
    ) -> bool:
        session = self._sessions.get(current_token_jti)
        if session is None:
            return False
        if session["user_id"] != user_id:
            return False
        if session["revoked_at"] is not None:
            return False
        if session["expires_at"] <= datetime.now(tz=timezone.utc):
            return False

        session["revoked_at"] = datetime.now(tz=timezone.utc)
        session["replaced_by_jti"] = replacement_token_jti
        self._sessions[replacement_token_jti] = {
            "user_id": user_id,
            "expires_at": replacement_expires_at,
            "revoked_at": None,
            "replaced_by_jti": None,
        }
        return True

    async def revoke(
        self,
        conn,
        *,
        user_id: str,
        token_jti: str,
    ) -> bool:
        session = self._sessions.get(token_jti)
        if session is None:
            return False
        if session["user_id"] != user_id:
            return False
        if session["revoked_at"] is not None:
            return False
        if session["expires_at"] <= datetime.now(tz=timezone.utc):
            return False

        session["revoked_at"] = datetime.now(tz=timezone.utc)
        return True


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_success_returns_201_with_tokens(self, client):
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user("alice@example.com", "SecureP@ss123")

        with (
            patch("services.auth.main.get_user_by_email", AsyncMock(return_value=None)),
            patch("services.auth.main.create_user", AsyncMock(return_value=fake_user)),
        ):
            resp = app_client.post(
                "/auth/register",
                json={
                    "email": "alice@example.com",
                    "password": "SecureP@ss123",
                    "display_name": "Alice",
                },
            )

        assert resp.status_code == 201
        body = resp.json()
        assert "user" in body
        assert "tokens" in body
        _assert_user_structure(body["user"], "alice@example.com")
        _assert_token_structure(body["tokens"])

    def test_register_duplicate_email_returns_409(self, client):
        app_client, fake_conn, settings = client
        existing_user = _make_fake_user("alice@example.com", "SecureP@ss123")

        with patch(
            "services.auth.main.get_user_by_email",
            AsyncMock(return_value=existing_user),
        ):
            resp = app_client.post(
                "/auth/register",
                json={
                    "email": "alice@example.com",
                    "password": "SecureP@ss123",
                    "display_name": "Alice",
                },
            )

        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "email_taken"
        assert "message" in body["error"]

    def test_register_missing_display_name_returns_422(self, client):
        app_client, *_ = client
        resp = app_client.post(
            "/auth/register",
            json={"email": "alice@example.com", "password": "SecureP@ss123"},
        )
        assert resp.status_code == 422

    def test_register_weak_password_no_uppercase_returns_422(self, client):
        app_client, *_ = client
        resp = app_client.post(
            "/auth/register",
            json={
                "email": "alice@example.com",
                "password": "weakpass1!",
                "display_name": "Alice",
            },
        )
        assert resp.status_code == 422

    def test_register_weak_password_no_digit_returns_422(self, client):
        app_client, *_ = client
        resp = app_client.post(
            "/auth/register",
            json={
                "email": "alice@example.com",
                "password": "WeakPass!",
                "display_name": "Alice",
            },
        )
        assert resp.status_code == 422

    def test_register_weak_password_no_special_char_returns_422(self, client):
        app_client, *_ = client
        resp = app_client.post(
            "/auth/register",
            json={
                "email": "alice@example.com",
                "password": "WeakPass1",
                "display_name": "Alice",
            },
        )
        assert resp.status_code == 422

    def test_register_invalid_email_returns_422(self, client):
        app_client, *_ = client
        resp = app_client.post(
            "/auth/register",
            json={
                "email": "not-an-email",
                "password": "SecureP@ss123",
                "display_name": "Alice",
            },
        )
        assert resp.status_code == 422

    def test_register_error_body_matches_contract(self, client):
        """Error body must be { 'error': { 'code': '...', 'message': '...' } }"""
        app_client, fake_conn, settings = client
        existing_user = _make_fake_user("alice@example.com", "SecureP@ss123")

        with patch(
            "services.auth.main.get_user_by_email",
            AsyncMock(return_value=existing_user),
        ):
            resp = app_client.post(
                "/auth/register",
                json={
                    "email": "alice@example.com",
                    "password": "SecureP@ss123",
                    "display_name": "Alice",
                },
            )

        body = resp.json()
        assert set(body.keys()) == {"error"}
        assert set(body["error"].keys()) == {"code", "message"}


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success_returns_200_with_tokens(self, client):
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user("alice@example.com", "SecureP@ss123")

        with patch(
            "services.auth.main.get_user_by_email",
            AsyncMock(return_value=fake_user),
        ):
            resp = app_client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "SecureP@ss123"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "user" in body
        assert "tokens" in body
        _assert_user_structure(body["user"], "alice@example.com")
        _assert_token_structure(body["tokens"])

    def test_login_wrong_password_returns_401(self, client):
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user("alice@example.com", "SecureP@ss123")

        with patch(
            "services.auth.main.get_user_by_email",
            AsyncMock(return_value=fake_user),
        ):
            resp = app_client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "WrongP@ss1"},
            )

        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "invalid_credentials"

    def test_login_unknown_email_returns_401(self, client):
        app_client, fake_conn, settings = client

        with patch(
            "services.auth.main.get_user_by_email",
            AsyncMock(return_value=None),
        ):
            resp = app_client.post(
                "/auth/login",
                json={"email": "ghost@example.com", "password": "SecureP@ss123"},
            )

        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "invalid_credentials"

    def test_login_generic_error_does_not_leak_which_field_is_wrong(self, client):
        """Both 'wrong email' and 'wrong password' must return the SAME message."""
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user("alice@example.com", "SecureP@ss123")

        with patch(
            "services.auth.main.get_user_by_email",
            AsyncMock(return_value=fake_user),
        ):
            bad_pass_resp = app_client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "WrongP@ss1"},
            )

        with patch(
            "services.auth.main.get_user_by_email",
            AsyncMock(return_value=None),
        ):
            bad_email_resp = app_client.post(
                "/auth/login",
                json={"email": "ghost@example.com", "password": "SecureP@ss123"},
            )

        assert bad_pass_resp.json()["error"]["message"] == bad_email_resp.json()["error"]["message"]

    def test_login_error_body_matches_contract(self, client):
        app_client, fake_conn, settings = client

        with patch(
            "services.auth.main.get_user_by_email",
            AsyncMock(return_value=None),
        ):
            resp = app_client.post(
                "/auth/login",
                json={"email": "ghost@example.com", "password": "SecureP@ss123"},
            )

        body = resp.json()
        assert set(body.keys()) == {"error"}
        assert set(body["error"].keys()) == {"code", "message"}

    def test_login_missing_password_returns_422(self, client):
        app_client, *_ = client
        resp = app_client.post(
            "/auth/login",
            json={"email": "alice@example.com"},
        )
        assert resp.status_code == 422


class TestRefreshTokens:
    def test_refresh_rotation_rejects_token_reuse(self, client):
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user("alice@example.com", "SecureP@ss123")
        session_store = InMemoryRefreshSessions()

        with (
            patch("services.auth.main.get_user_by_email", AsyncMock(return_value=fake_user)),
            patch("services.auth.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.auth.main.create_refresh_session", AsyncMock(side_effect=session_store.create)),
            patch("services.auth.main.rotate_refresh_session", AsyncMock(side_effect=session_store.rotate)),
        ):
            login_resp = app_client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "SecureP@ss123"},
            )
            initial_refresh_token = login_resp.json()["tokens"]["refresh_token"]

            refresh_resp = app_client.post(
                "/auth/refresh",
                json={"refresh_token": initial_refresh_token},
            )
            reuse_resp = app_client.post(
                "/auth/refresh",
                json={"refresh_token": initial_refresh_token},
            )

        assert login_resp.status_code == 200
        assert refresh_resp.status_code == 200
        assert reuse_resp.status_code == 401
        assert reuse_resp.json()["error"]["code"] == "invalid_refresh_token"

        refreshed_token = refresh_resp.json()["tokens"]["refresh_token"]
        assert refreshed_token != initial_refresh_token

        old_claims = decode_token(initial_refresh_token, settings.jwt_secret, expected_type="refresh")
        new_claims = decode_token(refreshed_token, settings.jwt_secret, expected_type="refresh")
        assert old_claims["jti"] != new_claims["jti"]

    def test_logout_revokes_refresh_session(self, client):
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user("alice@example.com", "SecureP@ss123")
        session_store = InMemoryRefreshSessions()

        with (
            patch("services.auth.main.get_user_by_email", AsyncMock(return_value=fake_user)),
            patch("services.auth.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.auth.main.create_refresh_session", AsyncMock(side_effect=session_store.create)),
            patch("services.auth.main.rotate_refresh_session", AsyncMock(side_effect=session_store.rotate)),
            patch("services.auth.main.revoke_refresh_session", AsyncMock(side_effect=session_store.revoke)),
        ):
            login_resp = app_client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "SecureP@ss123"},
            )
            refresh_token = login_resp.json()["tokens"]["refresh_token"]

            logout_resp = app_client.post(
                "/auth/logout",
                json={"refresh_token": refresh_token},
            )
            reuse_resp = app_client.post(
                "/auth/refresh",
                json={"refresh_token": refresh_token},
            )

        assert logout_resp.status_code == 200
        assert logout_resp.json() == {"message": "Session revoked."}
        assert reuse_resp.status_code == 401
        assert reuse_resp.json()["error"]["code"] == "invalid_refresh_token"


class TestProtectedEndpoints:
    def test_missing_bearer_token_returns_contract_401(self, client):
        app_client, *_ = client

        resp = app_client.get("/auth/roles/admin")

        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "authentication_required"
        assert set(body.keys()) == {"error"}
        assert set(body["error"].keys()) == {"code", "message"}

    def test_invalid_bearer_token_returns_contract_401(self, client):
        app_client, *_ = client

        resp = app_client.get(
            "/auth/roles/admin",
            headers=_auth_headers("not-a-valid-jwt"),
        )

        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "invalid_token"
        assert set(body.keys()) == {"error"}
        assert set(body["error"].keys()) == {"code", "message"}

    @pytest.mark.parametrize(
        ("role", "path", "expected_status"),
        [
            ("user", "/auth/roles/user", 200),
            ("user", "/auth/roles/seller", 403),
            ("seller", "/auth/roles/seller", 200),
            ("seller", "/auth/roles/admin", 403),
            ("admin", "/auth/roles/admin", 200),
            ("admin", "/auth/roles/auditor", 200),
            ("auditor", "/auth/roles/auditor", 200),
            ("auditor", "/auth/roles/user", 403),
        ],
    )
    def test_role_enforcement(self, client, role, path, expected_status):
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user("alice@example.com", "SecureP@ss123", role=role)
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with patch("services.auth.main.get_user_by_id", AsyncMock(return_value=fake_user)):
            resp = app_client.get(path, headers=_auth_headers(access_token))

        assert resp.status_code == expected_status
        body = resp.json()

        if expected_status == 200:
            assert body["status"] == "allowed"
            assert body["actor_role"] == role
        else:
            assert body["error"]["code"] == "forbidden"
            assert set(body.keys()) == {"error"}
            assert set(body["error"].keys()) == {"code", "message"}

    def test_me_returns_authenticated_user(self, client):
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user("alice@example.com", "SecureP@ss123", role="seller")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with patch("services.auth.main.get_user_by_id", AsyncMock(return_value=fake_user)):
            resp = app_client.get("/auth/me", headers=_auth_headers(access_token))

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(fake_user.id)
        assert body["email"] == fake_user.email
        assert body["role"] == "seller"


# ---------------------------------------------------------------------------
# POST /auth/nostr
# ---------------------------------------------------------------------------

class TestNostrAuth:
    def test_nostr_login_first_time_creates_user(self, client):
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user(email=None, password="")
        
        with (
            patch("services.auth.main.validate_nostr_event", MagicMock(return_value=None)),
            patch("services.auth.main.get_nostr_identity_by_pubkey", AsyncMock(return_value=None)),
            patch("services.auth.main.create_nostr_user", AsyncMock(return_value=fake_user)),
            patch("services.auth.main.create_nostr_identity", AsyncMock(return_value=None)),
            patch("services.auth.main.create_refresh_session", AsyncMock(return_value=None)),
        ):
            resp = app_client.post(
                "/auth/nostr",
                json={
                    "pubkey": "a" * 64,
                    "signed_event": {
                        "id": "b" * 64,
                        "kind": 22242,
                        "created_at": 1234567890,
                        "content": "Sign-in challenge: 123",
                        "sig": "c" * 128
                    }
                }
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "user" in body
        assert "tokens" in body
        assert body["user"]["email"] is None
        _assert_token_structure(body["tokens"])

    def test_nostr_login_existing_identity_returns_tokens(self, client):
        app_client, fake_conn, settings = client
        fake_user = _make_fake_user(email="test@nostr.com", password="")
        fake_identity = MagicMock(user_id=fake_user.id)
        
        with (
            patch("services.auth.main.validate_nostr_event", MagicMock(return_value=None)),
            patch("services.auth.main.get_nostr_identity_by_pubkey", AsyncMock(return_value=fake_identity)),
            patch("services.auth.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.auth.main.create_refresh_session", AsyncMock(return_value=None)),
        ):
            resp = app_client.post(
                "/auth/nostr",
                json={
                    "pubkey": "a" * 64,
                    "signed_event": {
                        "id": "b" * 64,
                        "kind": 22242,
                        "created_at": 1234567890,
                        "content": "Sign-in challenge: 123",
                        "sig": "c" * 128
                    }
                }
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["user"]["email"] == "test@nostr.com"

    def test_nostr_login_invalid_signature_returns_401(self, client):
        app_client, fake_conn, settings = client
        from services.auth.nostr_utils import NostrValidationError
        
        with (
            patch("services.auth.main.validate_nostr_event", MagicMock(side_effect=NostrValidationError("Bad sig"))),
        ):
            resp = app_client.post(
                "/auth/nostr",
                json={
                    "pubkey": "a" * 64,
                    "signed_event": {
                        "id": "b" * 64,
                        "kind": 22242,
                        "created_at": 1234567890,
                        "content": "Sign-in challenge: 123",
                        "sig": "c" * 128
                    }
                }
            )

        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_credentials"
        assert "Bad sig" in resp.json()["error"]["message"]
