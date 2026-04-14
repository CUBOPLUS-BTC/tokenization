"""Acceptance tests for wallet on-chain operations and transaction history."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
import uuid
from collections import namedtuple
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from services.auth.jwt_utils import issue_token_pair


FakeUser = namedtuple(
    "FakeUser",
    [
        "id",
        "email",
        "display_name",
        "role",
        "created_at",
        "deleted_at",
        "totp_secret",
    ],
)

FakeWallet = namedtuple(
    "FakeWallet",
    [
        "id",
        "user_id",
        "onchain_balance_sat",
        "lightning_balance_sat",
        "encrypted_seed",
        "derivation_path",
        "created_at",
        "updated_at",
    ],
)

FakeTransaction = namedtuple(
    "FakeTransaction",
    [
        "id",
        "wallet_id",
        "type",
        "amount_sat",
        "direction",
        "status",
        "txid",
        "ln_payment_hash",
        "description",
        "created_at",
        "confirmed_at",
    ],
)


def _totp(secret: str, for_time: float) -> str:
    counter = int(for_time // 30)
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(binary % 1_000_000).zfill(6)


def _make_fake_user(*, totp_secret: str | None = None) -> FakeUser:
    return FakeUser(
        id=uuid.uuid4(),
        email="alice@example.com",
        display_name="Alice",
        role="user",
        created_at=datetime.now(tz=timezone.utc),
        deleted_at=None,
        totp_secret=totp_secret,
    )


def _make_fake_wallet(user_id: uuid.UUID, *, onchain_balance_sat: int = 250_000) -> FakeWallet:
    now = datetime.now(tz=timezone.utc)
    return FakeWallet(
        id=uuid.uuid4(),
        user_id=user_id,
        onchain_balance_sat=onchain_balance_sat,
        lightning_balance_sat=0,
        encrypted_seed=b"seed",
        derivation_path="m/86'/0'/0'",
        created_at=now,
        updated_at=now,
    )


def _make_fake_transaction(
    wallet_id: uuid.UUID,
    *,
    tx_type: str,
    amount_sat: int,
    direction: str,
    minutes_ago: int,
    description: str,
) -> FakeTransaction:
    created_at = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_ago)
    return FakeTransaction(
        id=uuid.uuid4(),
        wallet_id=wallet_id,
        type=tx_type,
        amount_sat=amount_sat,
        direction=direction,
        status="confirmed",
        txid=None,
        ln_payment_hash=None,
        description=description,
        created_at=created_at,
        confirmed_at=created_at,
    )


@pytest.fixture()
def fake_settings():
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
        "JWT_SECRET": "test-secret-key-for-wallet-tests",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
    }


@pytest.fixture()
def client(fake_settings):
    fake_conn = AsyncMock()

    @asynccontextmanager
    async def _fake_connect():
        yield fake_conn

    fake_engine = MagicMock()
    fake_engine.connect = _fake_connect
    fake_engine.dispose = AsyncMock()

    with patch.dict(os.environ, fake_settings, clear=False):
        for module_name in ("services.wallet.main", "wallet.main", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.wallet.main as wallet_main

        wallet_main._engine = fake_engine
        app = wallet_main.app
        app.router.lifespan_context = None

        yield TestClient(app, raise_server_exceptions=True), fake_conn, wallet_main.settings


def _auth_headers(access_token: str, *, two_fa_code: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    if two_fa_code is not None:
        headers["X-2FA-Code"] = two_fa_code
    return headers


def _issue_access_token(user: FakeUser, secret: str) -> str:
    return issue_token_pair(
        user_id=str(user.id),
        role=user.role,
        wallet_id=None,
        secret=secret,
    ).access_token


class TestOnchainDepositAddress:
    def test_user_can_request_new_onchain_deposit_address(self, client):
        app_client, _, settings = client
        fake_user = _make_fake_user()
        fake_wallet = _make_fake_wallet(fake_user.id)
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with (
            patch("services.wallet.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.wallet.main.get_or_create_wallet", AsyncMock(return_value=fake_wallet)),
        ):
            resp = app_client.post(
                "/wallet/onchain/address",
                headers=_auth_headers(access_token),
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["type"] == "taproot"
        assert body["address"].startswith("bcrt1p")


class TestOnchainWithdrawal:
    def test_user_can_withdraw_onchain_with_valid_2fa_code(self, client):
        app_client, _, settings = client
        secret = "JBSWY3DPEHPK3PXP"
        fake_user = _make_fake_user(totp_secret=secret)
        fake_wallet = _make_fake_wallet(fake_user.id, onchain_balance_sat=500_000)
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        now = 1_700_000_000.0
        valid_code = _totp(secret, now)
        created_row = FakeTransaction(
            id=uuid.uuid4(),
            wallet_id=fake_wallet.id,
            type="withdrawal",
            amount_sat=100_000,
            direction="out",
            status="pending",
            txid="a" * 64,
            ln_payment_hash=None,
            description="On-chain withdrawal to bc1qexampleaddress",
            created_at=datetime.now(tz=timezone.utc),
            confirmed_at=None,
        )

        with (
            patch("services.wallet.main.time.time", return_value=now),
            patch("services.wallet.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.wallet.main.get_or_create_wallet", AsyncMock(return_value=fake_wallet)),
            patch("services.wallet.main.create_onchain_withdrawal", AsyncMock(return_value=created_row)),
        ):
            resp = app_client.post(
                "/wallet/onchain/withdraw",
                headers=_auth_headers(access_token, two_fa_code=valid_code),
                json={
                    "address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    "amount_sat": 100_000,
                    "fee_rate_sat_vb": 5,
                },
            )

        assert resp.status_code == 200
        assert resp.json() == {
            "txid": "a" * 64,
            "amount_sat": 100_000,
            "fee_sat": 705,
            "status": "pending",
        }

    def test_withdrawal_rejects_invalid_2fa_code(self, client):
        app_client, _, settings = client
        secret = "JBSWY3DPEHPK3PXP"
        fake_user = _make_fake_user(totp_secret=secret)
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with patch("services.wallet.main.get_user_by_id", AsyncMock(return_value=fake_user)):
            resp = app_client.post(
                "/wallet/onchain/withdraw",
                headers=_auth_headers(access_token, two_fa_code="000000"),
                json={
                    "address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    "amount_sat": 100_000,
                    "fee_rate_sat_vb": 5,
                },
            )

        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_2fa_code"


class TestTransactionHistory:
    def test_history_supports_type_filtering(self, client):
        app_client, _, settings = client
        fake_user = _make_fake_user()
        fake_wallet = _make_fake_wallet(fake_user.id)
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        rows = [
            _make_fake_transaction(
                fake_wallet.id,
                tx_type="ln_receive",
                amount_sat=25_000,
                direction="in",
                minutes_ago=2,
                description="Lightning deposit",
            ),
            _make_fake_transaction(
                fake_wallet.id,
                tx_type="withdrawal",
                amount_sat=90_000,
                direction="out",
                minutes_ago=1,
                description="On-chain withdrawal",
            ),
        ]

        with (
            patch("services.wallet.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.wallet.main.get_or_create_wallet", AsyncMock(return_value=fake_wallet)),
            patch("services.wallet.main.list_wallet_transactions", AsyncMock(return_value=rows)),
        ):
            resp = app_client.get(
                "/wallet/transactions?type=withdrawal",
                headers=_auth_headers(access_token),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["next_cursor"] is None
        assert len(body["transactions"]) == 1
        assert body["transactions"][0]["type"] == "withdrawal"
        assert body["transactions"][0]["amount_sat"] == 90_000

    def test_history_supports_cursor_pagination(self, client):
        app_client, _, settings = client
        fake_user = _make_fake_user()
        fake_wallet = _make_fake_wallet(fake_user.id)
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        newest = _make_fake_transaction(
            fake_wallet.id,
            tx_type="withdrawal",
            amount_sat=30_000,
            direction="out",
            minutes_ago=1,
            description="Newest tx",
        )
        middle = _make_fake_transaction(
            fake_wallet.id,
            tx_type="deposit",
            amount_sat=60_000,
            direction="in",
            minutes_ago=2,
            description="Middle tx",
        )
        oldest = _make_fake_transaction(
            fake_wallet.id,
            tx_type="fee",
            amount_sat=500,
            direction="out",
            minutes_ago=3,
            description="Oldest tx",
        )
        rows = [oldest, newest, middle]

        with (
            patch("services.wallet.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.wallet.main.get_or_create_wallet", AsyncMock(return_value=fake_wallet)),
            patch("services.wallet.main.list_wallet_transactions", AsyncMock(return_value=rows)),
        ):
            first_page = app_client.get(
                "/wallet/transactions?limit=2",
                headers=_auth_headers(access_token),
            )
            second_page = app_client.get(
                f"/wallet/transactions?limit=2&cursor={middle.id}",
                headers=_auth_headers(access_token),
            )

        assert first_page.status_code == 200
        first_body = first_page.json()
        assert [item["id"] for item in first_body["transactions"]] == [
            str(newest.id),
            str(middle.id),
        ]
        assert first_body["next_cursor"] == str(middle.id)

        assert second_page.status_code == 200
        second_body = second_page.json()
        assert [item["id"] for item in second_body["transactions"]] == [str(oldest.id)]
        assert second_body["next_cursor"] is None
