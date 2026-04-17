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
from services.common.realtime import StreamEvent


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
    order_type: str = "limit",
    quantity: int,
    price_sat: int,
    trigger_price_sat: int | None = None,
    triggered_at: datetime | None = None,
    filled_quantity: int = 0,
    status: str = "open",
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "token_id": token_id,
        "side": side,
        "order_type": order_type,
        "quantity": quantity,
        "price_sat": price_sat,
        "trigger_price_sat": trigger_price_sat,
        "triggered_at": triggered_at,
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
    fee_sat: int = 0,
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
        "fee_sat": fee_sat,
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
    refund_txid: str | None = None,
    status: str = "created",
    expires_at: datetime | None = None,
    settlement_metadata: dict[str, Any] | None = None,
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
        "refund_txid": refund_txid,
        "status": status,
        "collected_signatures": None,
        "settlement_metadata": settlement_metadata,
        "expires_at": expires_at or now,
        "created_at": now,
        "updated_at": now,
    }


def _make_treasury_entry(
    *,
    amount_sat: int,
    balance_after_sat: int,
    source_trade_id: uuid.UUID | None = None,
    entry_type: str = "fee_income",
    description: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    return {
        "id": uuid.uuid4(),
        "source_trade_id": source_trade_id,
        "type": entry_type,
        "amount_sat": amount_sat,
        "balance_after_sat": balance_after_sat,
        "description": description,
        "created_at": now,
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
        "MARKETPLACE_ESCROW_WATCH_INTERVAL_SECONDS": "1",
        "MARKETPLACE_ESCROW_FEE_RESERVE_SAT": "0",
        "WALLET_ENCRYPTION_KEY": "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
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


async def _stream_events(events: list[StreamEvent]):
    for event in events:
        yield event


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
    audit_mock = AsyncMock()

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
        patch("services.marketplace.main.record_audit_event", audit_mock),
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
    audit_mock.assert_awaited_once_with(
        ANY,
        settings=settings,
        request=ANY,
        action="marketplace.order.place",
        actor_id=str(fake_user.id),
        actor_role="user",
        target_type="order",
        target_id=filled_buy_order["id"],
        metadata={
            "token_id": str(token_id),
            "side": "buy",
            "order_type": "limit",
            "quantity": 10,
            "price_sat": 100_000,
            "trigger_price_sat": None,
            "matched_trades": 1,
        },
    )


def test_place_stop_limit_order_remains_dormant_until_triggered(client):
    app_client, _, settings = client
    fake_user = _make_fake_user(role="seller")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    token_id = uuid.uuid4()
    dormant_order = _make_order(
        user_id=fake_user.id,
        token_id=token_id,
        side="sell",
        order_type="stop_limit",
        quantity=5,
        price_sat=95_000,
        trigger_price_sat=90_000,
        triggered_at=None,
    )

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_token_by_id", AsyncMock(return_value={"id": token_id})),
        patch("services.marketplace.main.get_wallet_by_user_id", AsyncMock(return_value=_make_wallet(fake_user.id, onchain=100_000, lightning=0))),
        patch("services.marketplace.main.get_token_balance_for_user", AsyncMock(return_value={"balance": 50})),
        patch("services.marketplace.main.get_reserved_sell_quantity", AsyncMock(return_value=0)),
        patch("services.marketplace.main.get_reference_price_for_token", AsyncMock(return_value=100_000)),
        patch("services.marketplace.main.create_order", AsyncMock(return_value=dormant_order)),
        patch("services.marketplace.main.record_audit_event", AsyncMock()),
    ):
        response = app_client.post(
            "/orders",
            headers=_auth_headers(access_token),
            json={
                "token_id": str(token_id),
                "side": "sell",
                "order_type": "stop_limit",
                "quantity": 5,
                "price_sat": 95_000,
                "trigger_price_sat": 90_000,
            },
        )

    assert response.status_code == 201
    body = response.json()["order"]
    assert body["order_type"] == "stop_limit"
    assert body["is_triggered"] is False
    assert body["trigger_price_sat"] == 90_000


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
    assert escrow_params["multisig_address"].startswith("el1")
    assert escrow_params["buyer_pubkey"] == buyer_pubkey
    assert escrow_params["seller_pubkey"] == seller_pubkey
    assert escrow_params["platform_pubkey"] == platform_pubkey
    assert escrow_params["locked_amount_sat"] == trade_row["total_sat"]
    assert escrow_params["settlement_metadata"]["unconfidential_address"].startswith("ert1")
    assert escrow_params["settlement_metadata"]["witness_script"]
    assert escrow_params["settlement_metadata"]["script_pubkey"]
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


def test_get_escrow_details_returns_current_escrow_without_side_effects(client):
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
        status="escrowed",
        settled_at=None,
    )
    funded_escrow = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
        status="funded",
        funding_txid="cd" * 32,
    )
    publish_mock = AsyncMock(return_value=None)

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(side_effect=[buy_order, sell_order])),
        patch("services.marketplace.main.get_escrow_by_trade_id", AsyncMock(return_value=funded_escrow)),
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
            "release_txid": None,
            "refund_txid": None,
            "status": "funded",
            "expires_at": funded_escrow["expires_at"].isoformat().replace("+00:00", "Z"),
            "settlement_metadata": None,
        }
    }
    publish_mock.assert_not_awaited()


def test_watch_escrows_once_marks_created_escrow_funded_and_publishes_event(client):
    _, marketplace_main, _ = client
    token_id = uuid.uuid4()
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    buy_order = _make_order(user_id=buyer_id, token_id=token_id, side="buy", quantity=5, price_sat=100_000)
    sell_order = _make_order(user_id=seller_id, token_id=token_id, side="sell", quantity=5, price_sat=100_000)
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=5,
        price_sat=100_000,
        status="pending",
        settled_at=None,
    )
    created_escrow = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
        status="created",
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    funded_escrow = {
        **created_escrow,
        "status": "funded",
        "funding_txid": "ab" * 32,
        "settlement_metadata": {"funding_inputs": [{"txid": "ab" * 32, "vout": 0}]},
    }
    funded_trade = {**trade_row, "status": "escrowed"}
    publish_mock = AsyncMock()

    with (
        patch("services.marketplace.main.list_escrows_by_status", AsyncMock(return_value=[created_escrow])),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(side_effect=[buy_order, sell_order])),
        patch(
            "services.marketplace.main._scan_escrow_funding",
            AsyncMock(return_value=marketplace_main.FundingObservation(txid="ab" * 32, total_amount_sat=500_000, utxos=[{"txid": "ab" * 32, "vout": 0}])),
        ),
        patch("services.marketplace.main.mark_escrow_funded", AsyncMock(return_value=(funded_trade, funded_escrow))) as mark_funded_mock,
        patch("services.marketplace.main._prepare_escrow_transaction_pset", AsyncMock(return_value=funded_escrow)),
        patch.object(marketplace_main._event_bus, "publish", publish_mock),
    ):
        asyncio.run(marketplace_main._watch_escrows_once())

    mark_funded_mock.assert_awaited_once()
    publish_mock.assert_awaited_once_with(
        "escrow.funded",
        {
            "event": "escrow_funded",
            "trade_id": str(trade_row["id"]),
            "token_id": str(token_id),
            "escrow_id": str(funded_escrow["id"]),
            "buyer_id": str(buyer_id),
            "seller_id": str(seller_id),
            "multisig_address": funded_escrow["multisig_address"],
            "locked_amount_sat": 500_000,
            "funding_txid": "ab" * 32,
            "status": "funded",
        },
    )


