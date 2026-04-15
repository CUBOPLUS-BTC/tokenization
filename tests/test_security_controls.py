from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def test_sensitive_data_filter_redacts_plaintext_secrets_and_private_keys():
    from services.common.security import SensitiveDataFilter

    record = logging.LogRecord(
        name="security-test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="secret=test-secret jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature key=%s",
        args=("a" * 64,),
        exc_info=None,
    )
    record.payload = {
        "wallet_encryption_key": "super-secret-value",
        "nostr_private_key": "b" * 64,
    }

    SensitiveDataFilter().filter(record)

    assert "test-secret" not in str(record.msg)
    assert "[REDACTED]" in str(record.msg)
    assert record.args == ("[REDACTED]",)
    assert record.payload["wallet_encryption_key"] == "[REDACTED]"
    assert record.payload["nostr_private_key"] == "[REDACTED]"


def test_admin_sensitive_endpoint_rate_limits_repeat_requests():
    settings = {
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
        "JWT_SECRET": "test-secret-key-for-security-tests",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
        "RATE_LIMIT_WINDOW_SECONDS": "60",
        "RATE_LIMIT_WRITE_REQUESTS": "10",
        "RATE_LIMIT_SENSITIVE_REQUESTS": "1",
    }

    fake_conn = AsyncMock()

    @asynccontextmanager
    async def _fake_connect():
        yield fake_conn

    with patch.dict(os.environ, settings, clear=False):
        for module_name in (
            "services.admin.main",
            "services.admin.db",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.admin.main as admin_main
        from services.auth.jwt_utils import issue_token_pair

        fake_engine = MagicMock()
        fake_engine.connect = _fake_connect
        fake_engine.dispose = AsyncMock()
        admin_main._engine = fake_engine
        app = admin_main.app
        app.router.lifespan_context = None

        admin_user = type(
            "AdminUser",
            (),
            {
                "id": "d1bbf917-7665-4f0f-a8bf-2e4bb073c8ad",
                "email": "admin@example.com",
                "display_name": "Admin",
                "role": "admin",
                "created_at": datetime.now(tz=timezone.utc),
                "updated_at": datetime.now(tz=timezone.utc),
                "deleted_at": None,
                "totp_secret": None,
            },
        )()
        token = issue_token_pair(
            user_id=admin_user.id,
            role="admin",
            wallet_id=None,
            secret=admin_main.settings.jwt_secret,
        ).access_token

        treasury_entry = type(
            "TreasuryEntry",
            (),
            {
                "id": "31e7e1cf-66fd-4e89-a2c2-1a5cfd3546f5",
                "type": "disbursement",
                "amount_sat": 5000,
                "balance_after_sat": 10000,
                "source_trade_id": None,
                "description": "ops",
                "created_at": datetime.now(tz=timezone.utc),
            },
        )()

        with (
            patch("services.admin.main.get_user_by_id", AsyncMock(return_value=admin_user)),
            patch("services.admin.main.disburse_treasury", AsyncMock(return_value=treasury_entry)),
            patch("services.admin.main.record_audit_event", AsyncMock()),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            first = client.post(
                "/treasury/disburse",
                json={"amount_sat": 5000, "description": "ops"},
                headers={"Authorization": f"Bearer {token}", "X-Request-ID": "req-1"},
            )
            second = client.post(
                "/treasury/disburse",
                json={"amount_sat": 5000, "description": "ops"},
                headers={"Authorization": f"Bearer {token}", "X-Request-ID": "req-2"},
            )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limit_exceeded"
    assert second.headers["X-Request-ID"] == "req-2"
