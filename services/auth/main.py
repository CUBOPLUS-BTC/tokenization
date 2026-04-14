"""FastAPI application for the Auth Service.

Endpoints implemented (api-contracts.md §2):
    POST /auth/register  → 201  AuthResponse
    POST /auth/login     → 200  AuthResponse

Error body follows the contract:
    { "error": { "code": "<slug>", "message": "<human>" } }
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sys

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import uvicorn

# Local imports -----------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_settings
from common.readiness import get_readiness_payload

from .schemas import AuthResponse, LoginRequest, RegisterRequest, TokensOut, UserOut
from .jwt_utils import ACCESS_TOKEN_EXPIRE_SECONDS, issue_token_pair
from .db import create_user, get_user_by_email

import bcrypt

# Pre-computed once at start-up; used so that logins for missing or null-credential
# accounts always run a full bcrypt verification to prevent timing side-channels.
_DUMMY_HASH: str = bcrypt.hashpw(b"__placeholder__", bcrypt.gensalt()).decode("utf-8")

# SQLAlchemy async engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection

# -------------------------------------------------------------------------------

settings = get_settings(service_name="auth", default_port=8000)

# bcrypt hashing config (using default rounds)


# ---------------------------------------------------------------------------
# Async DB engine (lifecycle)
# ---------------------------------------------------------------------------

def _make_async_url(sync_url: str) -> str:
    """Convert a standard postgres:// URL to asyncpg driver URL."""
    url = sync_url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


_engine: object = None  # type: AsyncEngine


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _engine
    async_url = _make_async_url(settings.database_url)
    _engine = create_async_engine(async_url, pool_pre_ping=True)
    yield
    await _engine.dispose()


app = FastAPI(title="Auth Service", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error(
        code="validation_error",
        message="Request payload failed validation.",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return _error(
        code="http_error",
        message=str(exc.detail),
        status_code=exc.status_code,
    )


def _build_auth_response(row, *, secret: str) -> dict:
    """Build the contract-compliant AuthResponse dict for a user row."""
    user_id = str(row.id)
    access_token, refresh_token = issue_token_pair(
        user_id=user_id,
        role=row.role,
        wallet_id=None,  # wallet is provisioned separately
        secret=secret,
    )
    return AuthResponse(
        user=UserOut(
            id=user_id,
            email=row.email,
            display_name=row.display_name,
            role=row.role,
            created_at=row.created_at,
        ),
        tokens=TokensOut(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=ACCESS_TOKEN_EXPIRE_SECONDS,
        ),
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthResponse,
    summary="Register a new user",
)
async def register(body: RegisterRequest):
    """Register with email, password, and display_name.

    * Returns 409 if email already exists.
    * Returns 201 + access/refresh tokens on success.
    """
    async with _engine.connect() as conn:  # type: AsyncConnection
        existing = await get_user_by_email(conn, body.email)
        if existing is not None:
            return _error(
                "email_taken",
                "An account with that email already exists.",
                status.HTTP_409_CONFLICT,
            )

        password_hash = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        try:
            row = await create_user(
                conn,
                email=body.email,
                password_hash=password_hash,
                display_name=body.display_name,
            )
        except IntegrityError:
            return _error(
                "email_taken",
                "An account with that email already exists.",
                status.HTTP_409_CONFLICT,
            )

    secret = settings.jwt_secret or "dev-secret-change-me"
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=_build_auth_response(row, secret=secret),
    )


@app.post(
    "/auth/login",
    status_code=status.HTTP_200_OK,
    response_model=AuthResponse,
    summary="Log in and receive tokens",
)
async def login(body: LoginRequest):
    """Authenticate with email + password.

    * Returns 401 with a generic message for any credential mismatch
      (avoids leaking which field is wrong).
    """
    async with _engine.connect() as conn:  # type: AsyncConnection
        row = await get_user_by_email(conn, body.email)

    # Always run bcrypt to prevent user-enumeration via response timing.
    # A null password_hash means the account was created via social auth and
    # cannot be accessed with an email+password login → return 401, not 500.
    stored_hash = (row.password_hash or _DUMMY_HASH) if row is not None else _DUMMY_HASH

    # bcrypt requires bytes
    try:
        is_valid = bcrypt.checkpw(body.password.encode("utf-8"), stored_hash.encode("utf-8"))
    except ValueError:
        is_valid = False

    if not is_valid or row is None:
        return _error(
            "invalid_credentials",
            "Invalid email or password.",
            status.HTTP_401_UNAUTHORIZED,
        )

    secret = settings.jwt_secret or "dev-secret-change-me"
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=_build_auth_response(row, secret=secret),
    )


# ---------------------------------------------------------------------------
# Health / Readiness
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.service_name,
        "env_profile": settings.env_profile,
    }


@app.get("/ready")
async def ready():
    payload = get_readiness_payload(settings)
    code = 200 if payload["status"] == "ready" else 503
    return JSONResponse(status_code=code, content=payload)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