def test_watch_escrows_once_expires_unfunded_escrow_and_restores_state(client):
    _, marketplace_main, _ = client
    token_id = uuid.uuid4()
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    buy_order = _make_order(user_id=buyer_id, token_id=token_id, side="buy", quantity=5, price_sat=100_000)
    sell_order = _make_order(user_id=seller_id, token_id=token_id, side="sell", quantity=5, price_sat=100_000)
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=5,
        price_sat=100_000,
        status="pending",
        settled_at=None,
    )
    expired_at = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)
    created_escrow = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
        status="created",
        expires_at=expired_at,
    )
    expired_trade = {**trade_row, "status": "cancelled"}
    expired_escrow = {**created_escrow, "status": "expired"}

    with (
        patch("services.marketplace.main.list_escrows_by_status", AsyncMock(return_value=[created_escrow])),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(side_effect=[buy_order, sell_order])),
        patch("services.marketplace.main._record_settlement_failure", AsyncMock()) as failure_mock,
        patch(
            "services.marketplace.main.expire_unfunded_escrow",
            AsyncMock(return_value=(expired_trade, expired_escrow)),
        ) as expire_mock,
        patch("services.marketplace.main.record_audit_event", AsyncMock()) as audit_mock,
        patch("services.marketplace.main._publish_escrow_expired", AsyncMock()) as publish_expired_mock,
        patch("services.marketplace.main._scan_escrow_funding", AsyncMock(return_value=None)),
    ):
        asyncio.run(marketplace_main._watch_escrows_once())

    failure_mock.assert_awaited_once()
    expire_mock.assert_awaited_once()
    audit_mock.assert_awaited_once()
    publish_expired_mock.assert_awaited_once()


def test_sign_escrow_release_requires_seller_ack_before_buyer_approval(client):
    app_client, _, settings = client
    buyer = _make_fake_user(role="user")
    access_token = _issue_access_token(buyer, settings.jwt_secret)
    token_id = uuid.uuid4()
    buy_order = _make_order(user_id=buyer.id, token_id=token_id, side="buy", quantity=2, price_sat=100_000)
    sell_order = _make_order(user_id=uuid.uuid4(), token_id=token_id, side="sell", quantity=2, price_sat=100_000)
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=2,
        price_sat=100_000,
        status="escrowed",
        settled_at=None,
    )
    funded_escrow = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
        funding_txid="ab" * 32,
        status="funded",
        settlement_metadata={"release_unsigned_pset": "base-pset"},
    )

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=buyer)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(side_effect=[buy_order, sell_order])),
        patch("services.marketplace.main.get_escrow_by_trade_id", AsyncMock(return_value=funded_escrow)),
    ):
        response = app_client.post(
            f"/escrows/{trade_row['id']}/sign",
            headers=_auth_headers(access_token),
            json={},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "escrow_state_conflict"


def test_sign_escrow_release_records_seller_signature_and_moves_to_inspection_pending(client):
    app_client, _, settings = client
    seller = _make_fake_user(role="user")
    access_token = _issue_access_token(seller, settings.jwt_secret)
    token_id = uuid.uuid4()
    buy_order = _make_order(user_id=uuid.uuid4(), token_id=token_id, side="buy", quantity=2, price_sat=100_000)
    sell_order = _make_order(user_id=seller.id, token_id=token_id, side="sell", quantity=2, price_sat=100_000)
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=2,
        price_sat=100_000,
        status="escrowed",
        settled_at=None,
    )
    funded_escrow = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
        funding_txid="ab" * 32,
        status="funded",
        settlement_metadata={"release_unsigned_pset": "base-pset"},
    )
    inspection_pending_escrow = {
        **funded_escrow,
        "status": "inspection_pending",
        "collected_signatures": {"release": {"seller": {"signature": "sig"}}},
        "settlement_metadata": {"release_unsigned_pset": "base-pset", "release_signed_pset": "seller-pset"},
    }
    fake_pset = MagicMock()
    fake_pset.to_string.return_value = "seller-pset"

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=seller)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(side_effect=[buy_order, sell_order])),
        patch("services.marketplace.main.get_escrow_by_trade_id", AsyncMock(return_value=funded_escrow)),
        patch("services.marketplace.main._prepare_escrow_transaction_pset", AsyncMock(return_value=funded_escrow)),
        patch("services.marketplace.main.resolve_escrow_signing_material", AsyncMock(return_value=b"seller-secret")),
        patch("services.marketplace.main.derive_private_key", return_value=MagicMock()),
        patch("services.marketplace.main.PSET.from_string", return_value=fake_pset),
        patch("services.marketplace.main._liquid_rpc_client", MagicMock()),
        patch("services.marketplace.main.record_escrow_signature", AsyncMock(return_value=inspection_pending_escrow)) as record_sig_mock,
        patch("services.marketplace.main.process_escrow_signature", AsyncMock()) as process_sig_mock,
        patch("services.marketplace.main.record_audit_event", AsyncMock()),
    ):
        response = app_client.post(
            f"/escrows/{trade_row['id']}/sign",
            headers=_auth_headers(access_token),
            json={},
        )

    assert response.status_code == 200
    assert response.json()["escrow"]["status"] == "inspection_pending"
    assert response.json()["escrow"]["refund_txid"] is None
    record_sig_mock.assert_awaited_once()
    process_sig_mock.assert_not_awaited()


