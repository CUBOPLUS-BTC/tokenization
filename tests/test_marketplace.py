from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from typing import Any, NamedTuple
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch
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
    status: str = "settled",
    created_at: datetime | None = None,
    settled_at: datetime | None = None,
) -> dict[str, Any]:
    now = created_at or datetime.now(tz=timezone.utc)
    return {
        "id": uuid.uuid4(),
        "buy_order_id": buy_order_id,
        "sell_order_id": sell_order_id,
        "token_id": token_id,
        "quantity": quantity,
        "price_sat": price_sat,
        "total_sat": quantity * price_sat,
        "fee_sat": 0,
        "status": status,
        "created_at": now,
        "settled_at": settled_at if settled_at is not None or status != "settled" else now,
    }


def _make_escrow(
    *,
    trade_id: uuid.UUID,
    locked_amount_sat: int,
    multisig_address: str = "bcrt1q7l6jz2x2jl6xgttfpq3pyf6r0e84x4z0my2k7m0h5lyd0h00w7ksjg7tfj",
    buyer_pubkey: str = "02" + "11" * 32,
    seller_pubkey: str = "02" + "22" * 32,
    platform_pubkey: str = "02" + "33" * 32,
    funding_txid: str | None = None,
    status: str = "created",
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    return {
        "id": uuid.uuid4(),
        "trade_id": trade_id,
        "multisig_address": multisig_address,
        "buyer_pubkey": buyer_pubkey,
        "seller_pubkey": seller_pubkey,
        "platform_pubkey": platform_pubkey,
        "locked_amount_sat": locked_amount_sat,
        "funding_txid": funding_txid,
        "release_txid": None,
        "status": status,
        "expires_at": expires_at or now,
        "created_at": now,
        "updated_at": now,
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
        status="pending",
        settled_at=None,
    )
    escrow_row = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
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
        patch(
            "services.marketplace.main.create_trade_escrow",
            AsyncMock(return_value=(trade_row, escrow_row)),
        ) as create_trade_escrow_mock,
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

    create_trade_escrow_mock.assert_awaited_once()
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
            "status": "pending",
            "settled_at": None,
            "escrow_id": str(escrow_row["id"]),
            "multisig_address": escrow_row["multisig_address"],
            "escrow_status": "created",
            "escrow_expires_at": escrow_row["expires_at"].isoformat().replace("+00:00", "Z"),
        },
    )


