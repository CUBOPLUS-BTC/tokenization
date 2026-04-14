import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime

import sys
from pathlib import Path

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set up all required environment variables for the wallet service."""
    monkeypatch.setenv("SERVICE_NAME", "wallet")
    monkeypatch.setenv("SERVICE_PORT", "8001")
    monkeypatch.setenv("WALLET_SERVICE_URL", "http://wallet:8001")
    monkeypatch.setenv("TOKENIZATION_SERVICE_URL", "http://tokenization:8002")
    monkeypatch.setenv("MARKETPLACE_SERVICE_URL", "http://marketplace:8003")
    monkeypatch.setenv("EDUCATION_SERVICE_URL", "http://education:8004")
    monkeypatch.setenv("NOSTR_SERVICE_URL", "http://nostr:8005")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "testdb")
    monkeypatch.setenv("POSTGRES_USER", "user")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BITCOIN_RPC_HOST", "localhost")
    monkeypatch.setenv("BITCOIN_RPC_PORT", "18443")
    monkeypatch.setenv("BITCOIN_RPC_USER", "bitcoin")
    monkeypatch.setenv("BITCOIN_NETWORK", "regtest")
    monkeypatch.setenv("LND_GRPC_HOST", "localhost")
    monkeypatch.setenv("LND_GRPC_PORT", "10009")
    monkeypatch.setenv("LND_MACAROON_PATH", "tests/fixtures/admin.macaroon")
    monkeypatch.setenv("LND_TLS_CERT_PATH", "tests/fixtures/tls.cert")
    monkeypatch.setenv("TAPD_GRPC_HOST", "localhost")
    monkeypatch.setenv("TAPD_GRPC_PORT", "10029")
    monkeypatch.setenv("TAPD_MACAROON_PATH", "tests/fixtures/admin.macaroon")
    monkeypatch.setenv("TAPD_TLS_CERT_PATH", "tests/fixtures/tls.cert")
    monkeypatch.setenv("NOSTR_RELAYS", "wss://relay.example.com")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    monkeypatch.setenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7")
    monkeypatch.setenv("TOTP_ISSUER", "Platform")
    monkeypatch.setenv("LOG_LEVEL", "INFO")

@pytest.fixture
def mock_user_id():
    return str(uuid4())

@pytest.fixture
def client(mock_env, mock_user_id):
    from services.wallet.main import app
    from services.wallet.auth import get_current_user_id
    
    # Simple override for authentication
    app.dependency_overrides[get_current_user_id] = lambda: mock_user_id
    
    with TestClient(app) as c:
        yield c
    
    # Clean up overrides
    app.dependency_overrides = {}

def test_get_wallet_summary_unauthorized(mock_env):
    """Should return 401/403 if no valid token is provided."""
    from services.wallet.main import app
    # Remove overrides for this test to test unauthorized access
    app.dependency_overrides = {}
    client = TestClient(app)
    response = client.get("/wallet")
    assert response.status_code in [401, 403]

@patch("services.wallet.main._engine")
@patch("services.wallet.main.get_wallet_by_user_id")
@patch("services.wallet.main.get_token_balances_for_user")
def test_get_wallet_summary_success(
    mock_get_tokens,
    mock_get_wallet,
    mock_engine,
    client,
    mock_user_id
):
    """Should return aggregated balance summary successfully."""
    wallet_id = uuid4()
    mock_get_wallet.return_value = {
        "id": wallet_id,
        "onchain_balance_sat": 500000,
        "lightning_balance_sat": 150000
    }
    
    token_id = uuid4()
    mock_get_tokens.return_value = [
        {
            "token_id": token_id,
            "asset_name": "Deep Ocean Blue",
            "balance": 100,
            "unit_price_sat": 2500
        }
    ]
    
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    response = client.get("/wallet", headers={"Authorization": "Bearer fake-token"})
    
    assert response.status_code == 200
    data = response.json()["wallet"]
    assert data["onchain_balance_sat"] == 500000
    assert data["lightning_balance_sat"] == 150000
    assert len(data["token_balances"]) == 1
    assert data["token_balances"][0]["asset_name"] == "Deep Ocean Blue"
    # Total valuation = 500,000 + 150,000 + (100 * 2,500) = 900,000
    assert data["total_value_sat"] == 900000

@patch("services.wallet.main._engine")
@patch("services.wallet.main.get_wallet_by_user_id")
def test_get_wallet_summary_not_found(
    mock_get_wallet,
    mock_engine,
    client,
    mock_user_id
):
    """Should return 404 if the user has no wallet record."""
    mock_get_wallet.return_value = None
    
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    response = client.get("/wallet", headers={"Authorization": "Bearer fake-token"})
    
    assert response.status_code == 404
    assert response.json()["detail"] == "Wallet not found for user"
