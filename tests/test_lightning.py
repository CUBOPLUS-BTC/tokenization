import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime
import grpc

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
    from services.wallet.db import get_db_conn
    
    # Mock DB connection dependency
    async def _get_mock_db():
        yield AsyncMock()

    # Override dependencies
    app.dependency_overrides[get_current_user_id] = lambda: mock_user_id
    app.dependency_overrides[get_db_conn] = _get_mock_db
    
    with TestClient(app) as c:
        yield c
    
    # Clean up overrides
    app.dependency_overrides = {}

@patch("services.wallet.main.lnd_client")
@patch("services.wallet.main.get_wallet_by_user_id")
@patch("services.wallet.main.create_transaction")
@patch("services.wallet.main.get_db_conn")
def test_create_invoice_success(
    mock_get_db,
    mock_create_tx,
    mock_get_wallet,
    mock_lnd,
    client,
    mock_user_id
):
    """Should create an invoice and persist a pending transaction."""
    # Mock LND response
    mock_resp = MagicMock()
    mock_resp.payment_request = "lnbc1..."
    mock_resp.r_hash = b"\x01\x02\x03"
    mock_lnd.create_invoice.return_value = mock_resp
    
    # Mock DB
    wallet_id = uuid4()
    mock_get_wallet.return_value = {"id": wallet_id}
    mock_get_db.return_value = AsyncMock()
    mock_create_tx.return_value = AsyncMock()

    response = client.post(
        "/lightning/invoices",
        json={"amount_sats": 1000, "memo": "Test invoice"},
        headers={"Authorization": "Bearer fake-token"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["payment_request"] == "lnbc1..."
    assert data["payment_hash"] == "010203"
    
    # Verify persistence
    mock_create_tx.assert_called_once()
    args, kwargs = mock_create_tx.call_args
    assert kwargs["wallet_id"] == wallet_id
    assert kwargs["type"] == "ln_receive"
    assert kwargs["status"] == "pending"

@patch("services.wallet.main.lnd_client")
@patch("services.wallet.main.get_wallet_by_user_id")
@patch("services.wallet.main.create_transaction")
@patch("services.wallet.main.get_db_conn")
@patch("services.wallet.auth.get_user_2fa_secret")
def test_pay_invoice_success_no_2fa(
    mock_get_2fa,
    mock_get_db,
    mock_create_tx,
    mock_get_wallet,
    mock_lnd,
    client,
    mock_user_id
):
    """Should pay an invoice and persist a confirmed transaction (no 2FA required)."""
    # Mock 2FA (disabled for user)
    mock_get_2fa.return_value = None
    
    # Mock LND response
    mock_resp = MagicMock()
    mock_resp.payment_hash = b"\x04\x05\x06"
    mock_resp.payment_preimage = b"\x07\x08\x09"
    mock_resp.payment_error = ""
    mock_resp.payment_route.total_amt = 1050
    mock_resp.payment_route.total_fees = 50
    mock_lnd.pay_invoice.return_value = mock_resp
    
    # Mock DB
    wallet_id = uuid4()
    mock_get_wallet.return_value = {"id": wallet_id}
    mock_get_db.return_value = AsyncMock()

    response = client.post(
        "/lightning/payments",
        json={"payment_request": "lnbc1..."},
        headers={"Authorization": "Bearer fake-token"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCEEDED"
    assert data["payment_hash"] == "040506"
    
    # Verify persistence
    mock_create_tx.assert_called_once()
    args, kwargs = mock_create_tx.call_args
    assert kwargs["wallet_id"] == wallet_id
    assert kwargs["type"] == "ln_send"
    assert kwargs["status"] == "confirmed"
    assert kwargs["amount_sat"] == 1050

@patch("services.wallet.auth.get_user_2fa_secret")
@patch("services.wallet.main.get_wallet_by_user_id")
@patch("services.wallet.main.get_db_conn")
def test_pay_invoice_requires_2fa(
    mock_get_db,
    mock_get_wallet,
    mock_get_2fa,
    client,
    mock_user_id
):
    """Should return 403 if 2FA is enabled but code is missing."""
    # Mock 2FA (enabled for user)
    mock_get_2fa.return_value = "JBSWY3DPEHPK3PXP" # Base32 secret
    
    response = client.post(
        "/lightning/payments",
        json={"payment_request": "lnbc1..."},
        headers={"Authorization": "Bearer fake-token"}
    )
    
    assert response.status_code == 403
    assert "Two-factor authentication code is required" in response.json()["detail"]

@patch("services.wallet.auth.get_user_2fa_secret")
@patch("services.wallet.auth.pyotp.TOTP")
@patch("services.wallet.main.lnd_client")
@patch("services.wallet.main.get_wallet_by_user_id")
@patch("services.wallet.main.create_transaction")
@patch("services.wallet.main.get_db_conn")
def test_pay_invoice_with_valid_2fa(
    mock_get_db,
    mock_create_tx,
    mock_get_wallet,
    mock_lnd,
    mock_totp_class,
    mock_get_2fa,
    client,
    mock_user_id
):
    """Should succeed if valid 2FA code is provided."""
    # Mock 2FA (enabled for user)
    mock_get_2fa.return_value = AsyncMock(return_value="JBSWY3DPEHPK3PXP")() # Works with the way require_2fa is written
    # Actually require_2fa calls get_user_2fa_secret(conn, user_id)
    # Using patch directly on the function is better
    mock_get_2fa.return_value = "JBSWY3DPEHPK3PXP"
    
    mock_totp = MagicMock()
    mock_totp.verify.return_value = True
    mock_totp_class.return_value = mock_totp
    
    # Mock LND response
    mock_resp = MagicMock()
    mock_resp.payment_hash = b"\x01\x02"
    mock_resp.payment_preimage = b"\x03\x04"
    mock_resp.payment_error = ""
    mock_resp.payment_route.total_amt = 1000
    mock_resp.payment_route.total_fees = 10
    mock_lnd.pay_invoice.return_value = mock_resp
    
    # Mock DB
    mock_get_wallet.return_value = {"id": uuid4()}
    mock_get_db.return_value = AsyncMock()
    
    # Mock DB
    mock_get_wallet.return_value = {"id": uuid4()}

    response = client.post(
        "/lightning/payments",
        json={"payment_request": "lnbc1..."},
        headers={
            "Authorization": "Bearer fake-token",
            "x-2fa-code": "123456"
        }
    )
    
    assert response.status_code == 200
    mock_totp.verify.assert_called_once_with("123456", valid_window=1)

@patch("services.wallet.main.lnd_client")
def test_get_invoice_unauthorized(mock_lnd, mock_env):
    """Should return 401 if no valid token is provided."""
    from services.wallet.main import app
    app.dependency_overrides = {}
    client = TestClient(app)
    response = client.get("/lightning/invoices/010203")
    assert response.status_code in [401, 403]