def test_sign_escrow_release_broadcasts_after_buyer_approval(client):
    app_client, marketplace_main, settings = client
    buyer = _make_fake_user(role="user")
    access_token = _issue_access_token(buyer, settings.jwt_secret)
    token_id = uuid.uuid4()
    buy_order = _make_order(user_id=buyer.id, token_id=token_id, side="buy", quantity=2, price_sat=100_000)
    sell_order = _make_order(user_id=uuid.uuid4(), token_id=token_id, side="sell", quantity=2, price_sat=100_000)
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=2,
        price_sat=100_000,
        status="escrowed",
        settled_at=None,
    )
    inspection_pending_escrow = _make_escrow(
        trade_id=trade_row["id"],
        locked_amount_sat=trade_row["total_sat"],
        funding_txid="ab" * 32,
        status="inspection_pending",
        settlement_metadata={"release_unsigned_pset": "base-pset", "release_signed_pset": "seller-pset"},
    )
    inspection_pending_escrow["collected_signatures"] = {
        "release": {
            "seller": {"signature": "seller-sig", "signed_at": "2026-04-16T00:00:00Z"},
        }
    }
    released_escrow = {
        **inspection_pending_escrow,
        "status": "released",
        "release_txid": "cd" * 32,
        "collected_signatures": {
            "release": {
                "seller": {"signature": "seller-sig", "signed_at": "2026-04-16T00:00:00Z"},
                "buyer": {"signature": "buyer-sig", "signed_at": "2026-04-16T00:05:00Z"},
            }
        },
    }
    settled_trade = {**trade_row, "status": "settled", "settled_at": datetime.now(tz=timezone.utc)}
    base_pset = MagicMock()
    base_pset.to_string.return_value = "buyer-signed-pset"
    final_pset = MagicMock()
    final_pset.to_string.return_value = "final-pset"
    final_pset.serialize.return_value.hex.return_value = "deadbeef"
    finalized_tx = MagicMock()
    finalized_tx.serialize.return_value.hex.return_value = "deadbeef"
    publish_mock = AsyncMock()

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=buyer)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(side_effect=[buy_order, sell_order])),
        patch("services.marketplace.main.get_escrow_by_trade_id", AsyncMock(return_value=inspection_pending_escrow)),
        patch("services.marketplace.main._prepare_escrow_transaction_pset", AsyncMock(return_value=inspection_pending_escrow)),
        patch("services.marketplace.main.resolve_escrow_signing_material", AsyncMock(return_value=b"buyer-secret")),
        patch("services.marketplace.main.derive_private_key", return_value=MagicMock()),
        patch("services.marketplace.main.PSET.from_string", side_effect=[base_pset, final_pset]),
        patch("services.marketplace.main.process_escrow_signature", AsyncMock(return_value=(settled_trade, released_escrow))) as process_sig_mock,
        patch("services.marketplace.main.record_escrow_signature", AsyncMock()) as record_sig_mock,
        patch("services.marketplace.main.record_audit_event", AsyncMock()),
        patch("services.marketplace.main._liquid_rpc_client", MagicMock(walletprocesspsbt=AsyncMock(return_value={"psbt": "final-pset"}), sendrawtransaction=AsyncMock(return_value="cd" * 32))),
        patch("embit.liquid.finalizer.finalize_psbt", return_value=finalized_tx),
        patch.object(marketplace_main._event_bus, "publish", publish_mock),
    ):
        response = app_client.post(
            f"/escrows/{trade_row['id']}/sign",
            headers=_auth_headers(access_token),
            json={},
        )

    assert response.status_code == 200
    assert response.json()["escrow"]["status"] == "released"
    assert response.json()["escrow"]["release_txid"] == "cd" * 32
    record_sig_mock.assert_not_awaited()
    process_sig_mock.assert_awaited_once()
    publish_mock.assert_awaited_once()


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
        fee_sat=5_000,
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
        patch.object(marketplace_db, "record_trade_fee_income", AsyncMock()) as record_fee_mock,
    ):
        result = asyncio.run(
            marketplace_db.settle_trade(
                fake_conn,
                buy_order=buy_order,
                sell_order=sell_order,
                quantity=10,
                price_sat=100_000,
                fee_sat=5_000,
            )
        )

    assert result == trade_row
    debit_wallet_mock.assert_awaited_once_with(
        fake_conn,
        wallet_row=buyer_wallet,
        amount_sat=1_005_000,
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
    record_fee_mock.assert_awaited_once_with(fake_conn, trade_row=trade_row)
    fake_conn.commit.assert_awaited_once()


def test_record_trade_fee_income_creates_traceable_running_ledger_entry(marketplace_settings):
    with patch.dict(os.environ, marketplace_settings, clear=False):
        for module_name in (
            "services.marketplace.db",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.marketplace.db as marketplace_db

    trade_row = _make_trade(
        token_id=uuid.uuid4(),
        buy_order_id=uuid.uuid4(),
        sell_order_id=uuid.uuid4(),
        quantity=4,
        price_sat=125_000,
        fee_sat=7_500,
    )
    latest_entry = _make_treasury_entry(amount_sat=3_000, balance_after_sat=23_000)
    inserted_entry = _make_treasury_entry(
        amount_sat=7_500,
        balance_after_sat=30_500,
        source_trade_id=trade_row["id"],
        description=f"Fee income from trade {trade_row['id']}",
    )
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(
        side_effect=[
            _FetchOneResult(None),
            _FetchOneResult(latest_entry),
            _FetchOneResult(inserted_entry),
        ]
    )

    result = asyncio.run(
        marketplace_db.record_trade_fee_income(
            fake_conn,
            trade_row=trade_row,
        )
    )

    assert result == inserted_entry
    insert_stmt = fake_conn.execute.await_args_list[2].args[0]
    insert_params = insert_stmt.compile().params
    assert insert_params["type"] == "fee_income"
    assert insert_params["amount_sat"] == 7_500
    assert insert_params["balance_after_sat"] == 30_500
    assert insert_params["source_trade_id"] == trade_row["id"]
    assert str(trade_row["id"]) in insert_params["description"]


def test_process_escrow_signature_release_moves_tokens_and_records_treasury_income(marketplace_settings):
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
    trade_id = uuid.uuid4()
    buy_order = _make_order(user_id=buyer_id, token_id=token_id, side="buy", quantity=3, price_sat=200_000)
    sell_order = _make_order(user_id=seller_id, token_id=token_id, side="sell", quantity=3, price_sat=200_000)
    trade_row = {
        **_make_trade(
            token_id=token_id,
            buy_order_id=buy_order["id"],
            sell_order_id=sell_order["id"],
            quantity=3,
            price_sat=200_000,
            fee_sat=12_000,
            status="escrowed",
            settled_at=None,
        ),
        "id": trade_id,
        "status": "escrowed",
    }
    escrow_row = _make_escrow(trade_id=trade_id, locked_amount_sat=600_000, funding_txid="ab" * 32, status="funded")
    settled_at = datetime(2026, 4, 14, 18, 0, tzinfo=timezone.utc)
    released_escrow = {
        **escrow_row,
        "status": "released",
        "release_txid": "cd" * 32,
        "collected_signatures": {
            "release": {
                "seller": {"signature": "seller-sig"},
                "buyer": {"signature": "buyer-sig"},
            }
        },
    }
    settled_trade = {
        **trade_row,
        "status": "settled",
        "settled_at": settled_at,
    }
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(
        side_effect=[
            _FetchOneResult(released_escrow),
            _FetchOneResult(settled_trade),
        ]
    )
    fake_conn.commit = AsyncMock()
    fake_conn.rollback = AsyncMock()

    with (
        patch.object(marketplace_db, "increment_token_balance", AsyncMock()) as increment_mock,
        patch.object(marketplace_db, "record_trade_fee_income", AsyncMock()) as record_fee_mock,
    ):
        trade_result, escrow_result = asyncio.run(
            marketplace_db.process_escrow_signature(
                fake_conn,
                escrow_row=escrow_row,
                trade_row=trade_row,
                buy_order=buy_order,
                sell_order=sell_order,
                collected_signatures=released_escrow["collected_signatures"],
                release_txid="cd" * 32,
                settlement_metadata={"release_signed_pset": "cHNldA=="},
            )
        )

    assert trade_result == settled_trade
    assert escrow_result == released_escrow
    increment_mock.assert_awaited_once_with(fake_conn, user_id=buyer_id, token_id=token_id, quantity=3)
    record_fee_mock.assert_awaited_once_with(fake_conn, trade_row=settled_trade)
    fake_conn.commit.assert_awaited_once()
    fake_conn.rollback.assert_not_awaited()


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


def test_price_websocket_streams_live_updates_for_a_token(client):
    app_client, marketplace_main, _ = client
    token_id = uuid.uuid4()
    initial_snapshot = {
        "token_id": str(token_id),
        "last_price_sat": 100_000,
        "bid": 99_500,
        "ask": 100_500,
        "volume_24h": 20,
        "timestamp": "2026-04-15T01:00:00Z",
    }
    updated_snapshot = {
        "token_id": str(token_id),
        "last_price_sat": 101_000,
        "bid": 100_500,
        "ask": 101_500,
        "volume_24h": 25,
        "timestamp": "2026-04-15T01:00:05Z",
    }
    stream_event = StreamEvent(
        topic="trade.matched",
        event_id="1713142805000-0",
        payload={"token_id": str(token_id)},
        positions={"trade.matched": "1713142805000-0"},
    )

    with (
        patch("services.marketplace.main._price_snapshot", AsyncMock(side_effect=[initial_snapshot, updated_snapshot])),
        patch.object(
            marketplace_main._realtime_feed,
            "listen",
            side_effect=lambda *args, **kwargs: _stream_events([stream_event]),
        ) as listen_mock,
    ):
        with app_client.websocket_connect(f"/ws/prices/{token_id}") as websocket:
            first_message = websocket.receive_json()
            second_message = websocket.receive_json()

    assert first_message == {
        "event": "price_update",
        "data": initial_snapshot,
    }
    assert second_message == {
        "event": "price_update",
        "id": "1713142805000-0",
        "data": updated_snapshot,
    }
    listen_mock.assert_called_once_with(["trade.matched"], resume_from=None)


def test_notifications_websocket_requires_authentication_and_filters_personal_events(client):
    app_client, marketplace_main, settings = client
    fake_user = _make_fake_user(role="seller")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    stream_events = [
        StreamEvent(
            topic="trade.matched",
            event_id="1713142805000-0",
            payload={
                "trade_id": str(uuid.uuid4()),
                "token_id": str(uuid.uuid4()),
                "buy_order_id": str(uuid.uuid4()),
                "sell_order_id": str(uuid.uuid4()),
                "buyer_id": str(uuid.uuid4()),
                "seller_id": str(fake_user.id),
                "quantity": 4,
                "price_sat": 100_000,
                "status": "pending",
            },
            positions={
                "trade.matched": "1713142805000-0",
                "escrow.funded": "$",
                "ai.evaluation.complete": "$",
            },
        ),
        StreamEvent(
            topic="escrow.funded",
            event_id="1713142810000-0",
            payload={
                "trade_id": str(uuid.uuid4()),
                "token_id": str(uuid.uuid4()),
                "escrow_id": str(uuid.uuid4()),
                "buyer_id": str(uuid.uuid4()),
                "seller_id": str(uuid.uuid4()),
                "funding_txid": "ab" * 32,
                "status": "funded",
            },
            positions={
                "trade.matched": "1713142805000-0",
                "escrow.funded": "1713142810000-0",
                "ai.evaluation.complete": "$",
            },
        ),
        StreamEvent(
            topic="ai.evaluation.complete",
            event_id="1713142815000-0",
            payload={
                "asset_id": str(uuid.uuid4()),
                "owner_id": str(fake_user.id),
                "ai_score": 83.5,
                "projected_roi": 9.2,
                "status": "approved",
                "completed_at": "2026-04-15T01:00:15Z",
            },
            positions={
                "trade.matched": "1713142805000-0",
                "escrow.funded": "1713142810000-0",
                "ai.evaluation.complete": "1713142815000-0",
            },
        ),
    ]

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch.object(
            marketplace_main._realtime_feed,
            "listen",
            side_effect=lambda *args, **kwargs: _stream_events(stream_events),
        ),
    ):
        with app_client.websocket_connect(f"/ws/notifications?access_token={access_token}") as websocket:
            first_message = websocket.receive_json()
            second_message = websocket.receive_json()

    assert first_message["event"] == "order_filled"
    assert first_message["data"]["order_id"] == stream_events[0].payload["sell_order_id"]
    assert first_message["data"]["filled_quantity"] == 4
    assert first_message["resume_token"]

    assert second_message == {
        "event": "ai_evaluation_complete",
        "id": "ai.evaluation.complete:1713142815000-0",
        "resume_token": second_message["resume_token"],
        "data": {
            "asset_id": stream_events[2].payload["asset_id"],
            "ai_score": 83.5,
            "projected_roi": 9.2,
            "status": "approved",
            "completed_at": "2026-04-15T01:00:15Z",
        },
    }
    assert second_message["resume_token"] != first_message["resume_token"]


def test_notifications_websocket_replays_from_resume_token(client):
    app_client, marketplace_main, settings = client
    fake_user = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_user, settings.jwt_secret)
    trade_positions = {
        "trade.matched": "1713142900000-0",
        "escrow.funded": "$",
        "ai.evaluation.complete": "$",
    }
    replay_positions = {
        "trade.matched": "1713142900000-0",
        "escrow.funded": "1713142910000-0",
        "ai.evaluation.complete": "$",
    }
    first_event = StreamEvent(
        topic="trade.matched",
        event_id="1713142900000-0",
        payload={
            "trade_id": str(uuid.uuid4()),
            "token_id": str(uuid.uuid4()),
            "buy_order_id": str(uuid.uuid4()),
            "sell_order_id": str(uuid.uuid4()),
            "buyer_id": str(fake_user.id),
            "seller_id": str(uuid.uuid4()),
            "quantity": 2,
            "price_sat": 99_000,
            "status": "pending",
        },
        positions=trade_positions,
    )
    replay_event = StreamEvent(
        topic="escrow.funded",
        event_id="1713142910000-0",
        payload={
            "trade_id": first_event.payload["trade_id"],
            "token_id": first_event.payload["token_id"],
            "escrow_id": str(uuid.uuid4()),
            "buyer_id": str(fake_user.id),
            "seller_id": str(uuid.uuid4()),
            "funding_txid": "cd" * 32,
            "status": "funded",
        },
        positions=replay_positions,
    )

    listen_calls: list[dict[str, Any] | None] = []

    def _listen(*args, **kwargs):
        listen_calls.append(kwargs.get("resume_from"))
        if len(listen_calls) == 1:
            return _stream_events([first_event])
        return _stream_events([replay_event])

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_user)),
        patch.object(marketplace_main._realtime_feed, "listen", side_effect=_listen),
    ):
        with app_client.websocket_connect(f"/ws/notifications?access_token={access_token}") as websocket:
            first_message = websocket.receive_json()

        resume_token = first_message["resume_token"]

        with app_client.websocket_connect(
            f"/ws/notifications?access_token={access_token}&resume_token={resume_token}"
        ) as websocket:
            replay_message = websocket.receive_json()

    assert listen_calls == [None, trade_positions]
    assert replay_message == {
        "event": "escrow_funded",
        "id": "escrow.funded:1713142910000-0",
        "resume_token": replay_message["resume_token"],
        "data": {
            "trade_id": replay_event.payload["trade_id"],
            "token_id": replay_event.payload["token_id"],
            "escrow_id": replay_event.payload["escrow_id"],
            "txid": "cd" * 32,
            "status": "funded",
        },
    }

