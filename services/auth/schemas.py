"""Pydantic schemas for the auth service, aligned with api-contracts.md."""
from __future__ import annotations

import re
from datetime import datetime

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


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    id: str
    email: str
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


# ---------------------------------------------------------------------------
# Error schema (contract: { "error": { "code": "...", "message": "..." } })
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
