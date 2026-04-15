from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


OrderSide = Literal["buy", "sell"]
OrderType = Literal["limit", "stop_limit"]
OrderStatus = Literal["open", "partially_filled", "filled", "cancelled"]
TradeStatus = Literal["pending", "escrowed", "settled", "disputed"]
EscrowStatus = Literal["created", "funded", "released", "refunded", "disputed"]
DisputeStatus = Literal["open", "resolved"]
DisputeResolution = Literal["refund", "release"]


class OrderCreateRequest(BaseModel):
    token_id: UUID
    side: OrderSide
    order_type: OrderType = "limit"
    quantity: int = Field(gt=0)
    price_sat: int = Field(gt=0)
    trigger_price_sat: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_trigger_payload(self) -> OrderCreateRequest:
        if self.order_type == "stop_limit" and self.trigger_price_sat is None:
            raise ValueError("trigger_price_sat is required for stop_limit orders")
        if self.order_type == "limit" and self.trigger_price_sat is not None:
            raise ValueError("trigger_price_sat is only allowed for stop_limit orders")
        return self


class OrderOut(BaseModel):
    id: UUID
    token_id: UUID
    side: OrderSide
    order_type: OrderType
    quantity: int
    price_sat: int
    trigger_price_sat: int | None = None
    triggered_at: datetime | None = None
    is_triggered: bool = True
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