# ---------------------------------------------------------------------------
# Dispute helpers
# ---------------------------------------------------------------------------


def _make_dispute(
    *,
    trade_id: uuid.UUID,
    opened_by: uuid.UUID,
    reason: str = "Payment not received",
    status: str = "open",
    resolution: str | None = None,
    resolved_by: uuid.UUID | None = None,
    resolved_at: datetime | None = None,
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    return {
        "id": uuid.uuid4(),
        "trade_id": trade_id,
        "opened_by": opened_by,
        "reason": reason,
        "status": status,
        "resolution": resolution,
        "resolved_by": resolved_by,
        "resolved_at": resolved_at,
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# Dispute API tests
# ---------------------------------------------------------------------------


def test_open_dispute_allows_trade_participant_to_dispute_escrowed_trade(client):
    app_client, _, settings = client
    fake_buyer = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_buyer, settings.jwt_secret)
    token_id = uuid.uuid4()
    buy_order = _make_order(
        user_id=fake_buyer.id,
        token_id=token_id,
        side="buy",
        quantity=5,
        price_sat=100_000,
    )
    sell_order = _make_order(
        user_id=uuid.uuid4(),
        token_id=token_id,
        side="sell",
        quantity=5,
        price_sat=100_000,
    )
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=5,
        price_sat=100_000,
        status="escrowed",
        settled_at=None,
    )
    dispute_row = _make_dispute(trade_id=trade_row["id"], opened_by=fake_buyer.id)

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_buyer)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch(
            "services.marketplace.main.get_order_by_id",
            AsyncMock(side_effect=[buy_order, sell_order]),
        ),
        patch("services.marketplace.main.get_dispute_by_trade_id", AsyncMock(return_value=None)),
        patch("services.marketplace.main.open_dispute", AsyncMock(return_value=dispute_row)) as open_dispute_mock,
    ):
        response = app_client.post(
            f"/trades/{trade_row['id']}/dispute",
            headers=_auth_headers(access_token),
            json={"reason": "Payment not received"},
        )

    assert response.status_code == 201
    body = response.json()["dispute"]
    assert body["trade_id"] == str(trade_row["id"])
    assert body["opened_by"] == str(fake_buyer.id)
    assert body["reason"] == "Payment not received"
    assert body["status"] == "open"
    assert body["resolution"] is None
    open_dispute_mock.assert_awaited_once_with(
        ANY,
        trade_id=trade_row["id"],
        opened_by=str(fake_buyer.id),
        reason="Payment not received",
    )


