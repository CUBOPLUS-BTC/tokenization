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
from datetime import datetime, timezone
import base64
import hashlib
import ipaddress
from pathlib import Path
import logging
import secrets
import string
import sys
import time
import uuid

from typing import Annotated, Optional

import pyotp
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Security,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import uvicorn
from jose import JWTError

# Local imports -----------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import get_settings
from common import (
    ALL_API_KEY_SCOPES,
    allowed_api_key_scopes_for_role,
    create_referral_signup_reward,
    default_onramp_notices,
    describe_custody_settings,
    generate_referral_code,
    get_user_by_referral_code,
    list_onramp_provider_views,
    list_referral_rewards_for_user,
    list_referred_users,
    record_audit_event,
    summarize_referrals_for_user,
    extract_api_key_prefix,
    invalidate_api_key_verify_cache,
)
from common.security import install_http_security
from common.readiness import get_readiness_payload
from common.logging import configure_structured_logging
from common.metrics import metrics, mount_metrics_endpoint, record_business_event
from common.alerting import alert_dispatcher, AlertSeverity, configure_alerting
from common.db.schema_check import ensure_schema_ready

from schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListResponse,
    ApiKeyOut,
    ApiKeyVerifyRequest,
    ApiKeyVerifyResponse,
    AuthResponse,
    KycAdminUpdateRequest,
    KycListResponse,
    KycStatusOut,
    KycStatusResponse,
    KycSubmitRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    NostrChallengeResponse,
    NostrLoginRequest,
    RefreshRequest,
    RegisterRequest,
    RoleCheckResponse,
    TokensOut,
    TwoFactorEnableResponse,
    TwoFactorVerifyRequest,
    UserOut,
    OnboardingCustodyOut,
    OnboardingFiatProviderOut,
    OnboardingSummaryResponse,
    ReferralRewardOut,
    ReferralSummaryResponse,
    ReferredUserOut,
)
from jwt_utils import decode_token, issue_token_pair
from services.auth.nostr_utils import (
    NostrValidationError,
    extract_challenge_from_event,
    validate_nostr_event,
)
from db import (
    create_api_key,
    create_nostr_identity,
    create_nostr_user,
    create_refresh_session,
    create_user,
    get_api_key_by_id,
    get_api_key_by_name,
    get_api_key_by_prefix,
    enable_2fa,
    set_user_2fa_verified,
    get_nostr_identity_by_pubkey,
    get_user_2fa_secret,
    get_user_by_email,
    get_user_by_id,
    list_api_keys_for_user,
    revoke_refresh_session,
    revoke_api_key,
    rotate_refresh_session,
    rotate_api_key,
    touch_api_key_last_used,
)
from services.wallet.db import get_or_create_wallet
from kyc_db import (
    create_kyc_record,
    get_kyc_status,
    is_kyc_verified,
    list_kyc_records,
    update_kyc_status,
)

import bcrypt

# Pre-computed once at start-up; used so that logins for missing or null-credential
# accounts always run a full bcrypt verification to prevent timing side-channels.
_DUMMY_HASH: str = bcrypt.hashpw(b"__placeholder__", bcrypt.gensalt()).decode("utf-8")

# SQLAlchemy async engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection

# -------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

settings = get_settings(service_name="auth", default_port=8000)
configure_structured_logging(service_name=settings.service_name, log_level=settings.log_level)
configure_alerting(settings)

# bcrypt hashing config (using default rounds)
_API_KEY_ALPHABET = string.ascii_letters + string.digits
_API_KEY_VERIFY_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
_NOSTR_CHALLENGE_TTL_SECONDS = 300
_NOSTR_CHALLENGES: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Async DB engine (lifecycle)
# ---------------------------------------------------------------------------

def _make_async_url(sync_url: str) -> str:
    """Convert a standard postgres:// URL to asyncpg driver URL."""
    url = sync_url
    if url.startswith("postgresql+asyncpg://"):
        return url
    for prefix in ("postgresql+", "postgres+"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url.split("://", 1)[1]
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
    await ensure_schema_ready(
        _engine,
        (
            "users",
            "api_keys",
            "refresh_token_sessions",
            "kyc_verifications",
            "nostr_identities",
        ),
    )
    yield
    await _engine.dispose()


app = FastAPI(title="Auth Service", lifespan=_lifespan)

install_http_security(
    app,
    settings,
    sensitive_paths=(
        "/auth/login",
        "/auth/register",
        "/auth/refresh",
        "/auth/logout",
        "/auth/nostr/challenge",
        "/auth/nostr",
        "/auth/2fa",
    ),
)
mount_metrics_endpoint(app, settings)
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
        referral_code=getattr(row, "referral_code", None),
        created_at=row.created_at,
    )


