from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest.mock import ANY, AsyncMock, MagicMock, patch

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


class FakeAsset(NamedTuple):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str
    category: str
    valuation_sat: int
    documents_url: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    ai_score: float | None = None
    ai_analysis: dict[str, Any] | None = None
    projected_roi: float | None = None
    token_id: uuid.UUID | None = None
    taproot_asset_id: str | None = None
    total_supply: int | None = None
    circulating_supply: int | None = None
    unit_price_sat: int | None = None
    minted_at: datetime | None = None
    token_metadata: dict[str, Any] | None = None


def _make_fake_user(*, role: str = "seller") -> FakeUser:
    return FakeUser(
        id=uuid.uuid4(),
        email="seller@example.com",
        display_name="Seller",
        role=role,
        created_at=datetime.now(tz=timezone.utc),
        deleted_at=None,
    )


def _make_fake_asset(
    owner_id: uuid.UUID,
    *,
    status: str = "pending",
    category: str = "real_estate",
    ai_score: float | None = None,
    ai_analysis: dict[str, Any] | None = None,
    projected_roi: float | None = None,
    tokenized: bool = False,
    token_metadata: dict[str, Any] | None = None,
) -> FakeAsset:
    now = datetime.now(tz=timezone.utc)
    return FakeAsset(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Downtown Office Building",
        description="3-story commercial office building in the central district.",
        category=category,
        valuation_sat=100_000_000,
        documents_url="https://storage.example.com/docs/abc123",
        status=status,
        created_at=now,
        updated_at=now,
        ai_score=ai_score,
        ai_analysis=ai_analysis,
        projected_roi=projected_roi,
        token_id=uuid.uuid4() if tokenized else None,
        taproot_asset_id="ab" * 32 if tokenized else None,
        total_supply=1_000 if tokenized else None,
        circulating_supply=350 if tokenized else None,
        unit_price_sat=100_000 if tokenized else None,
        minted_at=now if tokenized else None,
        token_metadata=token_metadata,
    )


def _make_taproot_asset(*, asset_id: str, amount: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        amount=amount,
        script_key=bytes.fromhex("11" * 32),
        asset_genesis=SimpleNamespace(
            genesis_point="f" * 64 + ":0",
            name=name,
            meta_hash=bytes.fromhex("22" * 32),
            asset_id=bytes.fromhex(asset_id),
            asset_type=0,
            output_index=0,
        ),
        asset_group=SimpleNamespace(
            tweaked_group_key=bytes.fromhex("33" * 32),
        ),
        chain_anchor=SimpleNamespace(
            anchor_outpoint="e" * 64 + ":1",
            anchor_block_hash="d" * 64,
            block_height=144,
        ),
        decimal_display=SimpleNamespace(decimal_display=0),
    )


