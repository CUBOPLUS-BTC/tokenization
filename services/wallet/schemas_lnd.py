from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

class InvoiceStatus(str, Enum):
    SETTLED = "SETTLED"
    OPEN = "OPEN"
    CANCELED = "CANCELED"
    ACCEPTED = "ACCEPTED"

class InvoiceCreate(BaseModel):
    amount_sats: int = Field(..., gt=0, description="Amount in satoshis")
    memo: Optional[str] = Field(None, max_length=1024, description="Optional description for the invoice")

class Invoice(BaseModel):
    payment_request: str = Field(..., description="The bech32 encoded lightning invoice")
    payment_hash: str = Field(..., description="The hex encoded payment hash")
    r_hash: str = Field(..., description="Redundant hex encoded payment hash for LND compatibility")
    amount_sats: int = Field(..., description="Amount in satoshis")
    memo: Optional[str] = None
    status: InvoiceStatus
    settled_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PaymentCreate(BaseModel):
    payment_request: str = Field(..., description="The bech32 encoded lightning invoice to pay")

class PaymentStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    IN_FLIGHT = "IN_FLIGHT"

class Payment(BaseModel):
    payment_hash: str
    payment_preimage: Optional[str] = None
    status: PaymentStatus
    fee_sats: int = 0
    failure_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Bolt11DecodeRequest(BaseModel):
    payment_request: str

class Bolt11DecodeResponse(BaseModel):
    payment_hash: str
    amount_sat: int
    description: str
    description_hash: str | None = None
    timestamp: datetime
    expiry: int
    destination: str
    is_expired: bool
