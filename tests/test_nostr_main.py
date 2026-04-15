from __future__ import annotations

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


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
        "JWT_SECRET": "test-secret-key",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
    }


def _install_fake_redis_module(fake_client: object) -> None:
    redis_asyncio_module = ModuleType("redis.asyncio")

    class FakeRedis:
        @staticmethod
        def from_url(*args, **kwargs):
            return fake_client

    redis_asyncio_module.Redis = FakeRedis
    redis_module = ModuleType("redis")
    redis_module.asyncio = redis_asyncio_module
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio_module


def test_pump_events_to_relays_publishes_mapped_events(fake_settings):
    stop_event = asyncio.Event()
    entries = [
        [
            (
                "asset.created",
                [
                    (
                        "1-0",
                        {
                            "payload": json.dumps(
                                {
                                    "event": "asset_created",
                                    "asset_id": "asset-1",
                                    "created_at": "2026-04-15T10:00:00Z",
                                }
                            )
                        },
                    )
                ],
            )
        ],
        [],
    ]

    class FakeRedisClient:
        def __init__(self) -> None:
            self._calls = 0
            self.aclose = AsyncMock(return_value=None)

        async def xread(self, *args, **kwargs):
            value = entries[self._calls] if self._calls < len(entries) else []
            self._calls += 1
            if self._calls >= 2:
                stop_event.set()
            return value

    fake_client = FakeRedisClient()
    connector = SimpleNamespace(publish=AsyncMock(return_value=None))

    with pytest.MonkeyPatch.context() as mp:
        for key, value in fake_settings.items():
            mp.setenv(key, value)
        for module_name in ("services.nostr.main", "common", "common.config"):
            sys.modules.pop(module_name, None)
        _install_fake_redis_module(fake_client)
        import services.nostr.main as nostr_main

        asyncio.run(nostr_main._pump_events_to_relays(stop_event, connector))

    connector.publish.assert_awaited_once()


def test_pump_events_to_relays_handles_publish_failures_without_raising(fake_settings):
    stop_event = asyncio.Event()
    entries = [
        [
            (
                "trade.matched",
                [
                    (
                        "2-0",
                        {
                            "payload": json.dumps(
                                {
                                    "event": "trade_matched",
                                    "trade_id": "trade-1",
                                }
                            )
                        },
                    )
                ],
            )
        ],
        [],
    ]

    class FakeRedisClient:
        def __init__(self) -> None:
            self._calls = 0
            self.aclose = AsyncMock(return_value=None)

        async def xread(self, *args, **kwargs):
            value = entries[self._calls] if self._calls < len(entries) else []
            self._calls += 1
            if self._calls >= 2:
                stop_event.set()
            return value

    fake_client = FakeRedisClient()
    connector = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("relay unavailable")))

    with pytest.MonkeyPatch.context() as mp:
        for key, value in fake_settings.items():
            mp.setenv(key, value)
        for module_name in ("services.nostr.main", "common", "common.config"):
            sys.modules.pop(module_name, None)
        _install_fake_redis_module(fake_client)
        import services.nostr.main as nostr_main

        asyncio.run(nostr_main._pump_events_to_relays(stop_event, connector))

    connector.publish.assert_awaited_once()
