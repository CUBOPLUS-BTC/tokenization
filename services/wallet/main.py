from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timezone
import hashlib
import hmac
from pathlib import Path
import secrets
import sys
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_readiness_payload, get_settings

from .db import (
    create_onchain_withdrawal,
    get_or_create_wallet,
    get_user_by_id,
    list_wallet_transactions,
)
from .schemas import (
    OnchainAddressResponse,
    OnchainWithdrawalRequest,
    OnchainWithdrawalResponse,
    TransactionHistoryItem,
    TransactionHistoryResponse,
    TransactionType,
)

settings = get_settings(service_name="wallet", default_port=8001)

_ALGORITHM = "HS256"
_DEFAULT_TX_VSIZE = 141
_TOTP_DIGITS = 6
_TOTP_PERIOD_SECONDS = 30
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_bearer_scheme = HTTPBearer(auto_error=False)
_engine: object = None


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    id: str


class ContractError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _make_async_url(sync_url: str) -> str:
    url = sync_url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _engine
    async_url = _make_async_url(settings.database_url)
    _engine = create_async_engine(async_url, pool_pre_ping=True)
    yield
    await _engine.dispose()


app = FastAPI(title="Wallet Service", lifespan=_lifespan)


def _jwt_secret() -> str:
    return settings.jwt_secret or "dev-secret-change-me"


