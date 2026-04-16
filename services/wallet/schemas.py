"""Pydantic schemas for the wallet service."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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
    txHash: str | None = Field(None, description="Bitcoin transaction ID (txid) for on-chain transactions")
    paymentHash: str | None = Field(None, description="Lightning payment hash for LN transactions")
    fee_sat: int | None = Field(None, description="Fee paid in satoshis")


class TransactionHistoryResponse(BaseModel):
    transactions: list[TransactionHistoryItem]
    next_cursor: str | None


class CustodyStatusResponse(BaseModel):
    configured_backend: Literal["software", "hsm"]
    wallet_backend: Literal["software", "hsm"]
    signer_backend: Literal["software", "hsm"]
    state: Literal["ready", "degraded"]
    key_reference: str | None = None
    signer_key_reference: str | None = None
    derivation_path: str
    seed_exportable: bool
    withdraw_requires_2fa: bool
    server_compromise_impact: str
    disclaimers: list[str]


class FiatOnRampProviderStatus(BaseModel):
    provider_id: str
    display_name: str
    state: Literal["ready", "pending_redirect", "kyc_required", "limited", "unavailable"]
    supported_fiat_currencies: list[str]
    supported_countries: list[str]
    payment_methods: list[str]
    min_fiat_amount: Decimal
    max_fiat_amount: Decimal
    requires_kyc: bool
    disclaimer: str
    external_handoff_url: str


class FiatOnRampProvidersResponse(BaseModel):
    providers: list[FiatOnRampProviderStatus]
    compliance_notices: list[str]


class FiatOnRampSessionRequest(BaseModel):
    provider_id: str = Field(min_length=2, max_length=40)
    fiat_currency: str = Field(min_length=3, max_length=3)
    fiat_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    country_code: str = Field(min_length=2, max_length=2)
    return_url: str = Field(min_length=1, max_length=2048)
    cancel_url: str = Field(min_length=1, max_length=2048)

    @field_validator("provider_id")
    @classmethod
    def _normalize_provider_id(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("fiat_currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("country_code")
    @classmethod
    def _normalize_country(cls, value: str) -> str:
        return value.strip().upper()


class FiatOnRampSessionResponse(BaseModel):
    session_id: str
    provider_id: str
    state: Literal["ready", "pending_redirect", "kyc_required", "limited", "unavailable"]
    handoff_url: str
    deposit_address: str
    destination_wallet_id: str
    expires_at: datetime
    disclaimer: str
    compliance_action: Literal["review_terms", "complete_kyc"]

class OnchainAddressResponse(BaseModel):
    address: str
    type: str

class FeeEstimateLevel(BaseModel):
    sat_per_vb: int
    target_blocks: int

class FeeEstimateResponse(BaseModel):
    low: FeeEstimateLevel
    medium: FeeEstimateLevel
    high: FeeEstimateLevel

class OnchainWithdrawalRequest(BaseModel):
    address: str
    amount_sat: int
    fee_rate_sat_vb: int

class OnchainWithdrawalResponse(BaseModel):
    txid: str
    amount_sat: int
    fee_sat: int
    status: str