def _referred_user_out(row) -> ReferredUserOut:
    return ReferredUserOut(
        id=str(row.id),
        email=row.email,
        display_name=row.display_name,
        created_at=row.created_at,
    )


def _referral_reward_out(row) -> ReferralRewardOut:
    return ReferralRewardOut(
        id=str(getattr(row, "id")),
        referred_user_id=str(getattr(row, "referred_user_id")),
        referred_display_name=getattr(getattr(row, "_mapping", {}), "get", lambda *_: None)("referred_display_name") or getattr(row, "referred_display_name"),
        referred_email=getattr(getattr(row, "_mapping", {}), "get", lambda *_: None)("referred_email") or getattr(row, "referred_email", None),
        reward_type=getattr(row, "reward_type"),
        amount_sat=int(getattr(row, "amount_sat", 0)),
        status=getattr(row, "status"),
        eligibility_event=getattr(row, "eligibility_event"),
        credited_at=getattr(row, "credited_at"),
        created_at=getattr(row, "created_at"),
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


async def _require_2fa(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    x_2fa_code: Annotated[str | None, Header(None)] = None,
) -> None:
    """Dependency that enforces X-2FA-Code if 2FA is enabled for the user."""
    async with _engine.connect() as conn:
        secret = await get_user_2fa_secret(conn, principal.id)

    # Only enforce if 2FA is actually enabled
    if secret:
        if not x_2fa_code:
            raise ContractError(
                code="2fa_required",
                message="Two-factor authentication code is required for this operation.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        totp = pyotp.TOTP(secret)
        if not totp.verify(x_2fa_code, valid_window=1):
            raise ContractError(
                code="invalid_2fa_code",
                message="The provided two-factor authentication code is invalid or expired.",
                status_code=status.HTTP_401_UNAUTHORIZED,
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
    logger.warning("Validation error: %s", exc.errors())
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


def _row_value(row: object, key: str, default=None):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, default)


def _api_key_out(row) -> ApiKeyOut:
    return ApiKeyOut(
        id=str(_row_value(row, "id")),
        name=str(_row_value(row, "name")),
        key_prefix=str(_row_value(row, "key_prefix")),
        scopes=list(_row_value(row, "scopes") or []),
        last_used_at=_row_value(row, "last_used_at"),
        expires_at=_row_value(row, "expires_at"),
        revoked=bool(_row_value(row, "revoked")),
        created_at=_row_value(row, "created_at"),
    )


def _validate_requested_api_key_scopes(*, role: str, scopes: list[str]) -> list[str]:
    normalized_scopes = [str(scope).strip() for scope in scopes if str(scope).strip()]
    if not normalized_scopes:
        raise ContractError(
            code="invalid_scopes",
            message="At least one API key scope is required.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    unknown_scopes = sorted(set(normalized_scopes) - set(ALL_API_KEY_SCOPES))
    if unknown_scopes:
        raise ContractError(
            code="invalid_scopes",
            message=f"Unknown API key scopes requested: {', '.join(unknown_scopes)}.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    allowed_scopes = allowed_api_key_scopes_for_role(role)
    disallowed_scopes = sorted(set(normalized_scopes) - allowed_scopes)
    if disallowed_scopes:
        raise ContractError(
            code="invalid_scopes",
            message=f"Requested scopes are not permitted for this user: {', '.join(disallowed_scopes)}.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return sorted(set(normalized_scopes))


def _api_key_response(row, *, key: str) -> dict:
    return ApiKeyCreateResponse(
        id=str(_row_value(row, "id")),
        name=str(_row_value(row, "name")),
        key=key,
        key_prefix=str(_row_value(row, "key_prefix")),
        scopes=list(_row_value(row, "scopes") or []),
        expires_at=_row_value(row, "expires_at"),
        created_at=_row_value(row, "created_at"),
    ).model_dump(mode="json")


async def _generate_api_key_material(conn: AsyncConnection) -> tuple[str, str]:
    suffix_bytes = max(int(settings.api_key_suffix_bytes), 1)
    prefix_length = max(int(settings.api_key_prefix_length), 1)

    for _ in range(10):
        prefix = "".join(secrets.choice(_API_KEY_ALPHABET) for _ in range(prefix_length))
        if await get_api_key_by_prefix(conn, key_prefix=prefix) is not None:
            continue

        suffix = base64.urlsafe_b64encode(secrets.token_bytes(suffix_bytes)).rstrip(b"=").decode("ascii")
        return prefix, f"{prefix}_{suffix}"

    raise ContractError(
        code="api_key_generation_failed",
        message="Unable to generate a unique API key prefix.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _api_key_hash(raw_key: str) -> str:
    rounds = max(int(settings.api_key_bcrypt_rounds), 4)
    return bcrypt.hashpw(
        raw_key.encode("utf-8"),
        bcrypt.gensalt(rounds=rounds),
    ).decode("utf-8")


def _api_key_cache_digest(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _clear_api_key_verify_cache(*, key_id: str | None = None, raw_key: str | None = None) -> None:
    invalidate_api_key_verify_cache(
        key_id=key_id,
        digest=_api_key_cache_digest(raw_key) if raw_key else None,
    )
    if raw_key:
        _API_KEY_VERIFY_CACHE.pop(_api_key_cache_digest(raw_key), None)
    if key_id:
        stale_digests = [
            digest
            for digest, (_, cached_payload) in _API_KEY_VERIFY_CACHE.items()
            if cached_payload.get("key_id") == key_id
        ]
        for digest in stale_digests:
            _API_KEY_VERIFY_CACHE.pop(digest, None)


def _cache_api_key_verify_result(raw_key: str, payload: dict[str, object]) -> None:
    _API_KEY_VERIFY_CACHE[_api_key_cache_digest(raw_key)] = (time.monotonic(), payload)


def _get_cached_api_key_verify_result(raw_key: str) -> dict[str, object] | None:
    digest = _api_key_cache_digest(raw_key)
    cached = _API_KEY_VERIFY_CACHE.get(digest)
    if cached is None:
        return None

    cached_at, payload = cached
    if time.monotonic() - cached_at >= max(int(settings.api_key_cache_ttl_seconds), 1):
        _API_KEY_VERIFY_CACHE.pop(digest, None)
        return None
    return payload


def _prune_expired_nostr_challenges(*, now: float | None = None) -> None:
    current_time = time.monotonic() if now is None else now
    expired = [
        challenge
        for challenge, expires_at in _NOSTR_CHALLENGES.items()
        if expires_at <= current_time
    ]
    for challenge in expired:
        _NOSTR_CHALLENGES.pop(challenge, None)


def _issue_nostr_challenge() -> str:
    _prune_expired_nostr_challenges()
    nonce = secrets.token_urlsafe(24)
    _NOSTR_CHALLENGES[nonce] = time.monotonic() + _NOSTR_CHALLENGE_TTL_SECONDS
    return nonce


def _is_active_nostr_challenge(challenge: str) -> bool:
    _prune_expired_nostr_challenges()
    expires_at = _NOSTR_CHALLENGES.get(challenge)
    return expires_at is not None and expires_at > time.monotonic()


def _consume_nostr_challenge(challenge: str) -> bool:
    _prune_expired_nostr_challenges()
    expires_at = _NOSTR_CHALLENGES.get(challenge)
    if expires_at is None or expires_at <= time.monotonic():
        _NOSTR_CHALLENGES.pop(challenge, None)
        return False
    _NOSTR_CHALLENGES.pop(challenge, None)
    return True


def _is_private_network_request(request: Request) -> bool:
    host = request.client.host if request.client is not None else ""
    if not host:
        return settings.env_profile == "local"
    if host == "testclient":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "auth", "gateway"}
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
        )
    )


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

        referrer_id: str | None = None
        if body.referrer_code:
            referrer_row = await get_user_by_referral_code(conn, body.referrer_code)
            if referrer_row is None:
                return _error(
                    "referrer_not_found",
                    "Referral code is invalid.",
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            if getattr(referrer_row, "email", None) == body.email:
                return _error(
                    "self_referral_not_allowed",
                    "You cannot register using your own referral code.",
                    status.HTTP_409_CONFLICT,
                )
            referrer_id = str(referrer_row.id)

        password_hash = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        try:
            referral_code = await generate_referral_code(conn)
            row = await create_user(
                conn,
                email=body.email,
                password_hash=password_hash,
                display_name=body.display_name,
                referral_code=referral_code,
                referrer_id=referrer_id,
            )

            # Auto-provision a wallet for the new user.
            # We do this within the same transaction context to ensure consistency.
            try:
                await get_or_create_wallet(conn, str(row.id))
            except Exception as exc:
                logger.warning("Failed to auto-provision wallet for user %s: %s", row.id, exc)
        except IntegrityError:
            return _error(
                "email_taken",
                "An account with that email already exists.",
                status.HTTP_409_CONFLICT,
            )

        response = await _issue_auth_response(
            row,
            conn=conn,
            status_code=status.HTTP_201_CREATED,
        )
    record_business_event("auth_register")
    return response


@app.post(
    "/auth/2fa/enable",
    response_model=TwoFactorEnableResponse,
    summary="Enable two-factor authentication",
)
async def enable_2fa_endpoint(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    """Generate a TOTP secret and backup codes for the user."""
    async with _engine.connect() as conn:
        existing_secret = await get_user_2fa_secret(conn, principal.id)
        user = await get_user_by_id(conn, principal.id)
        is_verified = getattr(user, "is_verified", False) if user else False
        
        if existing_secret and is_verified:
            raise ContractError(
                code="2fa_already_enabled",
                message="Two-factor authentication is already enabled for this account.",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Generate TOTP secret
        totp_secret = pyotp.random_base32()

        # Generate 8 backup codes (8-char hex strings)
        backup_codes = [uuid.uuid4().hex[:8] for _ in range(8)]

        await enable_2fa(
            conn,
            user_id=principal.id,
            totp_secret=totp_secret,
            backup_codes=backup_codes,
        )

    # Build provisioning URI
    totp = pyotp.TOTP(totp_secret)
    totp_uri = totp.provisioning_uri(
        name=principal.email or principal.display_name,
        issuer_name=settings.totp_issuer,
    )

    record_business_event("auth_2fa_enable")
    return TwoFactorEnableResponse(
        totp_uri=totp_uri,
        backup_codes=backup_codes,
    )


@app.post(
    "/auth/2fa/verify",
    summary="Verify a two-factor authentication code",
)
async def verify_2fa_endpoint(
    body: TwoFactorVerifyRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    """Verify a TOTP code to confirm 2FA works."""
    async with _engine.connect() as conn:
        secret = await get_user_2fa_secret(conn, principal.id)

    if not secret:
        raise ContractError(
            code="2fa_not_enabled",
            message="Two-factor authentication is not enabled for this account.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    totp = pyotp.TOTP(secret)
    if not totp.verify(body.totp_code, valid_window=1):
        raise ContractError(
            code="invalid_2fa_code",
            message="The provided two-factor authentication code is invalid or expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    async with _engine.connect() as conn:
        await set_user_2fa_verified(conn, principal.id)

    record_business_event("auth_2fa_verify")
    return {"message": "2FA verification successful."}


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
        record_business_event("auth_login", outcome="failure")
        return _error(
            "invalid_credentials",
            "Invalid email or password.",
            status.HTTP_401_UNAUTHORIZED,
        )

    async with _engine.connect() as conn:  # type: AsyncConnection
        response = await _issue_auth_response(
            row,
            conn=conn,
            status_code=status.HTTP_200_OK,
        )
    record_business_event("auth_login")
    return response


@app.post(
    "/auth/nostr/challenge",
    status_code=status.HTTP_200_OK,
    response_model=NostrChallengeResponse,
    summary="Issue a one-time Nostr sign-in challenge",
)
async def issue_nostr_challenge():
    challenge = _issue_nostr_challenge()
    return NostrChallengeResponse(
        challenge=f"Sign-in challenge: {challenge}",
        nonce=challenge,
        kind=22242,
        expires_in=_NOSTR_CHALLENGE_TTL_SECONDS,
    )


@app.post(
    "/auth/nostr",
    status_code=status.HTTP_200_OK,
    response_model=AuthResponse,
    summary="Log in or register with a Nostr identity",
)
async def nostr_login(body: NostrLoginRequest):
    """Authenticate via Nostr signature challenge."""
    try:
        challenge = extract_challenge_from_event(body.signed_event)
    except NostrValidationError as e:
        record_business_event("auth_nostr_login", outcome="failure")
        return _error(
            "invalid_credentials",
            str(e),
            status.HTTP_401_UNAUTHORIZED,
        )

    if not _is_active_nostr_challenge(challenge):
        record_business_event("auth_nostr_login", outcome="failure")
        return _error(
            "invalid_credentials",
            "Challenge is missing, expired, or already used.",
            status.HTTP_401_UNAUTHORIZED,
        )

    try:
        validate_nostr_event(
            body.pubkey,
            body.signed_event,
            expected_challenge=challenge,
        )
    except NostrValidationError as e:
        record_business_event("auth_nostr_login", outcome="failure")
        return _error(
            "invalid_credentials",
            str(e),
            status.HTTP_401_UNAUTHORIZED,
        )

    if not _consume_nostr_challenge(challenge):
        record_business_event("auth_nostr_login", outcome="failure")
        return _error(
            "invalid_credentials",
            "Challenge is missing, expired, or already used.",
            status.HTTP_401_UNAUTHORIZED,
        )

    async with _engine.connect() as conn:  # type: AsyncConnection
        identity_row = await get_nostr_identity_by_pubkey(conn, body.pubkey)
        
        if identity_row is not None:
            user_row = await get_user_by_id(conn, str(identity_row.user_id))
            if user_row is None:
                return _error(
                    "invalid_credentials",
                    "Linked user account not found.",
                    status.HTTP_401_UNAUTHORIZED,
                )
        else:
            display_name = f"nostr:{body.pubkey[:8]}"
            referral_code = await generate_referral_code(conn)
            user_row = await create_nostr_user(
                conn,
                display_name=display_name,
                referral_code=referral_code,
            )
            await create_nostr_identity(
                conn,
                user_id=str(user_row.id),
                pubkey=body.pubkey,
                relay_urls=None,
            )

        response = await _issue_auth_response(
            user_row,
            conn=conn,
            status_code=status.HTTP_200_OK,
        )
    record_business_event("auth_nostr_login")
    return response


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
        record_business_event("auth_refresh", outcome="failure")
        return _invalid_refresh_token_response()

    record_business_event("auth_refresh")
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
        record_business_event("auth_logout", outcome="failure")
        return _invalid_refresh_token_response()

    record_business_event("auth_logout")
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


@app.get(
    "/auth/referrals/summary",
    status_code=status.HTTP_200_OK,
    response_model=ReferralSummaryResponse,
    summary="Return referral relationships and earned rewards for the authenticated user",
)
async def get_referral_summary(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _engine.connect() as conn:
        user_row = await get_user_by_id(conn, principal.id)
        if user_row is None:
            raise _invalid_access_token_error()
        counts = await summarize_referrals_for_user(conn, principal.id)
        referred_rows = await list_referred_users(conn, principal.id)
        reward_rows = await list_referral_rewards_for_user(conn, principal.id)

    return ReferralSummaryResponse(
        referral_code=getattr(user_row, "referral_code"),
        referrals_count=counts["referrals_count"],
        total_reward_sat=counts["total_reward_sat"],
        referred_users=[_referred_user_out(row) for row in referred_rows],
        rewards=[_referral_reward_out(row) for row in reward_rows],
    ).model_dump(mode="json")


def _api_key_target_user_id(
    principal: AuthenticatedPrincipal,
    requested_user_id: str | None,
) -> str:
    if requested_user_id is None:
        return principal.id
    if requested_user_id == principal.id:
        return principal.id
    if principal.role != "admin":
        raise ContractError(
            code="forbidden",
            message="Only admins can manage API keys for other users.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return requested_user_id


@app.post(
    "/auth/api-keys",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiKeyCreateResponse,
    summary="Create a new API key",
)
async def create_api_key_endpoint(
    request: Request,
    body: ApiKeyCreateRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    if body.expires_at is not None and body.expires_at <= datetime.now(tz=timezone.utc):
        raise ContractError(
            code="invalid_expiration",
            message="API key expiration must be in the future.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    scopes = _validate_requested_api_key_scopes(role=principal.role, scopes=body.scopes)

    async with _engine.connect() as conn:
        existing = await get_api_key_by_name(conn, user_id=principal.id, name=body.name)
        if existing is not None:
            raise ContractError(
                code="api_key_name_taken",
                message="An API key with that name already exists.",
                status_code=status.HTTP_409_CONFLICT,
            )

        key_prefix, raw_key = await _generate_api_key_material(conn)
        row = await create_api_key(
            conn,
            user_id=principal.id,
            name=body.name,
            key_prefix=key_prefix,
            key_hash=_api_key_hash(raw_key),
            scopes=scopes,
            expires_at=body.expires_at,
            created_by=principal.id,
        )
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="api_key_create",
            actor_id=principal.id,
            actor_role=principal.role,
            target_type="api_key",
            target_id=_row_value(row, "id"),
            metadata={
                "key_prefix": key_prefix,
                "name": body.name,
                "scopes": scopes,
                "expires_at": body.expires_at.isoformat() if body.expires_at else None,
            },
        )

    record_business_event("auth_api_key_create")
    return _api_key_response(row, key=raw_key)


@app.get(
    "/auth/api-keys",
    status_code=status.HTTP_200_OK,
    response_model=ApiKeyListResponse,
    summary="List API keys",
)
async def list_api_keys_endpoint(
    user_id: str | None = None,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    target_user_id = _api_key_target_user_id(principal, user_id)
    async with _engine.connect() as conn:
        rows = await list_api_keys_for_user(conn, user_id=target_user_id)
    return ApiKeyListResponse(keys=[_api_key_out(row) for row in rows]).model_dump(mode="json")


@app.delete(
    "/auth/api-keys/{key_id}",
    status_code=status.HTTP_200_OK,
    response_model=MessageResponse,
    summary="Revoke an API key",
)
async def revoke_api_key_endpoint(
    request: Request,
    key_id: str,
    user_id: str | None = None,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    target_user_id = _api_key_target_user_id(principal, user_id)
    async with _engine.connect() as conn:
        existing = await get_api_key_by_id(conn, key_id=key_id)
        if existing is None or str(_row_value(existing, "user_id")) != target_user_id:
            raise ContractError(
                code="api_key_not_found",
                message="API key not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if bool(_row_value(existing, "revoked")):
            return MessageResponse(message="Key revoked").model_dump()

        revoked_row = await revoke_api_key(conn, key_id=key_id)
        if revoked_row is None:
            raise ContractError(
                code="api_key_not_found",
                message="API key not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="api_key_revoke",
            actor_id=principal.id,
            actor_role=principal.role,
            target_type="api_key",
            target_id=key_id,
            metadata={"owner_id": target_user_id, "key_prefix": _row_value(revoked_row, "key_prefix")},
        )

    _clear_api_key_verify_cache(key_id=key_id)
    record_business_event("auth_api_key_revoke")
    return MessageResponse(message="Key revoked").model_dump()


@app.patch(
    "/auth/api-keys/{key_id}/rotate",
    status_code=status.HTTP_200_OK,
    response_model=ApiKeyCreateResponse,
    summary="Rotate an API key",
)
async def rotate_api_key_endpoint(
    request: Request,
    key_id: str,
    user_id: str | None = None,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    target_user_id = _api_key_target_user_id(principal, user_id)
    async with _engine.connect() as conn:
        existing = await get_api_key_by_id(conn, key_id=key_id)
        if existing is None or str(_row_value(existing, "user_id")) != target_user_id:
            raise ContractError(
                code="api_key_not_found",
                message="API key not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if bool(_row_value(existing, "revoked")):
            raise ContractError(
                code="api_key_revoked",
                message="Revoked API keys cannot be rotated.",
                status_code=status.HTTP_409_CONFLICT,
            )

        key_prefix, raw_key = await _generate_api_key_material(conn)
        rotated_row = await rotate_api_key(
            conn,
            key_id=key_id,
            key_prefix=key_prefix,
            key_hash=_api_key_hash(raw_key),
        )
        if rotated_row is None:
            raise ContractError(
                code="api_key_not_found",
                message="API key not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="api_key_rotate",
            actor_id=principal.id,
            actor_role=principal.role,
            target_type="api_key",
            target_id=key_id,
            metadata={"owner_id": target_user_id, "key_prefix": key_prefix},
        )

    _clear_api_key_verify_cache(key_id=key_id)
    record_business_event("auth_api_key_rotate")
    return _api_key_response(rotated_row, key=raw_key)


@app.post(
    "/internal/api-keys/verify",
    status_code=status.HTTP_200_OK,
    response_model=ApiKeyVerifyResponse,
    summary="Verify an API key for internal services",
)
async def verify_api_key_endpoint(
    request: Request,
    body: ApiKeyVerifyRequest,
):
    if not _is_private_network_request(request):
        raise ContractError(
            code="forbidden",
            message="This endpoint is only available on the internal network.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    cached = _get_cached_api_key_verify_result(body.api_key)
    if cached is not None:
        if not bool(cached.get("valid")):
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=cached)
        return cached

    key_prefix = extract_api_key_prefix(
        body.api_key,
        prefix_length=settings.api_key_prefix_length,
    )
    if key_prefix is None:
        payload = {"valid": False, "reason": "not_found"}
        _cache_api_key_verify_result(body.api_key, payload)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=payload)

    async with _engine.connect() as conn:
        row = await get_api_key_by_prefix(conn, key_prefix=key_prefix)
        if row is None:
            payload = {"valid": False, "reason": "not_found"}
            _cache_api_key_verify_result(body.api_key, payload)
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=payload)

        if bool(_row_value(row, "revoked")):
            payload = {"valid": False, "reason": "revoked"}
            _cache_api_key_verify_result(body.api_key, payload)
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=payload)

        expires_at = _row_value(row, "expires_at")
        if expires_at is not None and expires_at <= datetime.now(tz=timezone.utc):
            payload = {"valid": False, "reason": "expired"}
            _cache_api_key_verify_result(body.api_key, payload)
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=payload)

        try:
            valid = bcrypt.checkpw(
                body.api_key.encode("utf-8"),
                str(_row_value(row, "key_hash")).encode("utf-8"),
            )
        except ValueError:
            valid = False

        if not valid:
            payload = {"valid": False, "reason": "not_found"}
            _cache_api_key_verify_result(body.api_key, payload)
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=payload)

        await touch_api_key_last_used(conn, key_id=str(_row_value(row, "id")))
        user_row = await get_user_by_id(conn, str(_row_value(row, "user_id")))
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="api_key_authenticate",
            actor_id=str(_row_value(row, "user_id")),
            actor_role=_row_value(user_row, "role"),
            target_type="api_key",
            target_id=_row_value(row, "id"),
            metadata={
                "key_id": str(_row_value(row, "id")),
                "scopes": list(_row_value(row, "scopes") or []),
                "key_prefix": str(_row_value(row, "key_prefix")),
            },
        )

    payload = ApiKeyVerifyResponse(
        valid=True,
        user_id=str(_row_value(row, "user_id")),
        scopes=list(_row_value(row, "scopes") or []),
        key_id=str(_row_value(row, "id")),
    ).model_dump(mode="json")
    _cache_api_key_verify_result(body.api_key, payload)
    return payload


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
# KYC Verification Endpoints
# ---------------------------------------------------------------------------


def _kyc_out(row) -> KycStatusOut:
    mapping = getattr(row, "_mapping", None)
    def _val(key: str, default=None):
        if mapping is not None:
            return mapping.get(key, default)
        return getattr(row, key, default)
    return KycStatusOut(
        id=str(_val("id")),
        user_id=str(_val("user_id")),
        status=_val("status"),
        reviewed_by=str(_val("reviewed_by")) if _val("reviewed_by") else None,
        reviewed_at=_val("reviewed_at"),
        rejection_reason=_val("rejection_reason"),
        notes=_val("notes"),
        created_at=_val("created_at"),
        updated_at=_val("updated_at"),
    )


def _kyc_state_label(row) -> str:
    if row is None:
        return "not_started"
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return str(mapping.get("status", "not_started"))
    return str(getattr(row, "status", "not_started"))


@app.get(
    "/auth/onboarding/summary",
    status_code=status.HTTP_200_OK,
    response_model=OnboardingSummaryResponse,
    summary="Return onboarding custody and fiat handoff requirements",
)
async def onboarding_summary(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _engine.connect() as conn:
        user_row = await get_user_by_id(conn, principal.id)
        kyc_row = await get_kyc_status(conn, principal.id)

    if user_row is None:
        raise _invalid_access_token_error()

    custody = describe_custody_settings(settings)
    providers = list_onramp_provider_views(kyc_verified=is_kyc_verified(kyc_row))
    record_business_event("auth_onboarding_summary")
    return OnboardingSummaryResponse(
        user=_user_out(user_row),
        kyc_status=_kyc_state_label(kyc_row),
        custody=OnboardingCustodyOut(
            configured_backend=custody.backend,
            signer_backend=custody.signer_backend,
            state=custody.state,
            key_reference=custody.key_reference,
            signer_key_reference=custody.signer_key_reference,
            seed_exportable=custody.seed_exportable,
            server_compromise_impact=custody.server_compromise_impact,
            disclaimers=list(custody.disclaimers),
        ),
        fiat_onramp_providers=[
            OnboardingFiatProviderOut(
                provider_id=provider.provider_id,
                display_name=provider.display_name,
                state=provider.state,
                supported_fiat_currencies=list(provider.supported_fiat_currencies),
                supported_countries=list(provider.supported_countries),
                payment_methods=list(provider.payment_methods),
                requires_kyc=provider.requires_kyc,
                disclaimer=provider.disclaimer,
                external_handoff_url=provider.external_handoff_url,
            )
            for provider in providers
        ],
        compliance_notices=default_onramp_notices(),
    )


@app.post(
    "/auth/kyc/submit",
    status_code=status.HTTP_201_CREATED,
    response_model=KycStatusResponse,
    summary="Submit KYC verification request",
)
async def submit_kyc(
    body: KycSubmitRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    """Submit a KYC verification request.

    Creates or returns the existing record.  Once submitted the platform
    operator reviews and approves/rejects via the admin endpoint.
    """
    async with _engine.connect() as conn:
        existing = await get_kyc_status(conn, principal.id)
        if existing is not None:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=KycStatusResponse(kyc=_kyc_out(existing)).model_dump(mode="json"),
            )

        row = await create_kyc_record(
            conn,
            user_id=principal.id,
            document_url=body.document_url,
            notes=body.notes,
        )

    record_business_event("kyc_submit")
    return KycStatusResponse(kyc=_kyc_out(row)).model_dump(mode="json")


@app.get(
    "/auth/kyc/status",
    response_model=KycStatusResponse,
    summary="Get the current user's KYC verification status",
)
async def get_my_kyc_status(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    """Return the verification status of the authenticated user.

    Returns 404 if the user has not submitted a KYC request.
    """
    async with _engine.connect() as conn:
        row = await get_kyc_status(conn, principal.id)

    if row is None:
        return _error(
            "kyc_not_found",
            "No KYC verification record found. Please submit a KYC request first.",
            status.HTTP_404_NOT_FOUND,
        )

    return KycStatusResponse(kyc=_kyc_out(row)).model_dump(mode="json")


@app.get(
    "/auth/kyc/admin",
    response_model=KycListResponse,
    summary="List all KYC verification records (admin only)",
)
async def list_kyc_admin(
    status_filter: str | None = None,
    principal: AuthenticatedPrincipal = Depends(_require_roles("admin")),
):
    """Return all KYC records.  Admins can filter by status."""
    async with _engine.connect() as conn:
        rows = await list_kyc_records(conn, status_filter=status_filter)

    return KycListResponse(
        records=[_kyc_out(r) for r in rows],
    ).model_dump(mode="json")


@app.put(
    "/auth/kyc/admin/{user_id}",
    response_model=KycStatusResponse,
    summary="Update a user's KYC verification status (admin only)",
)
async def update_kyc_admin(
    request: Request,
    user_id: str,
    body: KycAdminUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(_require_roles("admin")),
):
    """Admin-only endpoint to approve, reject, or expire a user's KYC status."""
    async with _engine.connect() as conn:
        existing = await get_kyc_status(conn, user_id)
        if existing is None:
            return _error(
                "kyc_not_found",
                "No KYC verification record found for this user.",
                status.HTTP_404_NOT_FOUND,
            )

        existing_status = getattr(existing, "status", None)
        mapping = getattr(existing, "_mapping", None)
        if mapping is not None:
            existing_status = mapping.get("status", existing_status)

        row = await update_kyc_status(
            conn,
            user_id=user_id,
            new_status=body.status,
            reviewed_by=principal.id,
            rejection_reason=body.rejection_reason,
            notes=body.notes,
        )
        if body.status == "verified" and existing_status != "verified":
            await create_referral_signup_reward(conn, referred_user_id=user_id)
            await conn.commit()

    if row is None:
        return _error(
            "kyc_update_failed",
            "Failed to update KYC status.",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    record_business_event("kyc_admin_update")
    return KycStatusResponse(kyc=_kyc_out(row)).model_dump(mode="json")


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
