"""Pydantic schemas for the auth service, aligned with api-contracts.md."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)
    referrer_code: str | None = Field(default=None, min_length=6, max_length=12)

    @field_validator("password")
    @classmethod
    def _password_complexity(cls, v: str) -> str:
        """Require at least one uppercase, one digit, and one special character."""
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[^A-Za-z0-9]", v):
            raise ValueError("Password must contain at least one special character")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class NostrSignedEvent(BaseModel):
    id: str = Field(min_length=64, max_length=64)
    kind: int
    created_at: int
    content: str
    tags: list[list[str]] = []
    sig: str = Field(min_length=128, max_length=128)


class NostrLoginRequest(BaseModel):
    pubkey: str = Field(min_length=64, max_length=64)
    signed_event: NostrSignedEvent


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None


class ApiKeyVerifyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=256)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    id: str
    email: str | None = None
    display_name: str
    role: str
    referral_code: str | None = None
    created_at: datetime



class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int  # seconds – always 900 (15 min)


class AuthResponse(BaseModel):
    user: UserOut
    tokens: TokensOut


class MessageResponse(BaseModel):
    message: str


class RoleCheckResponse(BaseModel):
    status: Literal["allowed"]
    actor_role: str
    required_roles: list[str]


class TwoFactorEnableResponse(BaseModel):
    totp_uri: str
    backup_codes: list[str]


class TwoFactorVerifyRequest(BaseModel):
    totp_code: str = Field(min_length=6, max_length=6)


# ---------------------------------------------------------------------------
# Error schema (contract: { "error": { "code": "...", "message": "..." } })
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# KYC verification schemas
# ---------------------------------------------------------------------------

KycStatus = Literal["pending", "verified", "rejected", "expired"]


class KycSubmitRequest(BaseModel):
    document_url: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=2000)


class KycAdminUpdateRequest(BaseModel):
    status: KycStatus
    rejection_reason: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class KycStatusOut(BaseModel):
    id: str
    user_id: str
    status: KycStatus
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class KycStatusResponse(BaseModel):
    kyc: KycStatusOut


class KycListResponse(BaseModel):
    records: list[KycStatusOut]


class OnboardingCustodyOut(BaseModel):
    configured_backend: Literal["software", "hsm"]
    signer_backend: Literal["software", "hsm"]
    state: Literal["ready", "degraded"]
    key_reference: str | None = None
    signer_key_reference: str | None = None
    seed_exportable: bool
    server_compromise_impact: str
    disclaimers: list[str]


class OnboardingFiatProviderOut(BaseModel):
    provider_id: str
    display_name: str
    state: Literal["ready", "pending_redirect", "kyc_required", "limited", "unavailable"]
    supported_fiat_currencies: list[str]
    supported_countries: list[str]
    payment_methods: list[str]
    requires_kyc: bool
    disclaimer: str
    external_handoff_url: str


class OnboardingSummaryResponse(BaseModel):
    user: UserOut
    kyc_status: str
    custody: OnboardingCustodyOut
    fiat_onramp_providers: list[OnboardingFiatProviderOut]
    compliance_notices: list[str]


class ReferredUserOut(BaseModel):
    id: str
    email: str | None = None
    display_name: str
    created_at: datetime


class ReferralRewardOut(BaseModel):
    id: str
    referred_user_id: str
    referred_display_name: str
    referred_email: str | None = None
    reward_type: Literal["signup_bonus"]
    amount_sat: int
    status: Literal["credited", "reversed"]
    eligibility_event: Literal["kyc_verified"]
    credited_at: datetime
    created_at: datetime


class ReferralSummaryResponse(BaseModel):
    referral_code: str
    referrals_count: int
    total_reward_sat: int
    referred_users: list[ReferredUserOut]
    rewards: list[ReferralRewardOut]


class ApiKeyOut(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked: bool
    created_at: datetime


class ApiKeyCreateResponse(BaseModel):
    id: str
    name: str
    key: str
    key_prefix: str
    scopes: list[str]
    expires_at: datetime | None = None
    created_at: datetime


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeyOut]


class ApiKeyVerifyResponse(BaseModel):
    valid: bool
    user_id: str
    scopes: list[str]
    key_id: str