def _make_taproot_meta() -> SimpleNamespace:
    return SimpleNamespace(
        data=b'{"issuer":"tapd"}',
        type=1,
        meta_hash=bytes.fromhex("22" * 32),
        decimal_display=0,
        universe_commitments=True,
        canonical_universe_urls=["https://universe.example.com"],
        delegation_key=bytes.fromhex("44" * 32),
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
        "TAPD_MACAROON_PATH": "tests/fixtures/tapd.macaroon",
        "TAPD_TLS_CERT_PATH": "tests/fixtures/tapd.cert",
        "NOSTR_RELAYS": "wss://relay.example.com",
        "JWT_SECRET": "test-secret-key-for-tokenization-tests",
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
        for module_name in ("services.tokenization.main", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.tokenization.main as tokenization_main

        tokenization_main._engine = fake_engine
        app = tokenization_main.app
        app.router.lifespan_context = None

        yield TestClient(app, raise_server_exceptions=True), tokenization_main.settings


def _issue_access_token(user: FakeUser, secret: str) -> str:
    return issue_token_pair(
        user_id=str(user.id),
        role=user.role,
        wallet_id=None,
        secret=secret,
    ).access_token


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


class TestSubmitAsset:
    def test_asset_created_topic_is_mirrored_to_redis_stream(self, client):
        _, _ = client
        import services.tokenization.main as tokenization_main

        handlers = tokenization_main._event_bus._handlers.get("asset.created", [])
        assert handlers, "asset.created should be subscribed to at least one stream mirror handler"

    def test_seller_can_create_asset_with_pending_initial_status(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="seller")
        fake_asset = _make_fake_asset(fake_user.id)
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        create_asset_mock = AsyncMock(return_value=fake_asset)

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.create_asset", create_asset_mock),
        ):
            resp = app_client.post(
                "/assets",
                headers=_auth_headers(access_token),
                json={
                    "name": fake_asset.name,
                    "description": fake_asset.description,
                    "category": fake_asset.category,
                    "valuation_sat": fake_asset.valuation_sat,
                    "documents_url": fake_asset.documents_url,
                },
            )

        assert resp.status_code == 201
        body = resp.json()["asset"]
        assert body["owner_id"] == str(fake_user.id)
        assert body["name"] == fake_asset.name
        assert body["description"] == fake_asset.description
        assert body["category"] == fake_asset.category
        assert body["valuation_sat"] == fake_asset.valuation_sat
        assert body["documents_url"] == fake_asset.documents_url
        assert body["status"] == "pending"

        create_asset_mock.assert_awaited_once()
        assert create_asset_mock.await_args.args[0] is not None
        assert create_asset_mock.await_args.kwargs == {
            "owner_id": str(fake_user.id),
            "name": fake_asset.name,
            "description": fake_asset.description,
            "category": fake_asset.category,
            "valuation_sat": fake_asset.valuation_sat,
            "documents_url": fake_asset.documents_url,
        }

    def test_submit_asset_emits_asset_created_event(self, client):
        app_client, settings = client
        import services.tokenization.main as tokenization_main

        fake_user = _make_fake_user(role="seller")
        fake_asset = _make_fake_asset(fake_user.id, status="pending")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        publish_mock = AsyncMock(return_value=None)

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.create_asset", AsyncMock(return_value=fake_asset)),
            patch.object(tokenization_main._event_bus, "publish", publish_mock),
        ):
            response = app_client.post(
                "/assets",
                headers=_auth_headers(access_token),
                json={
                    "name": fake_asset.name,
                    "description": fake_asset.description,
                    "category": fake_asset.category,
                    "valuation_sat": fake_asset.valuation_sat,
                    "documents_url": fake_asset.documents_url,
                },
            )

        assert response.status_code == 201
        publish_mock.assert_awaited_once_with(
            "asset.created",
            {
                "event": "asset_created",
                "asset_id": str(fake_asset.id),
                "owner_id": str(fake_user.id),
                "name": fake_asset.name,
                "category": fake_asset.category,
                "valuation_sat": fake_asset.valuation_sat,
                "status": "pending",
                "created_at": fake_asset.created_at.isoformat().replace("+00:00", "Z"),
            },
        )

    def test_missing_documents_url_returns_clear_validation_error(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="seller")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)):
            resp = app_client.post(
                "/assets",
                headers=_auth_headers(access_token),
                json={
                    "name": "Downtown Office Building",
                    "description": "3-story commercial office building in the central district.",
                    "category": "real_estate",
                    "valuation_sat": 100_000_000,
                },
            )

        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["message"] == "Request payload failed validation."
        assert {"field": "documents_url", "message": "Field required"} in body["error"]["details"]

    def test_invalid_category_returns_field_level_validation_error(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="seller")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)):
            resp = app_client.post(
                "/assets",
                headers=_auth_headers(access_token),
                json={
                    "name": "Downtown Office Building",
                    "description": "3-story commercial office building in the central district.",
                    "category": "boats",
                    "valuation_sat": 100_000_000,
                    "documents_url": "https://storage.example.com/docs/abc123",
                },
            )

        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "validation_error"
        assert any(detail["field"] == "category" for detail in body["error"]["details"])

    def test_non_seller_role_is_rejected(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="user")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)):
            resp = app_client.post(
                "/assets",
                headers=_auth_headers(access_token),
                json={
                    "name": "Downtown Office Building",
                    "description": "3-story commercial office building in the central district.",
                    "category": "real_estate",
                    "valuation_sat": 100_000_000,
                    "documents_url": "https://storage.example.com/docs/abc123",
                },
            )

        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"

    def test_missing_bearer_token_is_rejected(self, client):
        app_client, _ = client

        resp = app_client.post(
            "/assets",
            json={
                "name": "Downtown Office Building",
                "description": "3-story commercial office building in the central district.",
                "category": "real_estate",
                "valuation_sat": 100_000_000,
                "documents_url": "https://storage.example.com/docs/abc123",
            },
        )

        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "authentication_required"


