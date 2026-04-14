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


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    id: str
    email: str | None = None
    display_name: str
    role: str
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
