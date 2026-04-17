from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import pyotp
from db import get_user_2fa_secret, get_db_conn
from sqlalchemy.ext.asyncio import AsyncConnection

# Add parent directory to path to allow imports from common
sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import get_settings

os.environ.setdefault("ELEMENTS_RPC_HOST", "localhost")
os.environ.setdefault("ELEMENTS_RPC_PORT", "7041")
os.environ.setdefault("ELEMENTS_RPC_USER", "user")
os.environ.setdefault("ELEMENTS_RPC_PASSWORD", "pass")
os.environ.setdefault("ELEMENTS_NETWORK", "elementsregtest")

settings = get_settings(service_name="wallet", default_port=8001)
_bearer_scheme = HTTPBearer()

def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer_scheme)],
) -> str:
    """Dependency that validates the JWT and returns the user_id (sub)."""
    secret = settings.jwt_secret or "dev-secret-change-me"
    try:
        payload = jwt.decode(credentials.credentials, secret, algorithms=["HS256"])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Access token is invalid or expired.",
            )
        return str(user_id)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token is invalid or expired.",
        )

async def require_2fa(
    user_id: Annotated[str, Depends(get_current_user_id)],
    conn: Annotated[AsyncConnection, Depends(get_db_conn)],
    x_2fa_code: Annotated[str | None, Header()] = None,
) -> None:
    """Dependency that enforces X-2FA-Code if 2FA is enabled for the user."""
    secret = await get_user_2fa_secret(conn, user_id)

    # Only enforce if 2FA is actually enabled
    if secret:
        if not x_2fa_code:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Two-factor authentication code is required for this operation.",
            )

        totp = pyotp.TOTP(secret)
        if not totp.verify(x_2fa_code, valid_window=1):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="The provided two-factor authentication code is invalid or expired.",
            )
