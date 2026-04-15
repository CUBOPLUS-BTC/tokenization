from __future__ import annotations

import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.common.config import Settings
from services.common.metrics import MetricsCollector, mount_metrics_endpoint

metrics_module = importlib.import_module("services.common.metrics")


def _test_settings() -> Settings:
    return Settings(
        env_profile="beta",
        service_name="observability-test",
        service_port=9999,
        wallet_service_url="http://wallet:8001",
        tokenization_service_url="http://tokenization:8002",
        marketplace_service_url="http://marketplace:8003",
        education_service_url="http://education:8004",
        nostr_service_url="http://nostr:8005",
        postgres_host="postgres",
        postgres_port=5432,
        postgres_db="tokenization_beta",
        postgres_user="tokenization",
        postgres_password="secret",
        database_url="postgresql://tokenization:secret@postgres:5432/tokenization_beta",
        redis_url="redis://redis:6379/0",
        bitcoin_rpc_host="signet-bitcoind",
        bitcoin_rpc_port=38332,
        bitcoin_rpc_user="beta_rpc",
        bitcoin_rpc_password="secret",
        bitcoin_network="signet",
        lnd_grpc_host="signet-lnd",
        lnd_grpc_port=10009,
        lnd_macaroon_path="/tmp/lnd.macaroon",
        lnd_tls_cert_path="/tmp/lnd.cert",
        tapd_grpc_host="signet-tapd",
        tapd_grpc_port=10029,
        tapd_macaroon_path="/tmp/tapd.macaroon",
        tapd_tls_cert_path="/tmp/tapd.cert",
        nostr_relays="wss://relay.example.com",
        jwt_secret="secret",
        jwt_access_token_expire_minutes=15,
        jwt_refresh_token_expire_days=7,
        totp_issuer="RWAPlatform-Beta",
        wallet_encryption_key="00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
        log_level="INFO",
    )


def test_metrics_endpoint_exposes_prometheus_text(monkeypatch):
    settings = _test_settings()
    collector = MetricsCollector()
    monkeypatch.setattr(metrics_module, "metrics", collector)
    monkeypatch.setattr(
        metrics_module,
        "get_readiness_payload",
        lambda _: {
            "status": "ready",
            "dependencies": {
                "postgres": {"ok": True, "target": "postgres:5432", "error": None},
                "redis": {"ok": True, "target": "redis:6379", "error": None},
                "bitcoin": {"ok": True, "target": "signet-bitcoind:38332", "error": None},
                "lnd": {"ok": True, "target": "signet-lnd:10009", "error": None},
                "tapd": {"ok": True, "target": "signet-tapd:10029", "error": None},
            },
        },
    )

    app = FastAPI()
    mount_metrics_endpoint(app, settings)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/ping").status_code == 200

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE http_request_duration_seconds histogram" in response.text
    assert 'http_requests_total{bitcoin_network="signet",env_profile="beta",method="GET",path="/ping",service="observability-test",status_code="200"}' in response.text
    assert 'service_ready{bitcoin_network="signet",env_profile="beta",service="observability-test"} 1.000000' in response.text


def test_metrics_endpoint_supports_json(monkeypatch):
    settings = _test_settings()
    collector = MetricsCollector()
    monkeypatch.setattr(metrics_module, "metrics", collector)
    monkeypatch.setattr(
        metrics_module,
        "get_readiness_payload",
        lambda _: {
            "status": "ready",
            "dependencies": {
                "postgres": {"ok": True, "target": "postgres:5432", "error": None},
                "redis": {"ok": True, "target": "redis:6379", "error": None},
                "bitcoin": {"ok": True, "target": "signet-bitcoind:38332", "error": None},
                "lnd": {"ok": True, "target": "signet-lnd:10009", "error": None},
                "tapd": {"ok": True, "target": "signet-tapd:10029", "error": None},
            },
        },
    )

    app = FastAPI()
    mount_metrics_endpoint(app, settings)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    client = TestClient(app)
    client.get("/ping")
    response = client.get("/metrics?format=json")
    payload = response.json()

    assert payload["service"] == "observability-test"
    assert payload["env_profile"] == "beta"
    assert payload["bitcoin_network"] == "signet"
    assert payload["readiness"]["status"] == "ready"
