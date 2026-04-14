"""Unit tests for POST /auth/register and POST /auth/login.

These tests run entirely in-process using an AsyncMock database layer,
so no real PostgreSQL connection is required.

Run with:
    pytest tests/test_auth.py -v
"""
from __future__ import annotations

import uuid
from collections import namedtuple
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
import bcrypt

# ---------------------------------------------------------------------------
# Patch the settings + engine BEFORE importing the app so that it never tries
# to touch a real database or config files.
# ---------------------------------------------------------------------------



# A fake user row that mimics a SQLAlchemy Row
FakeUser = namedtuple(
    "FakeUser",
    ["id", "email", "password_hash", "display_name", "role", "created_at"],
)


def _make_fake_user(email: str, password: str) -> FakeUser:
    return FakeUser(
        id=uuid.uuid4(),
        email=email,
        password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        display_name="Alice",
        role="user",
        created_at=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_settings():
    """Minimal settings object that the auth service needs."""
    s = MagicMock()
    s.service_name = "auth"
    s.env_profile = "local"
    s.service_host = "0.0.0.0"
    s.service_port = 8000
    s.database_url = "postgresql://user:pass@localhost/testdb"
    s.jwt_secret = "test-secret-key-for-unit-tests"
    return s


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

    with (
        patch("services.auth.main.get_settings", return_value=fake_settings),
        patch("services.auth.main.create_async_engine", return_value=fake_engine),
        patch("services.auth.main._engine", fake_engine),
    ):
        # Import app AFTER patching
        from services.auth.main import app

        # Override lifespan so it doesn't rebuild the engine
        app.router.lifespan_context = None  # disable lifespan for sync TestClient

        yield TestClient(app, raise_server_exceptions=True), fake_conn, fake_settings


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