class TestGetAssetDetails:
    def test_user_can_fetch_asset_details_with_ai_and_token_fields(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="user")
        fake_asset = _make_fake_asset(
            fake_user.id,
            status="tokenized",
            ai_score=78.5,
            ai_analysis={
                "risk_level": "moderate",
                "projected_roi_annual": 7.2,
                "market_timing": "favorable",
                "summary": "Strong location with consistent occupancy rates.",
            },
            projected_roi=7.2,
            tokenized=True,
        )
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=fake_asset)),
        ):
            resp = app_client.get(
                f"/assets/{fake_asset.id}",
                headers=_auth_headers(access_token),
            )

        assert resp.status_code == 200
        body = resp.json()["asset"]
        assert body["id"] == str(fake_asset.id)
        assert body["owner_id"] == str(fake_user.id)
        assert body["status"] == "tokenized"
        assert body["ai_score"] == 78.5
        assert body["ai_analysis"] == fake_asset.ai_analysis
        assert body["projected_roi"] == 7.2
        assert body["token"] == {
            "id": str(fake_asset.token_id),
            "taproot_asset_id": fake_asset.taproot_asset_id,
            "total_supply": fake_asset.total_supply,
            "circulating_supply": fake_asset.circulating_supply,
            "unit_price_sat": fake_asset.unit_price_sat,
            "issuance_metadata": None,
            "minted_at": fake_asset.minted_at.isoformat().replace("+00:00", "Z"),
        }

    def test_missing_asset_returns_contract_404(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="user")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        missing_asset_id = uuid.uuid4()

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=None)),
        ):
            resp = app_client.get(
                f"/assets/{missing_asset_id}",
                headers=_auth_headers(access_token),
            )

        assert resp.status_code == 404
        assert resp.json()["error"] == {
            "code": "asset_not_found",
            "message": "Asset not found.",
        }


class TestRequestAssetEvaluation:
    def test_owner_can_request_asset_evaluation(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="seller")
        pending_asset = _make_fake_asset(fake_user.id, status="pending")
        queued_asset = pending_asset._replace(status="evaluating")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        begin_evaluation_mock = AsyncMock(return_value=queued_asset)
        dispatch_mock = MagicMock()

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=pending_asset)),
            patch("services.tokenization.main.begin_asset_evaluation", begin_evaluation_mock),
            patch("services.tokenization.main._dispatch_asset_evaluation", dispatch_mock),
        ):
            resp = app_client.post(
                f"/assets/{pending_asset.id}/evaluate",
                headers=_auth_headers(access_token),
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["message"] == "Evaluation started"
        assert "estimated_completion" in body

        begin_evaluation_mock.assert_awaited_once()
        assert begin_evaluation_mock.await_args.kwargs == {
            "asset_id": pending_asset.id,
            "owner_id": str(fake_user.id),
        }
        dispatch_mock.assert_called_once_with(
            pending_asset.id,
            fallback_status="pending",
        )

    def test_non_owner_cannot_request_asset_evaluation(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="seller")
        other_owner_asset = _make_fake_asset(uuid.uuid4(), status="pending")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=other_owner_asset)),
            patch("services.tokenization.main.begin_asset_evaluation", AsyncMock()),
        ):
            resp = app_client.post(
                f"/assets/{other_owner_asset.id}/evaluate",
                headers=_auth_headers(access_token),
            )

        assert resp.status_code == 403
        assert resp.json()["error"] == {
            "code": "forbidden",
            "message": "Only the owning seller can evaluate this asset.",
        }


