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

import uuid
from datetime import datetime, timezone
from typing import Literal

from jose import JWTError, jwt


_ALGORITHM = "HS256"

# expires_in expected by the API contract (seconds)
ACCESS_TOKEN_EXPIRE_SECONDS = 900  # 15 min
REFRESH_TOKEN_EXPIRE_SECONDS = 60 * 60 * 24 * 7  # 7 days


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
) -> str:
    now = _utc_now()
    payload = {
        "sub": user_id,
        "role": role,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": _epoch(now),
        "exp": _epoch(now) + REFRESH_TOKEN_EXPIRE_SECONDS,
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_token(token: str, secret: str) -> dict:
    """Decode and validate a JWT.  Raises jose.JWTError on invalid tokens."""
    return jwt.decode(token, secret, algorithms=[_ALGORITHM])


def issue_token_pair(
    *,
    user_id: str,
    role: str,
    wallet_id: str | None,
    secret: str,
) -> tuple[str, str]:
    """Return (access_token, refresh_token)."""
    access = create_access_token(
        user_id=user_id, role=role, wallet_id=wallet_id, secret=secret
    )
    refresh = create_refresh_token(user_id=user_id, role=role, secret=secret)
    return access, refresh
