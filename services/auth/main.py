"""FastAPI application for the Auth Service.

Endpoints implemented (api-contracts.md §2):
    POST /auth/register  → 201  AuthResponse
    POST /auth/login     → 200  AuthResponse
    POST /auth/refresh   → 200  AuthResponse
    POST /auth/logout    → 200  MessageResponse
    GET  /auth/me        → 200  UserOut

Error body follows the contract:
    { "error": { "code": "<slug>", "message": "<human>" } }
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import uvicorn
from jose import JWTError

# Local imports -----------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_settings
from common.readiness import get_readiness_payload

from .schemas import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    RoleCheckResponse,
    TokensOut,
    UserOut,
)
from .jwt_utils import decode_token, issue_token_pair
from .db import (
    create_refresh_session,
    create_user,
    get_user_by_email,
    get_user_by_id,
    revoke_refresh_session,
    rotate_refresh_session,
)

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
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


class ContractError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    id: str
    email: str | None
    display_name: str
    role: str
    created_at: datetime


def _jwt_secret() -> str:
    return settings.jwt_secret or "dev-secret-change-me"


def _normalize_uuid_claim(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _user_out(row) -> UserOut:
    return UserOut(
        id=str(row.id),
        email=row.email,
        display_name=row.display_name,
        role=row.role,
        created_at=row.created_at,
    )


def _auth_response_payload(row, tokens) -> dict:
    return AuthResponse(
        user=_user_out(row),
        tokens=TokensOut(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.access_expires_in,
        ),
    ).model_dump(mode="json")


def _invalid_access_token_error() -> ContractError:
    return ContractError(
        code="invalid_token",
        message="Access token is invalid or expired.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def _invalid_refresh_token_response() -> JSONResponse:
    return _error(
        "invalid_refresh_token",
        "Refresh token is invalid, expired, or already used.",
        status.HTTP_401_UNAUTHORIZED,
    )


async def _issue_auth_response(row, *, conn: AsyncConnection, status_code: int) -> JSONResponse:
    tokens = issue_token_pair(
        user_id=str(row.id),
        role=row.role,
        wallet_id=None,
        secret=_jwt_secret(),
    )
    await create_refresh_session(
        conn,
        user_id=str(row.id),
        token_jti=tokens.refresh_token_jti,
        expires_at=tokens.refresh_expires_at,
    )
    return JSONResponse(
        status_code=status_code,
        content=_auth_response_payload(row, tokens),
    )


async def _get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> AuthenticatedPrincipal:
    if credentials is None:
        raise ContractError(
            code="authentication_required",
            message="Authentication is required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        claims = decode_token(
            credentials.credentials,
            _jwt_secret(),
            expected_type="access",
        )
    except JWTError as exc:
        raise _invalid_access_token_error() from exc

    user_id = _normalize_uuid_claim(claims.get("sub"))
    if user_id is None:
        raise _invalid_access_token_error()

    async with _engine.connect() as conn:  # type: AsyncConnection
        row = await get_user_by_id(conn, user_id)

    if row is None or getattr(row, "deleted_at", None) is not None:
        raise _invalid_access_token_error()

    return AuthenticatedPrincipal(
        id=str(row.id),
        email=row.email,
        display_name=row.display_name,
        role=row.role,
        created_at=row.created_at,
    )


def _require_roles(*allowed_roles: str):
    async def dependency(
        principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    ) -> AuthenticatedPrincipal:
        if principal.role not in allowed_roles:
            raise ContractError(
                code="forbidden",
                message="You do not have permission to access this resource.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return principal

    return dependency


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error(
        code="validation_error",
        message="Request payload failed validation.",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@app.exception_handler(ContractError)
async def contract_exception_handler(request: Request, exc: ContractError):
    return _error(exc.code, exc.message, exc.status_code)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return _error(
        code="http_error",
        message=str(exc.detail),
        status_code=exc.status_code,
    )


def _build_auth_response(row, *, secret: str) -> dict:
    """Build the contract-compliant AuthResponse dict for a user row."""
    tokens = issue_token_pair(
        user_id=str(row.id),
        role=row.role,
        wallet_id=None,
        secret=secret,
    )
    return _auth_response_payload(row, tokens)


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

        return await _issue_auth_response(
            row,
            conn=conn,
            status_code=status.HTTP_201_CREATED,
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

    async with _engine.connect() as conn:  # type: AsyncConnection
        return await _issue_auth_response(
            row,
            conn=conn,
            status_code=status.HTTP_200_OK,
        )


@app.post(
    "/auth/refresh",
    status_code=status.HTTP_200_OK,
    response_model=AuthResponse,
    summary="Rotate a refresh token and issue a fresh token pair",
)
async def refresh(body: RefreshRequest):
    try:
        claims = decode_token(body.refresh_token, _jwt_secret(), expected_type="refresh")
    except JWTError:
        return _invalid_refresh_token_response()

    user_id = _normalize_uuid_claim(claims.get("sub"))
    refresh_token_jti = _normalize_uuid_claim(claims.get("jti"))
    if user_id is None or refresh_token_jti is None:
        return _invalid_refresh_token_response()

    async with _engine.connect() as conn:  # type: AsyncConnection
        row = await get_user_by_id(conn, user_id)
        if row is None or getattr(row, "deleted_at", None) is not None:
            return _invalid_refresh_token_response()

        tokens = issue_token_pair(
            user_id=user_id,
            role=row.role,
            wallet_id=None,
            secret=_jwt_secret(),
        )
        rotated = await rotate_refresh_session(
            conn,
            user_id=user_id,
            current_token_jti=refresh_token_jti,
            replacement_token_jti=tokens.refresh_token_jti,
            replacement_expires_at=tokens.refresh_expires_at,
        )

    if not rotated:
        return _invalid_refresh_token_response()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=_auth_response_payload(row, tokens),
    )


@app.post(
    "/auth/logout",
    status_code=status.HTTP_200_OK,
    response_model=MessageResponse,
    summary="Revoke a refresh-token session",
)
async def logout(body: LogoutRequest):
    try:
        claims = decode_token(body.refresh_token, _jwt_secret(), expected_type="refresh")
    except JWTError:
        return _invalid_refresh_token_response()

    user_id = _normalize_uuid_claim(claims.get("sub"))
    refresh_token_jti = _normalize_uuid_claim(claims.get("jti"))
    if user_id is None or refresh_token_jti is None:
        return _invalid_refresh_token_response()

    async with _engine.connect() as conn:  # type: AsyncConnection
        revoked = await revoke_refresh_session(
            conn,
            user_id=user_id,
            token_jti=refresh_token_jti,
        )

    if not revoked:
        return _invalid_refresh_token_response()

    return MessageResponse(message="Session revoked.").model_dump()


@app.get(
    "/auth/me",
    status_code=status.HTTP_200_OK,
    response_model=UserOut,
    summary="Return the currently authenticated user",
)
async def get_current_user(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    return UserOut(
        id=principal.id,
        email=principal.email or "",
        display_name=principal.display_name,
        role=principal.role,
        created_at=principal.created_at,
    )


def _role_response(principal: AuthenticatedPrincipal, *required_roles: str) -> RoleCheckResponse:
    return RoleCheckResponse(
        status="allowed",
        actor_role=principal.role,
        required_roles=list(required_roles),
    )


@app.get(
    "/auth/roles/user",
    status_code=status.HTTP_200_OK,
    response_model=RoleCheckResponse,
    summary="Verify access for user-level actions",
)
async def user_role_check(
    principal: AuthenticatedPrincipal = Depends(_require_roles("user", "seller", "admin")),
):
    return _role_response(principal, "user", "seller", "admin")


@app.get(
    "/auth/roles/seller",
    status_code=status.HTTP_200_OK,
    response_model=RoleCheckResponse,
    summary="Verify access for seller-level actions",
)
async def seller_role_check(
    principal: AuthenticatedPrincipal = Depends(_require_roles("seller", "admin")),
):
    return _role_response(principal, "seller", "admin")


@app.get(
    "/auth/roles/admin",
    status_code=status.HTTP_200_OK,
    response_model=RoleCheckResponse,
    summary="Verify access for admin-level actions",
)
async def admin_role_check(
    principal: AuthenticatedPrincipal = Depends(_require_roles("admin")),
):
    return _role_response(principal, "admin")


@app.get(
    "/auth/roles/auditor",
    status_code=status.HTTP_200_OK,
    response_model=RoleCheckResponse,
    summary="Verify access for auditor-level actions",
)
async def auditor_role_check(
    principal: AuthenticatedPrincipal = Depends(_require_roles("auditor", "admin")),
):
    return _role_response(principal, "auditor", "admin")


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