def test_open_dispute_rejects_non_participant(client):
    app_client, _, settings = client
    outsider = _make_fake_user(role="user")
    access_token = _issue_access_token(outsider, settings.jwt_secret)
    token_id = uuid.uuid4()
    buy_order = _make_order(
        user_id=uuid.uuid4(),
        token_id=token_id,
        side="buy",
        quantity=5,
        price_sat=100_000,
    )
    sell_order = _make_order(
        user_id=uuid.uuid4(),
        token_id=token_id,
        side="sell",
        quantity=5,
        price_sat=100_000,
    )
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=5,
        price_sat=100_000,
        status="escrowed",
        settled_at=None,
    )

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=outsider)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch(
            "services.marketplace.main.get_order_by_id",
            AsyncMock(side_effect=[buy_order, sell_order]),
        ),
    ):
        response = app_client.post(
            f"/trades/{trade_row['id']}/dispute",
            headers=_auth_headers(access_token),
            json={"reason": "Trying to dispute someone else's trade"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_open_dispute_rejects_trade_not_in_escrowed_state(client):
    app_client, _, settings = client
    fake_buyer = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_buyer, settings.jwt_secret)
    token_id = uuid.uuid4()
    buy_order = _make_order(
        user_id=fake_buyer.id,
        token_id=token_id,
        side="buy",
        quantity=5,
        price_sat=100_000,
    )
    sell_order = _make_order(
        user_id=uuid.uuid4(),
        token_id=token_id,
        side="sell",
        quantity=5,
        price_sat=100_000,
    )
    pending_trade = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=5,
        price_sat=100_000,
        status="pending",
        settled_at=None,
    )

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_buyer)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=pending_trade)),
        patch(
            "services.marketplace.main.get_order_by_id",
            AsyncMock(side_effect=[buy_order, sell_order]),
        ),
    ):
        response = app_client.post(
            f"/trades/{pending_trade['id']}/dispute",
            headers=_auth_headers(access_token),
            json={"reason": "Should not work"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "trade_state_conflict"


def test_open_dispute_rejects_duplicate_dispute(client):
    app_client, _, settings = client
    fake_buyer = _make_fake_user(role="user")
    access_token = _issue_access_token(fake_buyer, settings.jwt_secret)
    token_id = uuid.uuid4()
    buy_order = _make_order(
        user_id=fake_buyer.id,
        token_id=token_id,
        side="buy",
        quantity=5,
        price_sat=100_000,
    )
    sell_order = _make_order(
        user_id=uuid.uuid4(),
        token_id=token_id,
        side="sell",
        quantity=5,
        price_sat=100_000,
    )
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=buy_order["id"],
        sell_order_id=sell_order["id"],
        quantity=5,
        price_sat=100_000,
        status="escrowed",
        settled_at=None,
    )
    existing_dispute = _make_dispute(trade_id=trade_row["id"], opened_by=fake_buyer.id)

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=fake_buyer)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=trade_row)),
        patch(
            "services.marketplace.main.get_order_by_id",
            AsyncMock(side_effect=[buy_order, sell_order]),
        ),
        patch("services.marketplace.main.get_dispute_by_trade_id", AsyncMock(return_value=existing_dispute)),
    ):
        response = app_client.post(
            f"/trades/{trade_row['id']}/dispute",
            headers=_auth_headers(access_token),
            json={"reason": "Duplicate attempt"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dispute_already_exists"


def test_resolve_dispute_release_uses_seller_and_platform_signatures(client):
    app_client, _, settings = client
    admin_user = _make_fake_user(role="admin")
    access_token = _issue_access_token(admin_user, settings.jwt_secret)
    token_id = uuid.uuid4()
    trade_id = uuid.uuid4()
    buy_order = _make_order(user_id=uuid.uuid4(), token_id=token_id, side="buy", quantity=5, price_sat=100_000)
    sell_order = _make_order(user_id=uuid.uuid4(), token_id=token_id, side="sell", quantity=5, price_sat=100_000)
    disputed_trade = {
        **_make_trade(
            token_id=token_id,
            buy_order_id=buy_order["id"],
            sell_order_id=sell_order["id"],
            quantity=5,
            price_sat=100_000,
            status="disputed",
            settled_at=None,
        ),
        "id": trade_id,
        "status": "disputed",
    }
    open_dispute_row = _make_dispute(trade_id=trade_id, opened_by=uuid.uuid4())
    resolved_trade = {**disputed_trade, "status": "settled"}
    disputed_escrow = _make_escrow(
        trade_id=trade_id,
        locked_amount_sat=500_000,
        status="disputed",
        settlement_metadata={"release_unsigned_pset": "base-pset"},
    )
    resolved_escrow = {**disputed_escrow, "status": "released", "release_txid": "cd" * 32}
    resolved_dispute = {
        **open_dispute_row,
        "status": "resolved",
        "resolution": "release",
        "resolved_by": admin_user.id,
        "resolved_at": datetime.now(tz=timezone.utc),
    }
    initial_pset = MagicMock()
    initial_pset.to_string.return_value = "seller-platform-pset"
    final_pset = MagicMock()
    final_pset.to_string.return_value = "final-pset"
    finalized_tx = MagicMock()
    finalized_tx.serialize.return_value.hex.return_value = "deadbeef"
    platform_signer = MagicMock()
    platform_signer.private_key.return_value = "11" * 32

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=admin_user)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=disputed_trade)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(side_effect=[buy_order, sell_order])),
        patch("services.marketplace.main.get_dispute_by_trade_id", AsyncMock(return_value=open_dispute_row)),
        patch("services.marketplace.main.get_escrow_by_trade_id", AsyncMock(return_value=disputed_escrow)),
        patch("services.marketplace.main._prepare_escrow_transaction_pset", AsyncMock(return_value=disputed_escrow)),
        patch("services.marketplace.main.resolve_escrow_signing_material", AsyncMock(return_value=b"seller-secret")),
        patch("services.marketplace.main.derive_private_key", return_value=MagicMock()),
        patch("services.marketplace.main.build_platform_signer", return_value=platform_signer),
        patch("services.marketplace.main.PSET.from_string", side_effect=[initial_pset, final_pset]),
        patch("services.marketplace.main._liquid_rpc_client", MagicMock(walletprocesspsbt=AsyncMock(return_value={"psbt": "final-pset"}), sendrawtransaction=AsyncMock(return_value="cd" * 32))),
        patch("embit.liquid.finalizer.finalize_psbt", return_value=finalized_tx),
        patch(
            "services.marketplace.main.resolve_dispute",
            AsyncMock(return_value=(resolved_dispute, resolved_trade, resolved_escrow)),
        ) as resolve_dispute_mock,
        patch("services.marketplace.main.record_audit_event", AsyncMock()),
    ):
        response = app_client.post(
            f"/trades/{trade_id}/dispute/resolve",
            headers=_auth_headers(access_token),
            json={"resolution": "release"},
        )

    assert response.status_code == 200
    body = response.json()["dispute"]
    assert body["status"] == "resolved"
    assert body["resolution"] == "release"
    assert body["resolved_by"] == str(admin_user.id)
    resolve_kwargs = resolve_dispute_mock.await_args.kwargs
    assert resolve_kwargs["trade_id"] == trade_id
    assert resolve_kwargs["resolved_by"] == str(admin_user.id)
    assert resolve_kwargs["resolution"] == "release"
    assert resolve_kwargs["resolution_txid"] == "cd" * 32
    assert "platform" in resolve_kwargs["collected_signatures"]["release"]
    assert "seller" in resolve_kwargs["collected_signatures"]["release"]


