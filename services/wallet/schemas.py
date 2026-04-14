"""Pydantic schemas for the wallet service."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


TransactionType = Literal[
    "deposit",
    "withdrawal",
    "ln_send",
    "ln_receive",
    "escrow_lock",
    "escrow_release",
    "fee",
]


class OnchainWithdrawalRequest(BaseModel):
    address: str = Field(min_length=14, max_length=90)
    amount_sat: int = Field(ge=1)
    fee_rate_sat_vb: int = Field(ge=1, le=1_000)

    @field_validator("address")
    @classmethod
    def _validate_address_prefix(cls, value: str) -> str:
        lowered = value.lower()
        valid_prefixes = ("bc1", "tb1", "bcrt1")
        if not lowered.startswith(valid_prefixes):
            raise ValueError("Address must be a bech32 on-chain Bitcoin address")
        return lowered


class OnchainAddressResponse(BaseModel):
    address: str
    type: Literal["taproot"]


class OnchainWithdrawalResponse(BaseModel):
    txid: str
    amount_sat: int
    fee_sat: int
    status: Literal["pending", "confirmed", "failed"]


class TransactionHistoryItem(BaseModel):
    id: str
    type: TransactionType
    amount_sat: int
    direction: Literal["in", "out"]
    status: Literal["pending", "confirmed", "failed"]
    description: str | None = None
    created_at: datetime


class TransactionHistoryResponse(BaseModel):
    transactions: list[TransactionHistoryItem]
    next_cursor: str | None
