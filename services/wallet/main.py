from __future__ import annotations

import logging
from datetime import datetime
from pathlib import sys, Path

import grpc
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
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

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.addFilter(SensitiveDataFilter())

settings = get_settings(service_name="wallet", default_port=8001)
app = FastAPI(title="Wallet Service")

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
