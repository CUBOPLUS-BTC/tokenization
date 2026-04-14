from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

# Add parent directory to path to allow imports from common
sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import get_settings

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