def test_place_buy_order_partially_matches_multiple_resting_sell_orders_and_emits_events(client):
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
    partially_filled_buy_order = {
        **created_buy_order,
        "filled_quantity": 3,
        "status": "partially_filled",
    }
    filled_buy_order = {
        **created_buy_order,
        "filled_quantity": 10,
        "status": "filled",
    }
    first_sell_order = _make_order(
        user_id=uuid.uuid4(),
        token_id=token_id,
        side="sell",
        quantity=3,
        price_sat=99_000,
    )
    second_sell_order = _make_order(
        user_id=uuid.uuid4(),
        token_id=token_id,
        side="sell",
        quantity=7,
        price_sat=100_000,
    )
    first_trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=created_buy_order["id"],
        sell_order_id=first_sell_order["id"],
        quantity=3,
        price_sat=99_000,
        status="pending",
        settled_at=None,
    )
    second_trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=created_buy_order["id"],
        sell_order_id=second_sell_order["id"],
        quantity=7,
        price_sat=100_000,
        status="pending",
        settled_at=None,
    )
    first_escrow_row = _make_escrow(
        trade_id=first_trade_row["id"],
        locked_amount_sat=first_trade_row["total_sat"],
    )
    second_escrow_row = _make_escrow(
        trade_id=second_trade_row["id"],
        locked_amount_sat=second_trade_row["total_sat"],
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
            AsyncMock(
                side_effect=[
                    created_buy_order,
                    partially_filled_buy_order,
                    partially_filled_buy_order,
                    filled_buy_order,
                    filled_buy_order,
                ]
            ),
        ),
        patch(
            "services.marketplace.main.find_best_match",
            AsyncMock(side_effect=[first_sell_order, second_sell_order]),
        ),
        patch(
            "services.marketplace.main.create_trade_escrow",
            AsyncMock(side_effect=[(first_trade_row, first_escrow_row), (second_trade_row, second_escrow_row)]),
        ) as create_trade_escrow_mock,
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
    assert response.json()["order"]["status"] == "filled"
    assert response.json()["order"]["filled_quantity"] == 10
    create_trade_escrow_mock.assert_has_awaits(
        [
            call(
                ANY,
                buy_order=created_buy_order,
                sell_order=first_sell_order,
                quantity=3,
                price_sat=99_000,
            ),
            call(
                ANY,
                buy_order=partially_filled_buy_order,
                sell_order=second_sell_order,
                quantity=7,
                price_sat=100_000,
            ),
        ]
    )
    publish_mock.assert_has_awaits(
        [
            call(
                "trade.matched",
                {
                    "event": "trade_matched",
                    "trade_id": str(first_trade_row["id"]),
                    "token_id": str(token_id),
                    "buy_order_id": str(created_buy_order["id"]),
                    "sell_order_id": str(first_sell_order["id"]),
                    "buyer_id": str(fake_user.id),
                    "seller_id": str(first_sell_order["user_id"]),
                    "quantity": 3,
                    "price_sat": 99_000,
                    "total_sat": 297_000,
                    "fee_sat": 0,
                    "status": "pending",
                    "settled_at": None,
                    "escrow_id": str(first_escrow_row["id"]),
                    "multisig_address": first_escrow_row["multisig_address"],
                    "escrow_status": "created",
                    "escrow_expires_at": first_escrow_row["expires_at"].isoformat().replace("+00:00", "Z"),
                },
            ),
            call(
                "trade.matched",
                {
                    "event": "trade_matched",
                    "trade_id": str(second_trade_row["id"]),
                    "token_id": str(token_id),
                    "buy_order_id": str(created_buy_order["id"]),
                    "sell_order_id": str(second_sell_order["id"]),
                    "buyer_id": str(fake_user.id),
                    "seller_id": str(second_sell_order["user_id"]),
                    "quantity": 7,
                    "price_sat": 100_000,
                    "total_sat": 700_000,
                    "fee_sat": 0,
                    "status": "pending",
                    "settled_at": None,
                    "escrow_id": str(second_escrow_row["id"]),
                    "multisig_address": second_escrow_row["multisig_address"],
                    "escrow_status": "created",
                    "escrow_expires_at": second_escrow_row["expires_at"].isoformat().replace("+00:00", "Z"),
                },
            ),
        ]
    )