def test_resolve_dispute_refund_sets_escrow_to_refunded_via_buyer_and_platform(client):
    app_client, _, settings = client
    admin_user = _make_fake_user(role="admin")
    access_token = _issue_access_token(admin_user, settings.jwt_secret)
    token_id = uuid.uuid4()
    trade_id = uuid.uuid4()
    buy_order = _make_order(user_id=uuid.uuid4(), token_id=token_id, side="buy", quantity=5, price_sat=100_000)
    sell_order = _make_order(user_id=uuid.uuid4(), token_id=token_id, side="sell", quantity=5, price_sat=100_000)
    disputed_trade = {
        **_make_trade(
            token_id=token_id,
            buy_order_id=buy_order["id"],
            sell_order_id=sell_order["id"],
            quantity=5,
            price_sat=100_000,
            status="disputed",
            settled_at=None,
        ),
        "id": trade_id,
        "status": "disputed",
    }
    open_dispute_row = _make_dispute(trade_id=trade_id, opened_by=uuid.uuid4())
    disputed_escrow = _make_escrow(
        trade_id=trade_id,
        locked_amount_sat=500_000,
        status="disputed",
        settlement_metadata={"refund_unsigned_pset": "refund-pset"},
    )
    resolved_trade = {**disputed_trade, "status": "cancelled"}
    refunded_escrow = {**disputed_escrow, "status": "refunded", "refund_txid": "ef" * 32}
    resolved_dispute = {
        **open_dispute_row,
        "status": "resolved",
        "resolution": "refund",
        "resolved_by": admin_user.id,
        "resolved_at": datetime.now(tz=timezone.utc),
    }
    initial_pset = MagicMock()
    initial_pset.to_string.return_value = "buyer-platform-pset"
    final_pset = MagicMock()
    final_pset.to_string.return_value = "refund-final-pset"
    finalized_tx = MagicMock()
    finalized_tx.serialize.return_value.hex.return_value = "beadfeed"
    platform_signer = MagicMock()
    platform_signer.private_key.return_value = "11" * 32

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=admin_user)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=disputed_trade)),
        patch("services.marketplace.main.get_order_by_id", AsyncMock(side_effect=[buy_order, sell_order])),
        patch("services.marketplace.main.get_dispute_by_trade_id", AsyncMock(return_value=open_dispute_row)),
        patch("services.marketplace.main.get_escrow_by_trade_id", AsyncMock(return_value=disputed_escrow)),
        patch("services.marketplace.main._prepare_escrow_transaction_pset", AsyncMock(return_value=disputed_escrow)),
        patch("services.marketplace.main.resolve_escrow_signing_material", AsyncMock(return_value=b"buyer-secret")),
        patch("services.marketplace.main.derive_private_key", return_value=MagicMock()),
        patch("services.marketplace.main.build_platform_signer", return_value=platform_signer),
        patch("services.marketplace.main.PSET.from_string", side_effect=[initial_pset, final_pset]),
        patch("services.marketplace.main._liquid_rpc_client", MagicMock(walletprocesspsbt=AsyncMock(return_value={"psbt": "refund-final-pset"}), sendrawtransaction=AsyncMock(return_value="ef" * 32))),
        patch("embit.liquid.finalizer.finalize_psbt", return_value=finalized_tx),
        patch(
            "services.marketplace.main.resolve_dispute",
            AsyncMock(return_value=(resolved_dispute, resolved_trade, refunded_escrow)),
        ) as resolve_dispute_mock,
        patch("services.marketplace.main.record_audit_event", AsyncMock()),
    ):
        response = app_client.post(
            f"/trades/{trade_id}/dispute/resolve",
            headers=_auth_headers(access_token),
            json={"resolution": "refund"},
        )

    assert response.status_code == 200
    body = response.json()["dispute"]
    assert body["status"] == "resolved"
    assert body["resolution"] == "refund"
    resolve_kwargs = resolve_dispute_mock.await_args.kwargs
    assert resolve_kwargs["trade_id"] == trade_id
    assert resolve_kwargs["resolved_by"] == str(admin_user.id)
    assert resolve_kwargs["resolution"] == "refund"
    assert resolve_kwargs["resolution_txid"] == "ef" * 32
    assert "platform" in resolve_kwargs["collected_signatures"]["refund"]
    assert "buyer" in resolve_kwargs["collected_signatures"]["refund"]


