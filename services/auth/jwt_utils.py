"""JWT helper – issues and verifies access/refresh tokens.

JWT payload (per architecture.md §4.1):
    sub       : user UUID (str)
    role      : user role string
    wallet_id : wallet UUID str | None  (None until wallet provisioned)
    type      : "access" | "refresh"
    jti       : unique token id (for future blacklisting)
    exp / iat : standard claims
"""
from __future__ import annotations

from dataclasses import dataclass
import uuid
from datetime import datetime, timezone
from typing import Literal

from jose import JWTError, jwt


_ALGORITHM = "HS256"

# expires_in expected by the API contract (seconds)
ACCESS_TOKEN_EXPIRE_SECONDS = 900  # 15 min
REFRESH_TOKEN_EXPIRE_SECONDS = 60 * 60 * 24 * 7  # 7 days


@dataclass(frozen=True)
class RefreshTokenEnvelope:
    token: str
    jti: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedTokenPair:
    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_token_jti: str
    refresh_expires_at: datetime


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def create_access_token(
    *,
    user_id: str,
    role: str,
    wallet_id: str | None,
    secret: str,
) -> str:
    now = _utc_now()
    payload = {
        "sub": user_id,
        "role": role,
        "wallet_id": wallet_id,
        "type": "access",
        "jti": str(uuid.uuid4()),
        "iat": _epoch(now),
        "exp": _epoch(now) + ACCESS_TOKEN_EXPIRE_SECONDS,
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def create_refresh_token(
    *,
    user_id: str,
    role: str,
    secret: str,
) -> RefreshTokenEnvelope:
    now = _utc_now()
    jti = str(uuid.uuid4())
    expires_at = now.replace(microsecond=0)
    expires_at = expires_at.fromtimestamp(_epoch(now) + REFRESH_TOKEN_EXPIRE_SECONDS, tz=timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "type": "refresh",
        "jti": jti,
        "iat": _epoch(now),
        "exp": _epoch(now) + REFRESH_TOKEN_EXPIRE_SECONDS,
    }
    return RefreshTokenEnvelope(
        token=jwt.encode(payload, secret, algorithm=_ALGORITHM),
        jti=jti,
        expires_at=expires_at,
    )


def decode_token(
    token: str,
    secret: str,
    *,
    expected_type: Literal["access", "refresh"] | None = None,
) -> dict:
    """Decode and validate a JWT.  Raises jose.JWTError on invalid tokens."""
    claims = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    if expected_type is not None and claims.get("type") != expected_type:
        raise JWTError("Unexpected token type.")
    return claims


def issue_token_pair(
    *,
    user_id: str,
    role: str,
    wallet_id: str | None,
    secret: str,
) -> IssuedTokenPair:
    """Return access + refresh tokens plus refresh-session metadata."""
    refresh = create_refresh_token(user_id=user_id, role=role, secret=secret)
    access = create_access_token(
        user_id=user_id, role=role, wallet_id=wallet_id, secret=secret
    )
    return IssuedTokenPair(
        access_token=access,
        refresh_token=refresh.token,
        access_expires_in=ACCESS_TOKEN_EXPIRE_SECONDS,
        refresh_token_jti=refresh.jti,
        refresh_expires_at=refresh.expires_at,
    )
