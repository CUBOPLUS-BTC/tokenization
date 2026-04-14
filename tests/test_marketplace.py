from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from typing import Any, NamedTuple
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import uuid

import pytest
from fastapi.testclient import TestClient

from services.auth.jwt_utils import issue_token_pair


class FakeUser(NamedTuple):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    created_at: datetime
    deleted_at: datetime | None


class _FetchOneResult:
    def __init__(self, row: object) -> None:
        self._row = row

    def fetchone(self) -> object:
        return self._row


def _make_fake_user(*, role: str = "user") -> FakeUser:
    return FakeUser(
        id=uuid.uuid4(),
        email="user@example.com",
        display_name="User",
        role=role,
        created_at=datetime.now(tz=timezone.utc),
        deleted_at=None,
    )


def _make_wallet(user_id: uuid.UUID, *, onchain: int, lightning: int) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "onchain_balance_sat": onchain,
        "lightning_balance_sat": lightning,
        "created_at": now,
        "updated_at": now,
    }


def _make_order(
    *,
    user_id: uuid.UUID,
    token_id: uuid.UUID,
    side: str,
    quantity: int,
    price_sat: int,
    filled_quantity: int = 0,
    status: str = "open",
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "token_id": token_id,
        "side": side,
        "quantity": quantity,
        "price_sat": price_sat,
        "filled_quantity": filled_quantity,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }


def _make_trade(
    *,
    token_id: uuid.UUID,
    buy_order_id: uuid.UUID,
    sell_order_id: uuid.UUID,
    quantity: int,
    price_sat: int,
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    return {
        "id": uuid.uuid4(),
        "buy_order_id": buy_order_id,
        "sell_order_id": sell_order_id,
        "token_id": token_id,
        "quantity": quantity,
        "price_sat": price_sat,
        "total_sat": quantity * price_sat,
        "fee_sat": 0,
        "status": "settled",
        "created_at": now,
        "settled_at": now,
    }


@pytest.fixture()
def marketplace_settings() -> dict[str, str]:
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
        "JWT_SECRET": "test-secret-key-for-marketplace-tests",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
    }


@pytest.fixture()
def client(marketplace_settings):
    fake_conn = AsyncMock()

    @asynccontextmanager
    async def _fake_connect():
        yield fake_conn

    fake_engine = MagicMock()
    fake_engine.connect = _fake_connect
    fake_engine.dispose = AsyncMock()

    with patch.dict(os.environ, marketplace_settings, clear=False):
        for module_name in (
            "services.marketplace.main",
            "services.marketplace.db",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.marketplace.main as marketplace_main

        marketplace_main._engine = fake_engine
        yield TestClient(marketplace_main.app, raise_server_exceptions=True), marketplace_main, marketplace_main.settings


def _issue_access_token(user: FakeUser, secret: str) -> str:
    return issue_token_pair(
        user_id=str(user.id),
        role=user.role,
        wallet_id=None,
        secret=secret,
    ).access_token


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def test_place_sell_order_rejects_when_reserved_balance_exhausts_holdings(client):
    app_client, _, settings = client
    fake_user = _make_fake_user(role="seller")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    token_id = uuid.uuid4()

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_token_by_id", AsyncMock(return_value={"id": token_id})),
        patch("services.marketplace.main.get_wallet_by_user_id", AsyncMock(return_value=_make_wallet(fake_user.id, onchain=10_000, lightning=0))),
        patch("services.marketplace.main.get_token_balance_for_user", AsyncMock(return_value={"balance": 50})),
        patch("services.marketplace.main.get_reserved_sell_quantity", AsyncMock(return_value=50)),
        patch("services.marketplace.main.create_order", AsyncMock()) as create_order_mock,
    ):
        response = app_client.post(
            "/orders",
            headers=_auth_headers(access_token),
            json={
                "token_id": str(token_id),
                "side": "sell",
                "quantity": 1,
                "price_sat": 100_000,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "insufficient_token_balance",
        "message": "Insufficient token balance for this sell order.",
    }
    create_order_mock.assert_not_called()


def test_place_buy_order_rejects_when_reserved_sats_exhaust_wallet_balance(client):
    app_client, _, settings = client
    fake_user = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    token_id = uuid.uuid4()

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_token_by_id", AsyncMock(return_value={"id": token_id})),
        patch(
            "services.marketplace.main.get_wallet_by_user_id",
            AsyncMock(return_value=_make_wallet(fake_user.id, onchain=700_000, lightning=200_000)),
        ),
        patch("services.marketplace.main.get_reserved_buy_commitment", AsyncMock(return_value=850_000)),
        patch("services.marketplace.main.create_order", AsyncMock()) as create_order_mock,
    ):
        response = app_client.post(
            "/orders",
            headers=_auth_headers(access_token),
            json={
                "token_id": str(token_id),
                "side": "buy",
                "quantity": 1,
                "price_sat": 100_000,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "insufficient_sats",
        "message": "Insufficient wallet balance for this buy order.",
    }
    create_order_mock.assert_not_called()


def test_place_buy_order_matches_resting_sell_order_and_emits_trade_event(client):
    app_client, marketplace_main, settings = client
    fake_user = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    token_id = uuid.uuid4()
    buyer_wallet = _make_wallet(fake_user.id, onchain=2_000_000, lightning=0)
    created_buy_order = _make_order(
        user_id=fake_user.id,
        token_id=token_id,
        side="buy",
        quantity=10,
        price_sat=100_000,
    )
    filled_buy_order = {
        **created_buy_order,
        "filled_quantity": 10,
        "status": "filled",
    }
    sell_order = _make_order(
        user_id=uuid.uuid4(),
        token_id=token_id,
        side="sell",
        quantity=10,
        price_sat=100_000,
    )
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=created_buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=10,
        price_sat=100_000,
    )
    publish_mock = AsyncMock(return_value=None)

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_token_by_id", AsyncMock(return_value={"id": token_id})),
        patch("services.marketplace.main.get_wallet_by_user_id", AsyncMock(return_value=buyer_wallet)),
        patch("services.marketplace.main.get_reserved_buy_commitment", AsyncMock(return_value=0)),
        patch("services.marketplace.main.create_order", AsyncMock(return_value=created_buy_order)),
        patch(
            "services.marketplace.main.get_order_by_id",
            AsyncMock(side_effect=[created_buy_order, filled_buy_order, filled_buy_order]),
        ),
        patch("services.marketplace.main.find_best_match", AsyncMock(return_value=sell_order)),
        patch("services.marketplace.main.settle_trade", AsyncMock(return_value=trade_row)) as settle_trade_mock,
        patch.object(marketplace_main._event_bus, "publish", publish_mock),
    ):
        response = app_client.post(
            "/orders",
            headers=_auth_headers(access_token),
            json={
                "token_id": str(token_id),
                "side": "buy",
                "quantity": 10,
                "price_sat": 100_000,
            },
        )

    assert response.status_code == 201
    body = response.json()["order"]
    assert body["status"] == "filled"
    assert body["filled_quantity"] == 10

    settle_trade_mock.assert_awaited_once()
    publish_mock.assert_awaited_once_with(
        "trade.matched",
        {
            "event": "trade_matched",
            "trade_id": str(trade_row["id"]),
            "token_id": str(token_id),
            "buy_order_id": str(created_buy_order["id"]),
            "sell_order_id": str(sell_order["id"]),
            "buyer_id": str(fake_user.id),
            "seller_id": str(sell_order["user_id"]),
            "quantity": 10,
            "price_sat": 100_000,
            "total_sat": 1_000_000,
            "fee_sat": 0,
            "status": "settled",
            "settled_at": trade_row["settled_at"].isoformat().replace("+00:00", "Z"),
        },
    )


