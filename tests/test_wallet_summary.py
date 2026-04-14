import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime

import sys
from pathlib import Path

# Add services directory to path
sys.path.append(str(Path(__file__).resolve().parents[1] / "services" / "wallet"))

from main import app
from auth import get_current_user_id

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def mock_user_id():
    return str(uuid4())

def test_get_wallet_summary_unauthorized(client):
    """Should return 401 if no valid token is provided."""
    # Note: Header missing, Depends(get_current_user_id) will raise 401 (HTTPBearer auto-error default)
    response = client.get("/wallet")
    assert response.status_code == 403 # HTTPBearer returns 403 Not Authenticated if auto_error=True

@patch("main.get_current_user_id")
@patch("main._engine")
@patch("main.get_wallet_by_user_id")
@patch("main.get_token_balances_for_user")
def test_get_wallet_summary_success(
    mock_get_tokens,
    mock_get_wallet,
    mock_engine,
    mock_auth,
    client,
    mock_user_id
):
    """Should return aggregated balance summary successfully."""
    mock_auth.return_value = mock_user_id
    
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
    
    # Mock engine connection
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    response = client.get("/wallet", headers={"Authorization": "Bearer fake-token"})
    
    assert response.status_code == 200
    data = response.json()["wallet"]
    assert data["onchain_balance_sat"] == 500000
    assert data["lightning_balance_sat"] == 150000
    assert len(data["token_balances"]) == 1
    assert data["token_balances"][0]["asset_name"] == "Deep Ocean Blue"
    # Total valuation = 500,000 + 150,000 + (100 * 2,500) = 650,000 + 250,000 = 900,000
    assert data["total_value_sat"] == 900000

@patch("main.get_current_user_id")
@patch("main._engine")
@patch("main.get_wallet_by_user_id")
def test_get_wallet_summary_not_found(
    mock_get_wallet,
    mock_engine,
    mock_auth,
    client,
    mock_user_id
):
    """Should return 404 if the user has no wallet record."""
    mock_auth.return_value = mock_user_id
    mock_get_wallet.return_value = None
    
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    response = client.get("/wallet", headers={"Authorization": "Bearer fake-token"})
    
    assert response.status_code == 404
    assert response.json()["detail"] == "Wallet not found for user"
