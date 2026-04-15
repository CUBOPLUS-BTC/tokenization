from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


OrderSide = Literal["buy", "sell"]
OrderStatus = Literal["open", "partially_filled", "filled", "cancelled"]
TradeStatus = Literal["pending", "escrowed", "settled", "disputed"]
EscrowStatus = Literal["created", "funded", "released", "refunded", "disputed"]
DisputeStatus = Literal["open", "resolved"]
DisputeResolution = Literal["refund", "release"]


class OrderCreateRequest(BaseModel):
    token_id: UUID
    side: OrderSide
    quantity: int = Field(gt=0)
    price_sat: int = Field(gt=0)


class OrderOut(BaseModel):
    id: UUID
    token_id: UUID
    side: OrderSide
    quantity: int
    price_sat: int
    filled_quantity: int
    status: OrderStatus
    created_at: datetime


class OrderResponse(BaseModel):
    order: OrderOut


class OrderListResponse(BaseModel):
    orders: list[OrderOut]
    next_cursor: UUID | None = None


class CancelledOrderOut(BaseModel):
    id: UUID
    status: Literal["cancelled"]


class CancelOrderResponse(BaseModel):
    order: CancelledOrderOut


class OrderBookLevel(BaseModel):
    price_sat: int
    total_quantity: int


class OrderBookResponse(BaseModel):
    token_id: UUID
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    last_trade_price_sat: int | None = None
    volume_24h: int


class TradeOut(BaseModel):
    id: UUID
    token_id: UUID
    quantity: int
    price_sat: int
    total_sat: int
    fee_sat: int
    status: TradeStatus
    created_at: datetime
    settled_at: datetime | None = None


class TradeListResponse(BaseModel):
    trades: list[TradeOut]
    next_cursor: UUID | None = None


class EscrowOut(BaseModel):
    id: UUID
    trade_id: UUID
    multisig_address: str
    locked_amount_sat: int
    funding_txid: str | None = None
    release_txid: str | None = None
    status: EscrowStatus
    expires_at: datetime


class EscrowResponse(BaseModel):
    escrow: EscrowOut


class EscrowSignRequest(BaseModel):
    partial_signature: str = Field(min_length=1)


class DisputeOpenRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class DisputeResolveRequest(BaseModel):
    resolution: DisputeResolution


class DisputeOut(BaseModel):
    id: UUID
    trade_id: UUID
    opened_by: UUID
    reason: str
    status: DisputeStatus
    resolution: DisputeResolution | None = None
    resolved_by: UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class DisputeResponse(BaseModel):
    dispute: DisputeOut