def _normalize_uuid_claim(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _invalid_access_token_error() -> ContractError:
    return ContractError(
        code="invalid_token",
        message="Access token is invalid or expired.",
        status_code=status.HTTP_401_UNAUTHORIZED,
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
        claims = jwt.decode(credentials.credentials, _jwt_secret(), algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise _invalid_access_token_error() from exc

    if claims.get("type") != "access":
        raise _invalid_access_token_error()

    user_id = _normalize_uuid_claim(claims.get("sub"))
    if user_id is None:
        raise _invalid_access_token_error()

    async with _engine.connect() as conn:
        row = await get_user_by_id(conn, user_id)

    if row is None or getattr(row, "deleted_at", None) is not None:
        raise _invalid_access_token_error()

    return AuthenticatedPrincipal(id=user_id)


def _network_hrp() -> str:
    network = settings.bitcoin_network.lower()
    if network == "mainnet":
        return "bc"
    if network == "regtest":
        return "bcrt"
    return "tb"


def _generate_onchain_address() -> str:
    suffix = "".join(secrets.choice(_BECH32_CHARSET) for _ in range(58))
    return f"{_network_hrp()}1p{suffix}"


def _estimate_onchain_fee(fee_rate_sat_vb: int) -> int:
    return fee_rate_sat_vb * _DEFAULT_TX_VSIZE


def _generate_txid(*, wallet_id: str, address: str, amount_sat: int, fee_sat: int) -> str:
    payload = f"{wallet_id}:{address}:{amount_sat}:{fee_sat}:{time.time_ns()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _generate_totp(secret: str, counter: int) -> str:
    normalized = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(normalized, casefold=True)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(binary % (10**_TOTP_DIGITS)).zfill(_TOTP_DIGITS)


def _verify_totp_code(secret: str, code: str, *, now: float | None = None) -> bool:
    normalized_code = code.strip()
    if not normalized_code.isdigit() or len(normalized_code) != _TOTP_DIGITS:
        return False

    current_time = time.time() if now is None else now
    counter = int(current_time // _TOTP_PERIOD_SECONDS)
    try:
        return any(
            hmac.compare_digest(_generate_totp(secret, counter + offset), normalized_code)
            for offset in (-1, 0, 1)
        )
    except (ValueError, base64.binascii.Error):
        return False


def _sort_transaction_rows(rows: list[object]) -> list[object]:
    return sorted(
        rows,
        key=lambda row: (getattr(row, "created_at"), str(getattr(row, "id"))),
        reverse=True,
    )


def _build_transaction_page(
    rows: list[object],
    *,
    cursor: str | None,
    limit: int,
    transaction_type: TransactionType | None,
) -> tuple[list[object], str | None]:
    filtered_rows = [
        row for row in _sort_transaction_rows(rows)
        if transaction_type is None or getattr(row, "type") == transaction_type
    ]

    start_index = 0
    if cursor is not None:
        try:
            cursor_uuid = str(uuid.UUID(cursor))
        except ValueError as exc:
            raise ContractError(
                code="invalid_cursor",
                message="Cursor must be a valid transaction UUID.",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from exc

        for index, row in enumerate(filtered_rows):
            if str(getattr(row, "id")) == cursor_uuid:
                start_index = index + 1
                break
        else:
            raise ContractError(
                code="invalid_cursor",
                message="Cursor does not match a transaction in this result set.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    page = filtered_rows[start_index:start_index + limit]
    next_cursor = str(page[-1].id) if start_index + limit < len(filtered_rows) and page else None
    return page, next_cursor


def _transaction_history_item(row: object) -> TransactionHistoryItem:
    created_at = getattr(row, "created_at")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    return TransactionHistoryItem(
        id=str(getattr(row, "id")),
        type=getattr(row, "type"),
        amount_sat=getattr(row, "amount_sat"),
        direction=getattr(row, "direction"),
        status=getattr(row, "status"),
        description=getattr(row, "description"),
        created_at=created_at,
    )


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


@app.post(
    "/wallet/onchain/address",
    status_code=status.HTTP_201_CREATED,
    response_model=OnchainAddressResponse,
    summary="Create a new on-chain deposit address",
)
@app.post(
    "/onchain/address",
    status_code=status.HTTP_201_CREATED,
    response_model=OnchainAddressResponse,
    include_in_schema=False,
)
async def create_onchain_address(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _engine.connect() as conn:
        await get_or_create_wallet(conn, principal.id)

    return OnchainAddressResponse(address=_generate_onchain_address(), type="taproot").model_dump()


@app.post(
    "/wallet/onchain/withdraw",
    status_code=status.HTTP_200_OK,
    response_model=OnchainWithdrawalResponse,
    summary="Submit an on-chain withdrawal",
)
@app.post(
    "/onchain/withdraw",
    status_code=status.HTTP_200_OK,
    response_model=OnchainWithdrawalResponse,
    include_in_schema=False,
)
async def withdraw_onchain(
    body: OnchainWithdrawalRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    two_fa_code: str | None = Header(default=None, alias="X-2FA-Code"),
):
    if not two_fa_code:
        raise ContractError(
            code="two_factor_required",
            message="X-2FA-Code header is required for withdrawals.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    async with _engine.connect() as conn:  # type: AsyncConnection
        user = await get_user_by_id(conn, principal.id)
        if user is None or getattr(user, "deleted_at", None) is not None:
            raise _invalid_access_token_error()
        if not getattr(user, "totp_secret", None):
            raise ContractError(
                code="two_factor_not_enabled",
                message="Two-factor authentication must be enabled before withdrawing.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if not _verify_totp_code(user.totp_secret, two_fa_code):
            raise ContractError(
                code="invalid_2fa_code",
                message="Two-factor authentication code is invalid.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        wallet = await get_or_create_wallet(conn, principal.id)
        fee_sat = _estimate_onchain_fee(body.fee_rate_sat_vb)
        row = await create_onchain_withdrawal(
            conn,
            wallet_id=str(wallet.id),
            amount_sat=body.amount_sat,
            fee_sat=fee_sat,
            txid=_generate_txid(
                wallet_id=str(wallet.id),
                address=body.address,
                amount_sat=body.amount_sat,
                fee_sat=fee_sat,
            ),
            description=f"On-chain withdrawal to {body.address}",
        )

    if row is None:
        raise ContractError(
            code="insufficient_funds",
            message="Wallet balance is insufficient for this withdrawal and fee.",
            status_code=status.HTTP_409_CONFLICT,
        )

    return OnchainWithdrawalResponse(
        txid=row.txid,
        amount_sat=row.amount_sat,
        fee_sat=fee_sat,
        status=row.status,
    ).model_dump()


@app.get(
    "/wallet/transactions",
    status_code=status.HTTP_200_OK,
    response_model=TransactionHistoryResponse,
    summary="Return paginated wallet transaction history",
)
@app.get(
    "/transactions",
    status_code=status.HTTP_200_OK,
    response_model=TransactionHistoryResponse,
    include_in_schema=False,
)
async def get_transaction_history(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    transaction_type: TransactionType | None = Query(default=None, alias="type"),
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _engine.connect() as conn:
        wallet = await get_or_create_wallet(conn, principal.id)
        rows = await list_wallet_transactions(conn, str(wallet.id))

    page, next_cursor = _build_transaction_page(
        rows,
        cursor=cursor,
        limit=limit,
        transaction_type=transaction_type,
    )

    return TransactionHistoryResponse(
        transactions=[_transaction_history_item(row) for row in page],
        next_cursor=next_cursor,
    ).model_dump(mode="json")


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


if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
