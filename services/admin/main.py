from __future__ import annotations

import base64
import hashlib
import hmac
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from typing import Annotated
import uuid

from fastapi import Depends, FastAPI, Header, Query, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth.jwt_utils import decode_token
from common import get_readiness_payload, get_settings
from admin.db import (
    create_course,
    disburse_treasury,
    get_dispute_by_trade_id,
    get_user_by_id,
    list_treasury_entries,
    list_users,
    update_user_role,
)
from admin.schemas import (
    AdminDisputeResolveRequest,
    CourseOut,
    CourseResponse,
    CreateCourseRequest,
    DisputeOut,
    DisputeResponse,
    TreasuryDisburseRequest,
    TreasuryDisburseResponse,
    TreasuryEntryOut,
    UpdateUserRoleRequest,
    UserListResponse,
    UserOut,
)
from marketplace.db import resolve_dispute


settings = get_settings(service_name="admin", default_port=8006)
_bearer_scheme = HTTPBearer(auto_error=False)
_engine: AsyncEngine | object | None = None

# ---------------------------------------------------------------------------
# TOTP helpers (same implementation as marketplace)
# ---------------------------------------------------------------------------

_TOTP_DIGITS = 6
_TOTP_PERIOD_SECONDS = 30


def _generate_totp(secret: str, counter: int) -> str:
    normalized = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(normalized, casefold=True)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(binary % (10**_TOTP_DIGITS)).zfill(_TOTP_DIGITS)


