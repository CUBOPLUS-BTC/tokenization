from __future__ import annotations

from datetime import datetime, timezone
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RouteHintHop(BaseModel):
    node_id: str
    chan_id: str
    fee_base_msat: int
    fee_proportional_millionths: int
    cltv_expiry_delta: int


class RouteHintOut(BaseModel):
    hops: list[RouteHintHop]

class Bolt11DecodeRequest(BaseModel):
    payment_request: str

class Bolt11DecodeResponse(BaseModel):
    payment_hash: str
    amount_sat: int | None = None
    amount_msat: int | None = None
    description: str | None = None
    description_hash: str | None = None
    timestamp: datetime
    created_at: datetime
    expiry: int
    expires_at: datetime
    destination: str | None = None
    fallback_address: str | None = None
    network: str
    route_hints: list[RouteHintOut] = Field(default_factory=list)
    is_expired: bool
