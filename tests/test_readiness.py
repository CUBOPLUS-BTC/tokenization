from __future__ import annotations

from services.common.config import Settings
from services.common.readiness import get_readiness_payload


def _settings(**overrides) -> Settings:
    base = {
        "env_profile": "local",
        "service_name": "readiness-test",
        "service_port": 9999,
        "wallet_service_url": "http://wallet:8001",
        "tokenization_service_url": "http://tokenization:8002",
        "marketplace_service_url": "http://marketplace:8003",
        "education_service_url": "http://education:8004",
        "nostr_service_url": "http://nostr:8005",
        "postgres_host": "postgres",
        "postgres_port": 5432,
        "postgres_db": "tokenization",
        "postgres_user": "tokenization",
        "postgres_password": "secret",
        "database_url": "postgresql://tokenization:secret@postgres:5432/tokenization",
        "redis_url": "redis://redis:6379/0",
        "bitcoin_rpc_host": "bitcoind",
        "bitcoin_rpc_port": 18443,
        "bitcoin_rpc_user": "local_rpc",
        "bitcoin_rpc_password": "local_rpc_password",
        "bitcoin_network": "regtest",
        "elements_rpc_host": "elementsd",
        "elements_rpc_port": 7041,
        "elements_rpc_user": "user",
        "elements_rpc_password": "pass",
        "elements_network": "elementsregtest",
        "lnd_grpc_host": "lnd",
        "lnd_grpc_port": 10009,
        "lnd_macaroon_path": "tests/fixtures/admin.macaroon",
        "lnd_tls_cert_path": "tests/fixtures/tls.cert",
        "nostr_relays": "wss://relay.example.com",
        "jwt_access_token_expire_minutes": 15,
        "jwt_refresh_token_expire_days": 7,
        "totp_issuer": "RWAPlatform",
        "log_level": "INFO",
    }
    base.update(overrides)
    return Settings(**base)


def test_readiness_allows_optional_local_lnd_and_elements(monkeypatch):
    settings = _settings()

    def fake_check(host: str, port: int, timeout_seconds: float = 1.5):
        if (host, port) in {("postgres", 5432), ("bitcoind", 18443)}:
            return True, None
        return False, "connection refused"

    monkeypatch.setattr("services.common.readiness._check_tcp_socket", fake_check)
    monkeypatch.setattr("services.common.readiness._check_redis_ping", lambda _: (True, None, "redis:6379"))

    payload = get_readiness_payload(settings)

    assert payload["status"] == "ready"
    assert payload["dependencies"]["bitcoin"]["required"] is True
    assert payload["dependencies"]["elements"]["required"] is False
    assert payload["dependencies"]["elements"]["ok"] is False
    assert payload["dependencies"]["elements"]["blocking"] is False
    assert payload["dependencies"]["lnd"]["required"] is False
    assert payload["dependencies"]["lnd"]["ok"] is False
    assert payload["dependencies"]["lnd"]["blocking"] is False


def test_readiness_blocks_beta_when_lnd_and_elements_are_down(monkeypatch):
    settings = _settings(env_profile="beta", jwt_secret="secret", wallet_encryption_key="0" * 64)

    def fake_check(host: str, port: int, timeout_seconds: float = 1.5):
        if (host, port) == ("postgres", 5432):
            return True, None
        if (host, port) == ("bitcoind", 18443):
            return True, None
        return False, "connection refused"

    monkeypatch.setattr("services.common.readiness._check_tcp_socket", fake_check)
    monkeypatch.setattr("services.common.readiness._check_redis_ping", lambda _: (True, None, "redis:6379"))

    payload = get_readiness_payload(settings)

    assert payload["status"] == "not_ready"
    assert payload["dependencies"]["elements"]["required"] is True
    assert payload["dependencies"]["elements"]["blocking"] is True
    assert payload["dependencies"]["lnd"]["required"] is True
    assert payload["dependencies"]["lnd"]["blocking"] is True
