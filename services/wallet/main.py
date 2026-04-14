from __future__ import annotations

import logging
from datetime import datetime
from pathlib import sys, Path

import grpc
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, AsyncConnection
import uvicorn

# Add parent directory to path to allow imports from common
sys.path.append(str(Path(__file__).resolve().parents[1]))

from common import get_readiness_payload, get_settings
from lnd_client import LNDClient
from log_filter import SensitiveDataFilter
from schemas_lnd import (
    Invoice, InvoiceCreate, InvoiceStatus,
    Payment, PaymentCreate, PaymentStatus
)
from auth import get_current_user_id
from schemas_wallet import WalletResponse, TokenBalance, WalletSummary
from db import get_wallet_by_user_id, get_token_balances_for_user

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.addFilter(SensitiveDataFilter())

settings = get_settings(service_name="wallet", default_port=8001)

def _make_async_url(sync_url: str) -> str:
    """Convert standard postgres:// URL to asyncpg driver URL."""
    url = sync_url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url

_engine: AsyncEngine | None = None

@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _engine
    async_url = _make_async_url(settings.database_url)
    _engine = create_async_engine(async_url, pool_pre_ping=True)
    yield
    await _engine.dispose()

app = FastAPI(title="Wallet Service", lifespan=_lifespan)

# Initialize LND client
lnd_client = LNDClient(settings)

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.service_name,
        "env_profile": settings.env_profile,
    }

@app.get("/ready")
async def ready():
    payload = get_readiness_payload(settings)
    status_code = 200 if payload["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=payload)

# --- Wallet Summary ---

@app.get("/wallet", response_model=WalletResponse, tags=["Wallet"])
async def get_wallet_summary(
    user_id: str = Depends(get_current_user_id)
):
    """
    Returns a unified summary of on-chain, Lightning, and token balances.
    Aggregates data from both the wallet and token domains.
    """
    async with _engine.connect() as conn:  # type: AsyncConnection
        wallet_row = await get_wallet_by_user_id(conn, user_id)
        if not wallet_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Wallet not found for user"
            )
        
        token_rows = await get_token_balances_for_user(conn, user_id)
    
    token_balances = [
        TokenBalance(
            token_id=row["token_id"],
            asset_name=row["asset_name"],
            symbol=None, # See implementation plan note on missing symbol column
            balance=row["balance"],
            unit_price_sat=row["unit_price_sat"]
        )
        for row in token_rows
    ]

    onchain = wallet_row["onchain_balance_sat"]
    lightning = wallet_row["lightning_balance_sat"]
    
    # Compute total value: sum of BTC balances + sum of token valuations
    tokens_valuation = sum(t.balance * t.unit_price_sat for t in token_balances)
    total_value = onchain + lightning + tokens_valuation

    return WalletResponse(
        wallet=WalletSummary(
            id=wallet_row["id"],
            onchain_balance_sat=onchain,
            lightning_balance_sat=lightning,
            token_balances=token_balances,
            total_value_sat=total_value
        )
    )

# --- Lightning Endpoints ---

@app.post("/lightning/invoices", response_model=Invoice, tags=["Lightning"])
async def create_invoice(req: InvoiceCreate):
    try:
        resp = lnd_client.create_invoice(memo=req.memo or "", amount_sats=req.amount_sats)
        return Invoice(
            payment_request=resp.payment_request,
            payment_hash=resp.r_hash.hex(),
            r_hash=resp.r_hash.hex(),
            amount_sats=req.amount_sats,
            memo=req.memo,
            status=InvoiceStatus.OPEN,
            created_at=datetime.utcnow()
        )
    except grpc.RpcError as e:
        logger.error(f"gRPC error creating invoice: {e}")
        raise HTTPException(status_code=503, detail="Lightning service unavailable")
    except Exception as e:
        logger.error(f"Unexpected error creating invoice: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/lightning/payments", response_model=Payment, tags=["Lightning"])
async def pay_invoice(req: PaymentCreate):
    try:
        resp = lnd_client.pay_invoice(payment_request=req.payment_request)
        
        status = PaymentStatus.SUCCEEDED
        failure_reason = None
        if resp.payment_error:
            status = PaymentStatus.FAILED
            failure_reason = resp.payment_error

        return Payment(
            payment_hash=resp.payment_hash.hex(),
            payment_preimage=resp.payment_preimage.hex() if not resp.payment_error else None,
            status=status,
            fee_sats=resp.payment_route.total_fees if resp.payment_route else 0,
            failure_reason=failure_reason
        )
    except grpc.RpcError as e:
        logger.error(f"gRPC error paying invoice: {e}")
        raise HTTPException(status_code=503, detail="Lightning service unavailable")
    except Exception as e:
        logger.error(f"Unexpected error paying invoice: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/lightning/invoices/{r_hash}", response_model=Invoice, tags=["Lightning"])
async def get_invoice(r_hash: str):
    try:
        ln_invoice = lnd_client.lookup_invoice(r_hash_str=r_hash)
        
        status_map = {
            0: InvoiceStatus.OPEN,
            1: InvoiceStatus.SETTLED,
            2: InvoiceStatus.CANCELED,
            3: InvoiceStatus.ACCEPTED
        }
        
        return Invoice(
            payment_request=ln_invoice.payment_request,
            payment_hash=ln_invoice.r_hash.hex(),
            r_hash=ln_invoice.r_hash.hex(),
            amount_sats=ln_invoice.value,
            memo=ln_invoice.memo,
            status=status_map.get(ln_invoice.state, InvoiceStatus.OPEN),
            settled_at=datetime.fromtimestamp(ln_invoice.settle_date) if ln_invoice.settle_date else None,
            created_at=datetime.fromtimestamp(ln_invoice.creation_date)
        )
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Invoice not found")
        logger.error(f"gRPC error looking up invoice: {e}")
        raise HTTPException(status_code=503, detail="Lightning service unavailable")
    except Exception as e:
        logger.error(f"Unexpected error looking up invoice: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