class TestTokenizeAsset:
    def test_owner_can_tokenize_approved_asset_with_taproot_metadata(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="seller")
        approved_asset = _make_fake_asset(fake_user.id, status="approved")
        taproot_asset_id = "ab" * 32
        token_metadata = {
            "asset_id": taproot_asset_id,
            "asset_name": approved_asset.name,
            "meta_reveal": {
                "data": '{"issuer":"tapd"}',
            },
        }
        tokenized_asset = approved_asset._replace(
            status="tokenized",
            token_id=uuid.uuid4(),
            taproot_asset_id=taproot_asset_id,
            total_supply=1_000,
            circulating_supply=1_000,
            unit_price_sat=100_000,
            minted_at=datetime.now(tz=timezone.utc),
            token_metadata=token_metadata,
        )
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        tapd_asset = _make_taproot_asset(
            asset_id=taproot_asset_id,
            amount=1_000,
            name=approved_asset.name,
        )
        tapd_meta = _make_taproot_meta()
        create_asset_token_mock = AsyncMock(return_value=tokenized_asset)
        audit_mock = AsyncMock()

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=approved_asset)),
            patch("services.tokenization.main.create_asset_token", create_asset_token_mock),
            patch("services.tokenization.main.tapd_client.fetch_asset", return_value=tapd_asset),
            patch("services.tokenization.main.tapd_client.fetch_asset_meta", return_value=tapd_meta),
            patch("services.tokenization.main.record_audit_event", audit_mock),
        ):
            resp = app_client.post(
                f"/assets/{approved_asset.id}/tokenize",
                headers=_auth_headers(access_token),
                json={
                    "taproot_asset_id": taproot_asset_id,
                    "total_supply": 1_000,
                    "unit_price_sat": 100_000,
                },
            )

        assert resp.status_code == 201
        body = resp.json()["asset"]
        assert body["status"] == "tokenized"
        assert body["token"] == {
            "id": str(tokenized_asset.token_id),
            "taproot_asset_id": taproot_asset_id,
            "total_supply": 1_000,
            "circulating_supply": 1_000,
            "unit_price_sat": 100_000,
            "issuance_metadata": token_metadata,
            "minted_at": tokenized_asset.minted_at.isoformat().replace("+00:00", "Z"),
        }

        create_asset_token_mock.assert_awaited_once()
        assert create_asset_token_mock.await_args.kwargs["asset_id"] == approved_asset.id
        assert create_asset_token_mock.await_args.kwargs["owner_id"] == str(fake_user.id)
        assert create_asset_token_mock.await_args.kwargs["taproot_asset_id"] == taproot_asset_id
        assert create_asset_token_mock.await_args.kwargs["total_supply"] == 1_000
        assert create_asset_token_mock.await_args.kwargs["circulating_supply"] == 1_000
        assert create_asset_token_mock.await_args.kwargs["unit_price_sat"] == 100_000
        issuance_metadata = create_asset_token_mock.await_args.kwargs["issuance_metadata"]
        assert issuance_metadata["asset_id"] == taproot_asset_id
        assert issuance_metadata["asset_name"] == approved_asset.name
        assert issuance_metadata["group_key"] == "33" * 32
        assert issuance_metadata["anchor_outpoint"] == "e" * 64 + ":1"
        assert issuance_metadata["meta_reveal"]["data"] == '{"issuer":"tapd"}'
        audit_mock.assert_awaited_once_with(
            ANY,
            settings=settings,
            request=ANY,
            action="tokenization.asset.tokenize",
            actor_id=str(fake_user.id),
            actor_role="seller",
            target_type="token",
            target_id=tokenized_asset.token_id,
            metadata={
                "asset_id": str(approved_asset.id),
                "taproot_asset_id": taproot_asset_id,
                "total_supply": 1_000,
                "unit_price_sat": 100_000,
            },
        )

    def test_tokenization_emits_token_minted_event(self, client):
        app_client, settings = client
        import services.tokenization.main as tokenization_main

        fake_user = _make_fake_user(role="seller")
        approved_asset = _make_fake_asset(fake_user.id, status="approved")
        taproot_asset_id = "ab" * 32
        minted_at = datetime.now(tz=timezone.utc)
        tokenized_asset = approved_asset._replace(
            status="tokenized",
            token_id=uuid.uuid4(),
            taproot_asset_id=taproot_asset_id,
            total_supply=1_000,
            circulating_supply=1_000,
            unit_price_sat=100_000,
            minted_at=minted_at,
            token_metadata={"issuer": "tapd"},
        )
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        publish_mock = AsyncMock(return_value=None)

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=approved_asset)),
            patch("services.tokenization.main.create_asset_token", AsyncMock(return_value=tokenized_asset)),
            patch(
                "services.tokenization.main.tapd_client.fetch_asset",
                return_value=_make_taproot_asset(
                    asset_id=taproot_asset_id,
                    amount=1_000,
                    name=approved_asset.name,
                ),
            ),
            patch("services.tokenization.main.tapd_client.fetch_asset_meta", return_value=_make_taproot_meta()),
            patch.object(tokenization_main._event_bus, "publish", publish_mock),
        ):
            response = app_client.post(
                f"/assets/{approved_asset.id}/tokenize",
                headers=_auth_headers(access_token),
                json={
                    "taproot_asset_id": taproot_asset_id,
                    "total_supply": 1_000,
                    "unit_price_sat": 100_000,
                },
            )

        assert response.status_code == 201
        publish_mock.assert_awaited_once_with(
            "token.minted",
            {
                "event": "token_minted",
                "asset_id": str(approved_asset.id),
                "owner_id": str(fake_user.id),
                "token_id": str(tokenized_asset.token_id),
                "taproot_asset_id": taproot_asset_id,
                "total_supply": 1_000,
                "circulating_supply": 1_000,
                "unit_price_sat": 100_000,
                "minted_at": minted_at.isoformat().replace("+00:00", "Z"),
            },
        )

    def test_only_approved_assets_can_be_tokenized(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="seller")
        pending_asset = _make_fake_asset(fake_user.id, status="pending")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=pending_asset)),
            patch("services.tokenization.main.tapd_client.fetch_asset") as fetch_asset_mock,
        ):
            resp = app_client.post(
                f"/assets/{pending_asset.id}/tokenize",
                headers=_auth_headers(access_token),
                json={
                    "taproot_asset_id": "ab" * 32,
                    "total_supply": 1_000,
                    "unit_price_sat": 100_000,
                },
            )

        assert resp.status_code == 409
        assert resp.json()["error"] == {
            "code": "asset_state_conflict",
            "message": "Only approved assets can be tokenized.",
        }
        fetch_asset_mock.assert_not_called()

    def test_tokenization_rejects_taproot_supply_mismatch(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="seller")
        approved_asset = _make_fake_asset(fake_user.id, status="approved")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        taproot_asset_id = "ab" * 32
        tapd_asset = _make_taproot_asset(
            asset_id=taproot_asset_id,
            amount=900,
            name=approved_asset.name,
        )

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=approved_asset)),
            patch("services.tokenization.main.tapd_client.fetch_asset", return_value=tapd_asset),
            patch("services.tokenization.main.tapd_client.fetch_asset_meta", return_value=_make_taproot_meta()),
            patch("services.tokenization.main.create_asset_token", AsyncMock()) as create_asset_token_mock,
        ):
            resp = app_client.post(
                f"/assets/{approved_asset.id}/tokenize",
                headers=_auth_headers(access_token),
                json={
                    "taproot_asset_id": taproot_asset_id,
                    "total_supply": 1_000,
                    "unit_price_sat": 100_000,
                },
            )

        assert resp.status_code == 409
        assert resp.json()["error"] == {
            "code": "taproot_supply_mismatch",
            "message": "Taproot asset supply does not match the requested total supply.",
        }
        create_asset_token_mock.assert_not_called()