def test_place_sell_order_matches_resting_buy_order_at_resting_price(client):
    app_client, marketplace_main, settings = client
    fake_user = _make_fake_user(role="seller")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    token_id = uuid.uuid4()
    seller_wallet = _make_wallet(fake_user.id, onchain=0, lightning=0)
    created_sell_order = _make_order(
        user_id=fake_user.id,
        token_id=token_id,
        side="sell",
        quantity=4,
        price_sat=100_000,
    )
    filled_sell_order = {
        **created_sell_order,
        "filled_quantity": 4,
        "status": "filled",
    }
    resting_buy_order = _make_order(
        user_id=uuid.uuid4(),
        token_id=token_id,
        side="buy",
        quantity=10,
        price_sat=105_000,
    )
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=resting_buy_order["id"],
        sell_order_id=created_sell_order["id"],
        quantity=4,
        price_sat=105_000,
        status="pending",
        settled_at=None,
    )
    escrow_row = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
    )
    publish_mock = AsyncMock(return_value=None)

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_token_by_id", AsyncMock(return_value={"id": token_id})),
        patch("services.marketplace.main.get_wallet_by_user_id", AsyncMock(return_value=seller_wallet)),
        patch("services.marketplace.main.get_token_balance_for_user", AsyncMock(return_value={"balance": 10})),
        patch("services.marketplace.main.get_reserved_sell_quantity", AsyncMock(return_value=0)),
        patch("services.marketplace.main.create_order", AsyncMock(return_value=created_sell_order)),
        patch(
            "services.marketplace.main.get_order_by_id",
            AsyncMock(side_effect=[created_sell_order, filled_sell_order, filled_sell_order]),
        ),
        patch("services.marketplace.main.find_best_match", AsyncMock(return_value=resting_buy_order)),
        patch(
            "services.marketplace.main.create_trade_escrow",
            AsyncMock(return_value=(trade_row, escrow_row)),
        ) as create_trade_escrow_mock,
        patch.object(marketplace_main._event_bus, "publish", publish_mock),
    ):
        response = app_client.post(
            "/orders",
            headers=_auth_headers(access_token),
            json={
                "token_id": str(token_id),
                "side": "sell",
                "quantity": 4,
                "price_sat": 100_000,
            },
        )

    assert response.status_code == 201
    assert response.json()["order"]["status"] == "filled"
    assert response.json()["order"]["filled_quantity"] == 4
    create_trade_escrow_mock.assert_awaited_once_with(
        ANY,
        buy_order=resting_buy_order,
        sell_order=created_sell_order,
        quantity=4,
        price_sat=105_000,
    )
    publish_mock.assert_awaited_once_with(
        "trade.matched",
        {
            "event": "trade_matched",
            "trade_id": str(trade_row["id"]),
            "token_id": str(token_id),
            "buy_order_id": str(resting_buy_order["id"]),
            "sell_order_id": str(created_sell_order["id"]),
            "buyer_id": str(resting_buy_order["user_id"]),
            "seller_id": str(fake_user.id),
            "quantity": 4,
            "price_sat": 105_000,
            "total_sat": 420_000,
            "fee_sat": 0,
            "status": "pending",
            "settled_at": None,
            "escrow_id": str(escrow_row["id"]),
            "multisig_address": escrow_row["multisig_address"],
            "escrow_status": "created",
            "escrow_expires_at": escrow_row["expires_at"].isoformat().replace("+00:00", "Z"),
        },
    )


def test_create_trade_escrow_persists_multisig_details_and_locks_seller_tokens(marketplace_settings):
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
        status="pending",
        settled_at=None,
    )
    escrow_row = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
    )
    buyer_pubkey = "02" + "44" * 32
    seller_pubkey = "02" + "55" * 32
    platform_pubkey = "02" + "66" * 32
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(side_effect=[_FetchOneResult(trade_row), _FetchOneResult(escrow_row)])
    fake_conn.commit = AsyncMock()
    fake_conn.rollback = AsyncMock()

    with (
        patch.object(marketplace_db, "_resolve_escrow_pubkey", AsyncMock(side_effect=[buyer_pubkey, seller_pubkey])),
        patch.object(marketplace_db, "_platform_escrow_pubkey", return_value=platform_pubkey),
        patch.object(marketplace_db, "decrement_token_balance", AsyncMock()) as decrement_balance_mock,
        patch.object(marketplace_db, "apply_order_fill", AsyncMock()) as apply_fill_mock,
    ):
        result_trade, result_escrow = asyncio.run(
            marketplace_db.create_trade_escrow(
                fake_conn,
                buy_order=buy_order,
                sell_order=sell_order,
                quantity=10,
                price_sat=100_000,
            )
        )

    assert result_trade == trade_row
    assert result_escrow == escrow_row
    decrement_balance_mock.assert_awaited_once_with(
        fake_conn,
        user_id=seller_id,
        token_id=token_id,
        quantity=10,
    )
    assert apply_fill_mock.await_count == 2
    trade_insert_stmt = fake_conn.execute.await_args_list[0].args[0]
    escrow_insert_stmt = fake_conn.execute.await_args_list[1].args[0]
    trade_params = trade_insert_stmt.compile().params
    escrow_params = escrow_insert_stmt.compile().params
    assert trade_params["status"] == "pending"
    assert escrow_params["multisig_address"].startswith("bcrt1q")
    assert escrow_params["buyer_pubkey"] == buyer_pubkey
    assert escrow_params["seller_pubkey"] == seller_pubkey
    assert escrow_params["platform_pubkey"] == platform_pubkey
    assert escrow_params["locked_amount_sat"] == trade_row["total_sat"]
    fake_conn.commit.assert_awaited_once()
    fake_conn.rollback.assert_not_awaited()