def test_settle_trade_synchronizes_wallet_and_token_balances(marketplace_settings):
    with patch.dict(os.environ, marketplace_settings, clear=False):
        for module_name in (
            "services.marketplace.db",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.marketplace.db as marketplace_db

    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    token_id = uuid.uuid4()
    buyer_wallet = _make_wallet(buyer_id, onchain=2_000_000, lightning=100_000)
    seller_wallet = _make_wallet(seller_id, onchain=0, lightning=0)
    buy_order = _make_order(
        user_id=buyer_id,
        token_id=token_id,
        side="buy",
        quantity=10,
        price_sat=100_000,
    )
    sell_order = _make_order(
        user_id=seller_id,
        token_id=token_id,
        side="sell",
        quantity=10,
        price_sat=100_000,
    )
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=10,
        price_sat=100_000,
    )
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(return_value=_FetchOneResult(trade_row))
    fake_conn.commit = AsyncMock()

    with (
        patch.object(marketplace_db, "get_wallet_by_user_id", AsyncMock(side_effect=[buyer_wallet, seller_wallet])),
        patch.object(marketplace_db, "debit_wallet_balance", AsyncMock()) as debit_wallet_mock,
        patch.object(marketplace_db, "credit_wallet_balance", AsyncMock()) as credit_wallet_mock,
        patch.object(marketplace_db, "decrement_token_balance", AsyncMock()) as decrement_balance_mock,
        patch.object(marketplace_db, "increment_token_balance", AsyncMock()) as increment_balance_mock,
        patch.object(marketplace_db, "apply_order_fill", AsyncMock()) as apply_fill_mock,
    ):
        result = asyncio.run(
            marketplace_db.settle_trade(
                fake_conn,
                buy_order=buy_order,
                sell_order=sell_order,
                quantity=10,
                price_sat=100_000,
            )
        )

    assert result == trade_row
    debit_wallet_mock.assert_awaited_once_with(
        fake_conn,
        wallet_row=buyer_wallet,
        amount_sat=1_000_000,
    )
    credit_wallet_mock.assert_awaited_once_with(
        fake_conn,
        wallet_row=seller_wallet,
        amount_sat=1_000_000,
    )
    decrement_balance_mock.assert_awaited_once_with(
        fake_conn,
        user_id=seller_id,
        token_id=token_id,
        quantity=10,
    )
    increment_balance_mock.assert_awaited_once_with(
        fake_conn,
        user_id=buyer_id,
        token_id=token_id,
        quantity=10,
    )
    assert apply_fill_mock.await_count == 2
    fake_conn.commit.assert_awaited_once()


def test_cancel_order_allows_owner_to_cancel_partially_filled_order(client):
    app_client, _, settings = client
    fake_user = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    token_id = uuid.uuid4()
    existing_order = _make_order(
        user_id=fake_user.id,
        token_id=token_id,
        side="sell",
        quantity=10,
        price_sat=100_000,
        filled_quantity=4,
        status="partially_filled",
    )
    cancelled_order = {
        **existing_order,
        "status": "cancelled",
    }

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(return_value=existing_order)),
        patch("services.marketplace.main.cancel_order", AsyncMock(return_value=cancelled_order)) as cancel_order_mock,
    ):
        response = app_client.delete(
            f"/orders/{existing_order['id']}",
            headers=_auth_headers(access_token),
        )

    assert response.status_code == 200
    assert response.json() == {
        "order": {
            "id": str(existing_order["id"]),
            "status": "cancelled",
        }
    }
    cancel_order_mock.assert_awaited_once_with(
        ANY,
        order_id=existing_order["id"],
        user_id=str(fake_user.id),
    )