class TestAssetEvaluationProcessor:
    def test_background_evaluation_persists_results_and_emits_event(self, client):
        _, _ = client
        import services.tokenization.main as tokenization_main

        fake_asset = _make_fake_asset(_make_fake_user(role="seller").id, status="evaluating")
        completed_at = datetime(2026, 4, 14, 10, 30, tzinfo=timezone.utc)
        analysis = {
            "risk_level": "low",
            "projected_roi_annual": 11.2,
            "summary": "Strong underwriting profile with healthy upside.",
        }
        completed_asset = fake_asset._replace(
            status="approved",
            ai_score=88.5,
            ai_analysis=analysis,
            projected_roi=11.2,
            updated_at=completed_at,
        )

        evaluation_result = SimpleNamespace(
            ai_score=88.5,
            ai_analysis=analysis,
            projected_roi=11.2,
            status="approved",
        )

        complete_evaluation_mock = AsyncMock(return_value=completed_asset)
        publish_mock = AsyncMock(return_value=None)

        with (
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=fake_asset)),
            patch("services.tokenization.main.evaluate_asset_submission", MagicMock(return_value=evaluation_result)),
            patch("services.tokenization.main.complete_asset_evaluation", complete_evaluation_mock),
            patch.object(tokenization_main._event_bus, "publish", publish_mock),
        ):
            asyncio.run(
                tokenization_main._run_asset_evaluation(
                    fake_asset.id,
                    fallback_status="pending",
                )
            )

        complete_evaluation_mock.assert_awaited_once()
        assert complete_evaluation_mock.await_args.kwargs == {
            "asset_id": fake_asset.id,
            "ai_score": 88.5,
            "ai_analysis": analysis,
            "projected_roi": 11.2,
            "status": "approved",
        }
        publish_mock.assert_awaited_once_with(
            "ai.evaluation.complete",
            {
                "event": "ai_evaluation_complete",
                "asset_id": str(fake_asset.id),
                "owner_id": str(fake_asset.owner_id),
                "ai_score": 88.5,
                "projected_roi": 11.2,
                "status": "approved",
                "analysis": analysis,
                "completed_at": "2026-04-14T10:30:00Z",
            },
        )

    def test_background_evaluation_restores_previous_status_when_processing_fails(self, client):
        _, _ = client
        import services.tokenization.main as tokenization_main

        fake_asset = _make_fake_asset(_make_fake_user(role="seller").id, status="evaluating")
        reset_mock = AsyncMock(return_value=fake_asset._replace(status="pending"))
        publish_mock = AsyncMock(return_value=None)

        with (
            patch("services.tokenization.main.get_asset_by_id", AsyncMock(return_value=fake_asset)),
            patch(
                "services.tokenization.main.evaluate_asset_submission",
                MagicMock(side_effect=RuntimeError("processor unavailable")),
            ),
            patch("services.tokenization.main.reset_asset_evaluation", reset_mock),
            patch.object(tokenization_main._event_bus, "publish", publish_mock),
        ):
            asyncio.run(
                tokenization_main._run_asset_evaluation(
                    fake_asset.id,
                    fallback_status="pending",
                )
            )

        reset_mock.assert_awaited_once_with(
            ANY,
            asset_id=fake_asset.id,
            fallback_status="pending",
        )
        publish_mock.assert_not_awaited()