def _verify_totp_code(secret: str, code: str) -> bool:
    normalized = code.strip()
    if not normalized.isdigit() or len(normalized) != _TOTP_DIGITS:
        return False
    counter = int(time.time() // _TOTP_PERIOD_SECONDS)
    try:
        return any(
            hmac.compare_digest(_generate_totp(secret, counter + offset), normalized)
            for offset in (-1, 0, 1)
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# App bootstrap
# ---------------------------------------------------------------------------


class ContractError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    id: str
    role: str


def _make_async_url(sync_url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if sync_url.startswith(prefix):
            return "postgresql+asyncpg://" + sync_url[len(prefix):]
    return sync_url


def _runtime_engine() -> AsyncEngine | object:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _make_async_url(settings.database_url), pool_pre_ping=True
        )
    return _engine


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _runtime_engine()
    yield
    await _runtime_engine().dispose()


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _row_value(row: object, key: str, default: object | None = None):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, default)


def _aware_datetime(value) -> datetime | None:
    if value is None:
        return None
    if hasattr(value, "tzinfo") and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _jwt_secret() -> str:
    return settings.jwt_secret or "dev-secret-change-me"


def _normalize_uuid_claim(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------


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
        raise ContractError(
            code="invalid_token",
            message="Access token is invalid or expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    user_id = _normalize_uuid_claim(claims.get("sub"))
    role = claims.get("role")
    if user_id is None or not isinstance(role, str):
        raise ContractError(
            code="invalid_token",
            message="Access token is invalid or expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    async with _runtime_engine().connect() as conn:
        row = await get_user_by_id(conn, user_id)

    if row is None or _row_value(row, "deleted_at") is not None:
        raise ContractError(
            code="invalid_token",
            message="Access token is invalid or expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return AuthenticatedPrincipal(id=user_id, role=role)


async def _require_admin(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
) -> AuthenticatedPrincipal:
    if principal.role != "admin":
        raise ContractError(
            code="forbidden",
            message="Admin role is required.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return principal


async def _check_2fa(conn: object, user_id: str, code: str | None) -> None:
    """Verify 2FA code when the user has TOTP configured."""
    user_row = await get_user_by_id(conn, user_id)
    totp_secret = _row_value(user_row, "totp_secret") if user_row is not None else None
    if not totp_secret:
        return
    if code is None:
        raise ContractError(
            code="2fa_required",
            message="Two-factor authentication code is required.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not _verify_totp_code(str(totp_secret), code):
        raise ContractError(
            code="2fa_invalid",
            message="Invalid two-factor authentication code.",
            status_code=status.HTTP_403_FORBIDDEN,
        )


# ---------------------------------------------------------------------------
# Row -> schema helpers
# ---------------------------------------------------------------------------


def _user_out(row: object) -> UserOut:
    return UserOut(
        id=str(_row_value(row, "id")),
        email=_row_value(row, "email"),
        display_name=_row_value(row, "display_name"),
        role=_row_value(row, "role"),
        created_at=_aware_datetime(_row_value(row, "created_at")),
    )


def _course_out(row: object) -> CourseOut:
    return CourseOut(
        id=str(_row_value(row, "id")),
        title=_row_value(row, "title"),
        description=_row_value(row, "description"),
        category=_row_value(row, "category"),
        difficulty=_row_value(row, "difficulty"),
        content_url=_row_value(row, "content_url"),
    )


def _treasury_entry_out(row: object) -> TreasuryEntryOut:
    return TreasuryEntryOut(
        id=str(_row_value(row, "id")),
        type=_row_value(row, "type"),
        amount_sat=int(_row_value(row, "amount_sat", 0)),
        balance_after_sat=int(_row_value(row, "balance_after_sat", 0)),
        reference_id=(
            str(_row_value(row, "source_trade_id"))
            if _row_value(row, "source_trade_id") is not None
            else None
        ),
        description=_row_value(row, "description"),
        created_at=_aware_datetime(_row_value(row, "created_at")),
    )


def _dispute_out(row: object) -> DisputeOut:
    return DisputeOut(
        id=str(_row_value(row, "id")),
        trade_id=str(_row_value(row, "trade_id")),
        opened_by=str(_row_value(row, "opened_by")),
        reason=_row_value(row, "reason"),
        status=_row_value(row, "status"),
        resolution=_row_value(row, "resolution"),
        resolved_by=(
            str(_row_value(row, "resolved_by"))
            if _row_value(row, "resolved_by") is not None
            else None
        ),
        notes=None,
        resolved_at=_aware_datetime(_row_value(row, "resolved_at")),
        created_at=_aware_datetime(_row_value(row, "created_at")),
        updated_at=_aware_datetime(_row_value(row, "updated_at")),
    )


def _build_user_page(
    rows: list[object],
    *,
    cursor: str | None,
    limit: int,
) -> tuple[list[object], str | None]:
    start_index = 0
    if cursor is not None:
        try:
            cursor_uuid = str(uuid.UUID(cursor))
        except ValueError as exc:
            raise ContractError(
                code="invalid_cursor",
                message="Cursor must be a valid user UUID.",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from exc
        for i, row in enumerate(rows):
            if str(_row_value(row, "id")) == cursor_uuid:
                start_index = i + 1
                break
        else:
            raise ContractError(
                code="invalid_cursor",
                message="Cursor does not match a user in the result set.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    page = rows[start_index : start_index + limit]
    next_cursor = (
        str(_row_value(page[-1], "id"))
        if start_index + limit < len(rows) and page
        else None
    )
    return page, next_cursor


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Admin Service", lifespan=_lifespan)


@app.exception_handler(ContractError)
async def contract_exception_handler(request: Request, exc: ContractError):
    return _error(exc.code, exc.message, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error(
        "validation_error",
        "Request payload failed validation.",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


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
    status_code = 200 if payload["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=payload)


# ---------------------------------------------------------------------------
# 7.1  List Users
# ---------------------------------------------------------------------------


@app.get(
    "/users",
    response_model=UserListResponse,
    summary="List users (admin only)",
)
async def list_users_endpoint(
    role: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(_require_admin),
):
    async with _runtime_engine().connect() as conn:
        rows = await list_users(conn, role=role)

    page, next_cursor = _build_user_page(rows, cursor=cursor, limit=limit)
    return UserListResponse(
        users=[_user_out(r) for r in page],
        next_cursor=next_cursor,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# 7.2  Update User Role
# ---------------------------------------------------------------------------


@app.patch(
    "/users/{user_id}",
    response_model=UserOut,
    summary="Update a user's role (admin only)",
)
async def update_user_role_endpoint(
    user_id: uuid.UUID,
    body: UpdateUserRoleRequest,
    principal: AuthenticatedPrincipal = Depends(_require_admin),
):
    async with _runtime_engine().connect() as conn:
        row = await update_user_role(conn, user_id=user_id, new_role=body.role)

    if row is None:
        return _error("user_not_found", "User not found.", status.HTTP_404_NOT_FOUND)

    return _user_out(row)


# ---------------------------------------------------------------------------
# 7.4  Create Course
# ---------------------------------------------------------------------------


@app.post(
    "/courses",
    status_code=status.HTTP_201_CREATED,
    response_model=CourseResponse,
    summary="Create a new course (admin only)",
)
async def create_course_endpoint(
    body: CreateCourseRequest,
    principal: AuthenticatedPrincipal = Depends(_require_admin),
):
    async with _runtime_engine().connect() as conn:
        row = await create_course(
            conn,
            title=body.title,
            description=body.description,
            content_url=str(body.content_url),
            category=body.category,
            difficulty=body.difficulty,
        )

    return CourseResponse(course=_course_out(row)).model_dump(mode="json")


# ---------------------------------------------------------------------------
# 7.5  Disburse Treasury Funds
# ---------------------------------------------------------------------------


@app.post(
    "/treasury/disburse",
    response_model=TreasuryDisburseResponse,
    summary="Disburse treasury funds (admin only, requires 2FA)",
)
async def disburse_treasury_endpoint(
    body: TreasuryDisburseRequest,
    x_2fa_code: Annotated[str | None, Header(alias="X-2FA-Code")] = None,
    principal: AuthenticatedPrincipal = Depends(_require_admin),
):
    async with _runtime_engine().connect() as conn:
        await _check_2fa(conn, principal.id, x_2fa_code)
        try:
            row = await disburse_treasury(
                conn,
                amount_sat=body.amount_sat,
                description=body.description,
            )
        except ValueError as exc:
            if "insufficient" in str(exc):
                return _error(
                    "insufficient_treasury_balance",
                    "Treasury balance is insufficient for this disbursement.",
                    status.HTTP_409_CONFLICT,
                )
            raise

    return TreasuryDisburseResponse(
        entry=_treasury_entry_out(row)
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# 7.3  Resolve Dispute (admin escrow resolve)
# ---------------------------------------------------------------------------


@app.post(
    "/escrows/{trade_id}/resolve",
    response_model=DisputeResponse,
    summary="Resolve an escrow dispute (admin only, requires 2FA)",
)
async def resolve_escrow_dispute_endpoint(
    trade_id: uuid.UUID,
    body: AdminDisputeResolveRequest,
    x_2fa_code: Annotated[str | None, Header(alias="X-2FA-Code")] = None,
    principal: AuthenticatedPrincipal = Depends(_require_admin),
):
    # Map API resolution values to internal DB values
    resolution_map = {
        "refund_buyer": "refund",
        "release_to_seller": "release",
    }
    db_resolution = resolution_map[body.resolution]

    async with _runtime_engine().connect() as conn:
        await _check_2fa(conn, principal.id, x_2fa_code)

        existing_dispute = await get_dispute_by_trade_id(conn, trade_id)
        if existing_dispute is None:
            return _error(
                "dispute_not_found",
                "No dispute found for this trade.",
                status.HTTP_404_NOT_FOUND,
            )

        if _row_value(existing_dispute, "status") != "open":
            return _error(
                "dispute_already_resolved",
                "This dispute has already been resolved.",
                status.HTTP_409_CONFLICT,
            )

        try:
            dispute_row, _trade_row, _escrow_row = await resolve_dispute(
                conn,
                trade_id=trade_id,
                resolved_by=principal.id,
                resolution=db_resolution,
            )
        except LookupError as exc:
            return _error(
                "resolution_conflict",
                "Could not apply resolution due to a state conflict.",
                status.HTTP_409_CONFLICT,
            )

    return DisputeResponse(dispute=_dispute_out(dispute_row)).model_dump(mode="json")


if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