def test_mark_escrow_funded_updates_trade_and_escrow_status(marketplace_settings):
    with patch.dict(os.environ, marketplace_settings, clear=False):
        for module_name in (
            "services.marketplace.db",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.marketplace.db as marketplace_db

    trade_id = uuid.uuid4()
    token_id = uuid.uuid4()
    updated_trade = {
        **_make_trade(
            token_id=token_id,
            buy_order_id=uuid.uuid4(),
            sell_order_id=uuid.uuid4(),
            quantity=10,
            price_sat=100_000,
            status="pending",
            settled_at=None,
        ),
        "id": trade_id,
        "status": "escrowed",
    }
    updated_escrow = {
        **_make_escrow(
            trade_id=trade_id,
            locked_amount_sat=1_000_000,
            funding_txid="ab" * 32,
            status="funded",
        ),
        "funding_txid": "ab" * 32,
        "status": "funded",
    }
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(side_effect=[_FetchOneResult(updated_escrow), _FetchOneResult(updated_trade)])
    fake_conn.commit = AsyncMock()
    fake_conn.rollback = AsyncMock()

    trade_row, escrow_row = asyncio.run(
        marketplace_db.mark_escrow_funded(
            fake_conn,
            trade_id=trade_id,
            funding_txid="ab" * 32,
        )
    )

    assert trade_row == updated_trade
    assert escrow_row == updated_escrow
    fake_conn.commit.assert_awaited_once()
    fake_conn.rollback.assert_not_awaited()


def test_get_escrow_details_refreshes_funding_status_and_emits_event(client):
    app_client, marketplace_main, settings = client
    fake_user = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    token_id = uuid.uuid4()
    buy_order = _make_order(
        user_id=fake_user.id,
        token_id=token_id,
        side="buy",
        quantity=10,
        price_sat=100_000,
    )
    sell_order = _make_order(
        user_id=uuid.uuid4(),
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
        status="pending",
        settled_at=None,
    )
    funded_trade_row = {**trade_row, "status": "escrowed"}
    created_escrow = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
        status="created",
        funding_txid=None,
    )
    funded_escrow = {
        **created_escrow,
        "status": "funded",
        "funding_txid": "cd" * 32,
    }
    publish_mock = AsyncMock(return_value=None)

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(side_effect=[buy_order, sell_order])),
        patch("services.marketplace.main.get_escrow_by_trade_id", AsyncMock(return_value=created_escrow)),
        patch(
            "services.marketplace.main._refresh_escrow_funding",
            AsyncMock(return_value=(funded_trade_row, funded_escrow, True)),
        ),
        patch.object(marketplace_main._event_bus, "publish", publish_mock),
    ):
        response = app_client.get(
            f"/escrows/{trade_row['id']}",
            headers=_auth_headers(access_token),
        )

    assert response.status_code == 200
    assert response.json() == {
        "escrow": {
            "id": str(funded_escrow["id"]),
            "trade_id": str(trade_row["id"]),
            "multisig_address": funded_escrow["multisig_address"],
            "locked_amount_sat": 1_000_000,
            "funding_txid": "cd" * 32,
            "status": "funded",
            "expires_at": funded_escrow["expires_at"].isoformat().replace("+00:00", "Z"),
        }
    }
    publish_mock.assert_awaited_once_with(
        "escrow.funded",
        {
            "event": "escrow_funded",
            "trade_id": str(trade_row["id"]),
            "token_id": str(token_id),
            "escrow_id": str(funded_escrow["id"]),
            "buyer_id": str(fake_user.id),
            "seller_id": str(sell_order["user_id"]),
            "multisig_address": funded_escrow["multisig_address"],
            "locked_amount_sat": 1_000_000,
            "funding_txid": "cd" * 32,
            "status": "funded",
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


def test_apply_order_fill_caps_filled_quantity_at_total_quantity(marketplace_settings):
    with patch.dict(os.environ, marketplace_settings, clear=False):
        for module_name in (
            "services.marketplace.db",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.marketplace.db as marketplace_db

    order = _make_order(
        user_id=uuid.uuid4(),
        token_id=uuid.uuid4(),
        side="buy",
        quantity=10,
        price_sat=100_000,
        filled_quantity=7,
        status="partially_filled",
    )
    fake_conn = AsyncMock()
    updated_at = datetime(2026, 4, 14, 16, 0, tzinfo=timezone.utc)

    with patch.object(marketplace_db, "_utc_now", return_value=updated_at):
        asyncio.run(
            marketplace_db.apply_order_fill(
                fake_conn,
                order_row=order,
                quantity=9,
            )
        )

    stmt = fake_conn.execute.await_args.args[0]
    compiled_params = stmt.compile().params
    assert 10 in compiled_params.values()
    assert "filled" in compiled_params.values()
    assert updated_at in compiled_params.values()


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


def test_get_order_book_returns_aggregated_market_depth_and_stats(client):
    app_client, _, settings = client
    fake_user = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    token_id = uuid.uuid4()

    orders = [
        _make_order(
            user_id=uuid.uuid4(),
            token_id=token_id,
            side="buy",
            quantity=10,
            price_sat=100_000,
            filled_quantity=2,
            status="open",
        ),
        _make_order(
            user_id=uuid.uuid4(),
            token_id=token_id,
            side="buy",
            quantity=6,
            price_sat=100_000,
            filled_quantity=1,
            status="partially_filled",
        ),
        _make_order(
            user_id=uuid.uuid4(),
            token_id=token_id,
            side="buy",
            quantity=12,
            price_sat=99_500,
            status="open",
        ),
        _make_order(
            user_id=uuid.uuid4(),
            token_id=token_id,
            side="sell",
            quantity=4,
            price_sat=101_000,
            status="open",
        ),
        _make_order(
            user_id=uuid.uuid4(),
            token_id=token_id,
            side="sell",
            quantity=9,
            price_sat=101_000,
            filled_quantity=3,
            status="partially_filled",
        ),
        _make_order(
            user_id=uuid.uuid4(),
            token_id=token_id,
            side="sell",
            quantity=7,
            price_sat=102_000,
            status="open",
        ),
        _make_order(
            user_id=uuid.uuid4(),
            token_id=token_id,
            side="buy",
            quantity=20,
            price_sat=98_000,
            status="filled",
        ),
    ]

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_token_by_id", AsyncMock(return_value={"id": token_id})),
        patch("services.marketplace.main.list_orders", AsyncMock(return_value=orders)),
        patch("services.marketplace.main.get_last_trade_price_for_token", AsyncMock(return_value=100_500)),
        patch("services.marketplace.main.get_trade_volume_24h", AsyncMock(return_value=21)),
    ):
        response = app_client.get(
            f"/orderbook/{token_id}",
            headers=_auth_headers(access_token),
        )

    assert response.status_code == 200
    assert response.json() == {
        "token_id": str(token_id),
        "bids": [
            {"price_sat": 100_000, "total_quantity": 13},
            {"price_sat": 99_500, "total_quantity": 12},
        ],
        "asks": [
            {"price_sat": 101_000, "total_quantity": 10},
            {"price_sat": 102_000, "total_quantity": 7},
        ],
        "last_trade_price_sat": 100_500,
        "volume_24h": 21,
    }


def test_get_trade_history_paginates_and_filters_by_token(client):
    app_client, _, settings = client
    fake_user = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    token_id = uuid.uuid4()
    other_token_id = uuid.uuid4()
    base_time = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)

    filtered_trades = [
        _make_trade(
            token_id=token_id,
            buy_order_id=uuid.uuid4(),
            sell_order_id=uuid.uuid4(),
            quantity=3,
            price_sat=101_000,
            created_at=base_time,
            settled_at=base_time,
        ),
        _make_trade(
            token_id=token_id,
            buy_order_id=uuid.uuid4(),
            sell_order_id=uuid.uuid4(),
            quantity=2,
            price_sat=100_500,
            created_at=base_time.replace(minute=30),
            settled_at=base_time.replace(minute=30),
        ),
        _make_trade(
            token_id=token_id,
            buy_order_id=uuid.uuid4(),
            sell_order_id=uuid.uuid4(),
            quantity=1,
            price_sat=100_000,
            created_at=base_time.replace(hour=14),
            settled_at=base_time.replace(hour=14),
        ),
    ]

    list_trades_mock = AsyncMock(return_value=filtered_trades)

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_token_by_id", AsyncMock(return_value={"id": token_id})),
        patch("services.marketplace.main.list_trades", list_trades_mock),
    ):
        first_response = app_client.get(
            f"/trades?token_id={token_id}&limit=2",
            headers=_auth_headers(access_token),
        )
        second_response = app_client.get(
            f"/trades?token_id={token_id}&cursor={filtered_trades[1]['id']}&limit=2",
            headers=_auth_headers(access_token),
        )

    assert first_response.status_code == 200
    assert first_response.json() == {
        "trades": [
            {
                "id": str(filtered_trades[0]["id"]),
                "token_id": str(token_id),
                "quantity": 3,
                "price_sat": 101_000,
                "total_sat": 303_000,
                "fee_sat": 0,
                "status": "settled",
                "created_at": filtered_trades[0]["created_at"].isoformat().replace("+00:00", "Z"),
                "settled_at": filtered_trades[0]["settled_at"].isoformat().replace("+00:00", "Z"),
            },
            {
                "id": str(filtered_trades[1]["id"]),
                "token_id": str(token_id),
                "quantity": 2,
                "price_sat": 100_500,
                "total_sat": 201_000,
                "fee_sat": 0,
                "status": "settled",
                "created_at": filtered_trades[1]["created_at"].isoformat().replace("+00:00", "Z"),
                "settled_at": filtered_trades[1]["settled_at"].isoformat().replace("+00:00", "Z"),
            },
        ],
        "next_cursor": str(filtered_trades[1]["id"]),
    }

    assert second_response.status_code == 200
    assert second_response.json() == {
        "trades": [
            {
                "id": str(filtered_trades[2]["id"]),
                "token_id": str(token_id),
                "quantity": 1,
                "price_sat": 100_000,
                "total_sat": 100_000,
                "fee_sat": 0,
                "status": "settled",
                "created_at": filtered_trades[2]["created_at"].isoformat().replace("+00:00", "Z"),
                "settled_at": filtered_trades[2]["settled_at"].isoformat().replace("+00:00", "Z"),
            }
        ],
        "next_cursor": None,
    }

    assert list_trades_mock.await_count == 2
    list_trades_mock.assert_any_await(ANY, token_id=token_id)

    for response in (first_response, second_response):
        payload = response.json()
        assert set(payload.keys()) == {"trades", "next_cursor"}
        for trade in payload["trades"]:
            assert set(trade.keys()) == {
                "id",
                "token_id",
                "quantity",
                "price_sat",
                "total_sat",
                "fee_sat",
                "status",
                "created_at",
                "settled_at",
            }
            assert trade["token_id"] != str(other_token_id)