class TestListAssets:
    def test_user_can_list_assets_by_status_and_category(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="user")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        approved_art_asset = _make_fake_asset(
            fake_user.id,
            status="approved",
            category="art",
        )
        list_assets_mock = AsyncMock(return_value=[approved_art_asset])

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.list_assets", list_assets_mock),
        ):
            resp = app_client.get(
                "/assets?status=approved&category=art",
                headers=_auth_headers(access_token),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["next_cursor"] is None
        assert len(body["assets"]) == 1
        assert body["assets"][0]["id"] == str(approved_art_asset.id)
        assert body["assets"][0]["status"] == "approved"
        assert body["assets"][0]["category"] == "art"
        list_assets_mock.assert_awaited_once()
        assert list_assets_mock.await_args.kwargs == {
            "asset_status": "approved",
            "category": "art",
        }

    def test_asset_catalog_supports_cursor_pagination(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="user")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        newest = _make_fake_asset(fake_user.id, status="tokenized")
        middle = _make_fake_asset(fake_user.id, status="approved")
        oldest = _make_fake_asset(fake_user.id, status="pending")
        newest = newest._replace(created_at=datetime(2026, 4, 10, tzinfo=timezone.utc))
        middle = middle._replace(created_at=datetime(2026, 4, 9, tzinfo=timezone.utc))
        oldest = oldest._replace(created_at=datetime(2026, 4, 8, tzinfo=timezone.utc))
        rows = [oldest, newest, middle]

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.list_assets", AsyncMock(return_value=rows)),
        ):
            first_page = app_client.get(
                "/assets?limit=2",
                headers=_auth_headers(access_token),
            )
            second_page = app_client.get(
                f"/assets?limit=2&cursor={middle.id}",
                headers=_auth_headers(access_token),
            )

        assert first_page.status_code == 200
        first_body = first_page.json()
        assert [item["id"] for item in first_body["assets"]] == [
            str(newest.id),
            str(middle.id),
        ]
        assert first_body["next_cursor"] == str(middle.id)

        assert second_page.status_code == 200
        second_body = second_page.json()
        assert [item["id"] for item in second_body["assets"]] == [str(oldest.id)]
        assert second_body["next_cursor"] is None

    def test_asset_catalog_rejects_cursor_outside_filtered_result_set(self, client):
        app_client, settings = client
        fake_user = _make_fake_user(role="user")
        access_token = _issue_access_token(fake_user, settings.jwt_secret)
        rows = [_make_fake_asset(fake_user.id, status="approved")]

        with (
            patch("services.tokenization.main.get_user_by_id", AsyncMock(return_value=fake_user)),
            patch("services.tokenization.main.list_assets", AsyncMock(return_value=rows)),
        ):
            resp = app_client.get(
                f"/assets?status=approved&cursor={uuid.uuid4()}",
                headers=_auth_headers(access_token),
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == {
            "code": "invalid_cursor",
            "message": "Cursor does not match an asset in this result set.",
        }