def test_resolve_dispute_rejects_non_admin(client):
    app_client, _, settings = client
    regular_user = _make_fake_user(role="user")
    access_token = _issue_access_token(regular_user, settings.jwt_secret)
    trade_id = uuid.uuid4()

    with patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=regular_user)):
        response = app_client.post(
            f"/trades/{trade_id}/dispute/resolve",
            headers=_auth_headers(access_token),
            json={"resolution": "release"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_resolve_dispute_rejects_non_disputed_trade(client):
    app_client, _, settings = client
    admin_user = _make_fake_user(role="admin")
    access_token = _issue_access_token(admin_user, settings.jwt_secret)
    token_id = uuid.uuid4()
    trade_id = uuid.uuid4()
    escrowed_trade = {
        **_make_trade(
            token_id=token_id,
            buy_order_id=uuid.uuid4(),
            sell_order_id=uuid.uuid4(),
            quantity=5,
            price_sat=100_000,
            status="escrowed",
            settled_at=None,
        ),
        "id": trade_id,
        "status": "escrowed",
    }

    with (
        patch("services.marketplace.main.get_user_by_id", AsyncMock(return_value=admin_user)),
        patch("services.marketplace.main.get_trade_by_id", AsyncMock(return_value=escrowed_trade)),
    ):
        response = app_client.post(
            f"/trades/{trade_id}/dispute/resolve",
            headers=_auth_headers(access_token),
            json={"resolution": "release"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "trade_state_conflict"


# ---------------------------------------------------------------------------
# Dispute DB unit tests
# ---------------------------------------------------------------------------


def test_open_dispute_db_updates_trade_and_escrow_status(marketplace_settings):
    with patch.dict(os.environ, marketplace_settings, clear=False):
        for module_name in ("services.marketplace.db", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.marketplace.db as marketplace_db

    trade_id = uuid.uuid4()
    token_id = uuid.uuid4()
    opener_id = uuid.uuid4()
    trade_row = {
        **_make_trade(
            token_id=token_id,
            buy_order_id=uuid.uuid4(),
            sell_order_id=uuid.uuid4(),
            quantity=5,
            price_sat=100_000,
            status="disputed",
            settled_at=None,
        ),
        "id": trade_id,
    }
    escrow_row = _make_escrow(trade_id=trade_id, locked_amount_sat=500_000, status="disputed")
    dispute_row = _make_dispute(trade_id=trade_id, opened_by=opener_id)

    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(
        side_effect=[
            _FetchOneResult(trade_row),   # UPDATE trades SET status='disputed'
            _FetchOneResult(escrow_row),  # UPDATE escrows SET status='disputed'
            _FetchOneResult(dispute_row), # INSERT INTO disputes
        ]
    )
    fake_conn.commit = AsyncMock()
    fake_conn.rollback = AsyncMock()

    result = asyncio.run(
        marketplace_db.open_dispute(
            fake_conn,
            trade_id=trade_id,
            opened_by=opener_id,
            reason="Documentation incomplete",
        )
    )

    assert result == dispute_row
    assert fake_conn.execute.await_count == 3
    fake_conn.commit.assert_awaited_once()
    fake_conn.rollback.assert_not_awaited()


def test_expire_unfunded_escrow_db_restores_inventory_and_order_fill(marketplace_settings):
    with patch.dict(os.environ, marketplace_settings, clear=False):
        for module_name in ("services.marketplace.db", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.marketplace.db as marketplace_db

    token_id = uuid.uuid4()
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    trade_row = _make_trade(
        token_id=token_id,
        buy_order_id=uuid.uuid4(),
        sell_order_id=uuid.uuid4(),
        quantity=4,
        price_sat=100_000,
        status="pending",
        settled_at=None,
    )
    buy_order = _make_order(
        user_id=buyer_id,
        token_id=token_id,
        side="buy",
        quantity=4,
        price_sat=100_000,
        filled_quantity=4,
        status="filled",
    )
    sell_order = _make_order(
        user_id=seller_id,
        token_id=token_id,
        side="sell",
        quantity=4,
        price_sat=100_000,
        filled_quantity=4,
        status="filled",
    )
    escrow_row = _make_escrow(trade_id=trade_row["id"], locked_amount_sat=400_000, status="created")
    cancelled_trade = {**trade_row, "status": "cancelled"}
    expired_escrow = {**escrow_row, "status": "expired"}
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(side_effect=[_FetchOneResult(expired_escrow), _FetchOneResult(cancelled_trade)])
    fake_conn.commit = AsyncMock()
    fake_conn.rollback = AsyncMock()

    with (
        patch.object(marketplace_db, "increment_token_balance", AsyncMock()) as increment_mock,
        patch.object(marketplace_db, "revert_order_fill", AsyncMock()) as revert_fill_mock,
    ):
        trade_result, escrow_result = asyncio.run(
            marketplace_db.expire_unfunded_escrow(
                fake_conn,
                trade_row=trade_row,
                escrow_row=escrow_row,
                buy_order=buy_order,
                sell_order=sell_order,
            )
        )

    assert trade_result == cancelled_trade
    assert escrow_result == expired_escrow
    increment_mock.assert_awaited_once_with(fake_conn, user_id=seller_id, token_id=token_id, quantity=4)
    assert revert_fill_mock.await_count == 2
    fake_conn.commit.assert_awaited_once()
    fake_conn.rollback.assert_not_awaited()


def test_resolve_dispute_db_release_transfers_tokens_and_records_txid(marketplace_settings):
    with patch.dict(os.environ, marketplace_settings, clear=False):
        for module_name in ("services.marketplace.db", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.marketplace.db as marketplace_db

    trade_id = uuid.uuid4()
    token_id = uuid.uuid4()
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    buy_order = _make_order(user_id=buyer_id, token_id=token_id, side="buy", quantity=5, price_sat=100_000)
    sell_order = _make_order(user_id=seller_id, token_id=token_id, side="sell", quantity=5, price_sat=100_000)
    trade_row = {
        **_make_trade(
            token_id=token_id,
            buy_order_id=buy_order["id"],
            sell_order_id=sell_order["id"],
            quantity=5,
            price_sat=100_000,
            fee_sat=10_000,
            status="disputed",
            settled_at=None,
        ),
        "id": trade_id,
        "status": "disputed",
    }
    escrow_row = _make_escrow(trade_id=trade_id, locked_amount_sat=500_000, status="disputed")
    settled_trade = {**trade_row, "status": "settled"}
    released_escrow = {**escrow_row, "status": "released", "release_txid": "cd" * 32}
    resolved_dispute = _make_dispute(
        trade_id=trade_id,
        opened_by=buyer_id,
        status="resolved",
        resolution="release",
    )
    admin_id = uuid.uuid4()

    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(
        side_effect=[
            _FetchOneResult(trade_row),       # SELECT trade
            _FetchOneResult(escrow_row),      # SELECT escrow
            _FetchOneResult(buy_order),       # SELECT buy order
            _FetchOneResult(sell_order),      # SELECT sell order
            _FetchOneResult(released_escrow), # UPDATE escrow status
            _FetchOneResult(settled_trade),   # UPDATE trade status
            _FetchOneResult(resolved_dispute),# UPDATE dispute status
        ]
    )
    fake_conn.commit = AsyncMock()
    fake_conn.rollback = AsyncMock()

    with (
        patch.object(marketplace_db, "increment_token_balance", AsyncMock()) as increment_mock,
        patch.object(marketplace_db, "record_trade_fee_income", AsyncMock()) as record_fee_mock,
    ):
        dispute_result, trade_result, escrow_result = asyncio.run(
            marketplace_db.resolve_dispute(
                fake_conn,
                trade_id=trade_id,
                resolved_by=admin_id,
                resolution="release",
                resolution_txid="cd" * 32,
                collected_signatures={"release": {"seller": {"signature": "seller-sig"}, "platform": {"signature": "platform-sig"}}},
                settlement_metadata={"resolution": "release"},
            )
        )

    assert dispute_result == resolved_dispute
    assert trade_result == settled_trade
    assert escrow_result == released_escrow
    increment_mock.assert_awaited_once_with(fake_conn, user_id=buyer_id, token_id=token_id, quantity=5)
    record_fee_mock.assert_awaited_once_with(fake_conn, trade_row=settled_trade)
    fake_conn.commit.assert_awaited_once()
    fake_conn.rollback.assert_not_awaited()


def test_resolve_dispute_db_refund_returns_tokens_to_seller(marketplace_settings):
    with patch.dict(os.environ, marketplace_settings, clear=False):
        for module_name in ("services.marketplace.db", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.marketplace.db as marketplace_db

    trade_id = uuid.uuid4()
    token_id = uuid.uuid4()
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    buy_order = _make_order(user_id=buyer_id, token_id=token_id, side="buy", quantity=8, price_sat=50_000)
    sell_order = _make_order(user_id=seller_id, token_id=token_id, side="sell", quantity=8, price_sat=50_000)
    trade_row = {
        **_make_trade(
            token_id=token_id,
            buy_order_id=buy_order["id"],
            sell_order_id=sell_order["id"],
            quantity=8,
            price_sat=50_000,
            status="disputed",
            settled_at=None,
        ),
        "id": trade_id,
        "status": "disputed",
    }
    escrow_row = _make_escrow(trade_id=trade_id, locked_amount_sat=400_000, status="disputed")
    cancelled_trade = {**trade_row, "status": "cancelled"}
    refunded_escrow = {**escrow_row, "status": "refunded", "refund_txid": "ef" * 32}
    resolved_dispute = _make_dispute(
        trade_id=trade_id,
        opened_by=buyer_id,
        status="resolved",
        resolution="refund",
    )
    admin_id = uuid.uuid4()

    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(
        side_effect=[
            _FetchOneResult(trade_row),       # SELECT trade
            _FetchOneResult(escrow_row),      # SELECT escrow
            _FetchOneResult(buy_order),       # SELECT buy order
            _FetchOneResult(sell_order),      # SELECT sell order
            _FetchOneResult(refunded_escrow), # UPDATE escrow status
            _FetchOneResult(cancelled_trade), # UPDATE trade status
            _FetchOneResult(resolved_dispute),# UPDATE dispute status
        ]
    )
    fake_conn.commit = AsyncMock()
    fake_conn.rollback = AsyncMock()

    with patch.object(marketplace_db, "increment_token_balance", AsyncMock()) as increment_mock:
        dispute_result, trade_result, escrow_result = asyncio.run(
            marketplace_db.resolve_dispute(
                fake_conn,
                trade_id=trade_id,
                resolved_by=admin_id,
                resolution="refund",
                resolution_txid="ef" * 32,
                collected_signatures={"refund": {"buyer": {"signature": "buyer-sig"}, "platform": {"signature": "platform-sig"}}},
                settlement_metadata={"resolution": "refund"},
            )
        )

    assert dispute_result == resolved_dispute
    assert trade_result == cancelled_trade
    assert escrow_result == refunded_escrow
    increment_mock.assert_awaited_once_with(fake_conn, user_id=seller_id, token_id=token_id, quantity=8)
    fake_conn.commit.assert_awaited_once()
    fake_conn.rollback.assert_not_awaited()
