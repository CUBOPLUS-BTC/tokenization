from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import logging
import os
from pathlib import Path
import secrets
import sys
import time
from types import MethodType
from typing import Any
import uuid

import grpc
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_readiness_payload, get_settings

from .auth import get_current_user_id, require_2fa
from .db import (
    create_onchain_withdrawal,
    create_transaction,
    get_db_conn,
    get_engine,
    get_or_create_wallet,
    get_token_balances_for_user,
    get_user_by_id,
    get_wallet_by_user_id,
    list_wallet_transactions,
)
from .lnd_client import LNDClient
from .log_filter import SensitiveDataFilter
from .schemas import (
    OnchainAddressResponse,
    OnchainWithdrawalRequest,
    OnchainWithdrawalResponse,
    TransactionHistoryItem,
    TransactionHistoryResponse,
    TransactionType,
)
from .schemas_lnd import (
    Invoice,
    InvoiceCreate,
    InvoiceStatus,
    Payment,
    PaymentCreate,
    PaymentStatus,
)
from .schemas_wallet import TokenBalance, WalletResponse, WalletSummary


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.addFilter(SensitiveDataFilter())

os.environ.setdefault("TAPD_MACAROON_PATH", "")
os.environ.setdefault("TAPD_TLS_CERT_PATH", "")

settings = get_settings(service_name="wallet", default_port=8001)
lnd_client = LNDClient(settings)

_ALGORITHM = "HS256"
_DEFAULT_TX_VSIZE = 141
_TOTP_DIGITS = 6
_TOTP_PERIOD_SECONDS = 30
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_bearer_scheme = HTTPBearer(auto_error=False)
_engine: AsyncEngine | Any | None = None


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


def _runtime_engine() -> AsyncEngine | Any:
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


def _row_value(row: object, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)

    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]

    if hasattr(row, key):
        return getattr(row, key)

    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError, IndexError):
        return default


@asynccontextmanager
async def _lifespan(app: FastAPI):
    engine = get_engine()
    global _engine
    if _engine is None:
        _engine = engine
    yield
    await engine.dispose()


@asynccontextmanager
async def _noop_lifespan(app: FastAPI):
    yield


app = FastAPI(title="Wallet Service", lifespan=_lifespan)
_original_router_lifespan = app.router.lifespan


async def _safe_router_lifespan(self, scope, receive, send):
    if self.lifespan_context is None:
        self.lifespan_context = _noop_lifespan
        try:
            await _original_router_lifespan(scope, receive, send)
        finally:
            self.lifespan_context = None
        return

    await _original_router_lifespan(scope, receive, send)


app.router.lifespan = MethodType(_safe_router_lifespan, app.router)


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

    async with _runtime_engine().connect() as conn:
        row = await get_user_by_id(conn, user_id)

    if row is None or _row_value(row, "deleted_at") is not None:
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
        key=lambda row: (_row_value(row, "created_at"), str(_row_value(row, "id"))),
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
        if transaction_type is None or _row_value(row, "type") == transaction_type
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
            if str(_row_value(row, "id")) == cursor_uuid:
                start_index = index + 1
                break
        else:
            raise ContractError(
                code="invalid_cursor",
                message="Cursor does not match a transaction in this result set.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    page = filtered_rows[start_index:start_index + limit]
    next_cursor = str(_row_value(page[-1], "id")) if start_index + limit < len(filtered_rows) and page else None
    return page, next_cursor


def _transaction_history_item(row: object) -> TransactionHistoryItem:
    created_at = _row_value(row, "created_at")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    return TransactionHistoryItem(
        id=str(_row_value(row, "id")),
        type=_row_value(row, "type"),
        amount_sat=_row_value(row, "amount_sat"),
        direction=_row_value(row, "direction"),
        status=_row_value(row, "status"),
        description=_row_value(row, "description"),
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


@app.get("/wallet", response_model=WalletResponse, tags=["Wallet"])
async def get_wallet_summary(user_id: str = Depends(get_current_user_id)):
    async with _runtime_engine().connect() as conn:
        wallet_row = await get_wallet_by_user_id(conn, user_id)
        if not wallet_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Wallet not found for user",
            )

        token_rows = await get_token_balances_for_user(conn, user_id)

    token_balances = [
        TokenBalance(
            token_id=row["token_id"],
            asset_name=row["asset_name"],
            symbol=None,
            balance=row["balance"],
            unit_price_sat=row["unit_price_sat"],
        )
        for row in token_rows
    ]

    onchain = _row_value(wallet_row, "onchain_balance_sat", 0)
    lightning = _row_value(wallet_row, "lightning_balance_sat", 0)
    tokens_valuation = sum(t.balance * t.unit_price_sat for t in token_balances)

    return WalletResponse(
        wallet=WalletSummary(
            id=_row_value(wallet_row, "id"),
            onchain_balance_sat=onchain,
            lightning_balance_sat=lightning,
            token_balances=token_balances,
            total_value_sat=onchain + lightning + tokens_valuation,
        )
    )


@app.post("/lightning/invoices", response_model=Invoice, tags=["Lightning"])
async def create_invoice(
    req: InvoiceCreate,
    user_id: str = Depends(get_current_user_id),
    conn: AsyncConnection = Depends(get_db_conn),
):
    try:
        resp = lnd_client.create_invoice(memo=req.memo or "", amount_sats=req.amount_sats)

        wallet = await get_wallet_by_user_id(conn, user_id)
        if wallet:
            await create_transaction(
                conn,
                wallet_id=_row_value(wallet, "id"),
                type="ln_receive",
                direction="in",
                amount_sat=req.amount_sats,
                status="pending",
                ln_payment_hash=resp.r_hash.hex(),
                description=req.memo,
            )

        return Invoice(
            payment_request=resp.payment_request,
            payment_hash=resp.r_hash.hex(),
            r_hash=resp.r_hash.hex(),
            amount_sats=req.amount_sats,
            memo=req.memo,
            status=InvoiceStatus.OPEN,
            created_at=datetime.now(timezone.utc),
        )
    except grpc.RpcError as exc:
        logger.error("gRPC error creating invoice: %s", exc)
        raise HTTPException(status_code=503, detail="Lightning service unavailable") from exc
    except Exception as exc:
        logger.error("Unexpected error creating invoice: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.post("/lightning/payments", response_model=Payment, tags=["Lightning"])
async def pay_invoice(
    req: PaymentCreate,
    user_id: str = Depends(get_current_user_id),
    _: None = Depends(require_2fa),
    conn: AsyncConnection = Depends(get_db_conn),
):
    try:
        wallet = await get_wallet_by_user_id(conn, user_id)
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        resp = lnd_client.pay_invoice(payment_request=req.payment_request)

        payment_status = PaymentStatus.SUCCEEDED
        db_status = "confirmed"
        failure_reason = None
        if resp.payment_error:
            payment_status = PaymentStatus.FAILED
            db_status = "failed"
            failure_reason = resp.payment_error

        amount_sat = resp.payment_route.total_amt if resp.payment_route else 0

        await create_transaction(
            conn,
            wallet_id=_row_value(wallet, "id"),
            type="ln_send",
            direction="out",
            amount_sat=amount_sat,
            status=db_status,
            ln_payment_hash=resp.payment_hash.hex(),
            description=f"Payment to {req.payment_request[:20]}...",
        )

        return Payment(
            payment_hash=resp.payment_hash.hex(),
            payment_preimage=resp.payment_preimage.hex() if not resp.payment_error else None,
            status=payment_status,
            fee_sats=resp.payment_route.total_fees if resp.payment_route else 0,
            failure_reason=failure_reason,
            created_at=datetime.now(timezone.utc),
        )
    except grpc.RpcError as exc:
        logger.error("gRPC error paying invoice: %s", exc)
        raise HTTPException(status_code=503, detail="Lightning service unavailable") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Unexpected error paying invoice: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.get("/lightning/invoices/{r_hash}", response_model=Invoice, tags=["Lightning"])
async def get_invoice(
    r_hash: str,
    user_id: str = Depends(get_current_user_id),
):
    try:
        ln_invoice = lnd_client.lookup_invoice(r_hash_str=r_hash)

        status_map = {
            0: InvoiceStatus.OPEN,
            1: InvoiceStatus.SETTLED,
            2: InvoiceStatus.CANCELED,
            3: InvoiceStatus.ACCEPTED,
        }

        return Invoice(
            payment_request=ln_invoice.payment_request,
            payment_hash=r_hash,
            r_hash=r_hash,
            amount_sats=ln_invoice.value,
            memo=ln_invoice.memo,
            status=status_map.get(ln_invoice.state, InvoiceStatus.OPEN),
            settled_at=datetime.fromtimestamp(ln_invoice.settle_date) if ln_invoice.settle_date else None,
            created_at=datetime.fromtimestamp(ln_invoice.creation_date),
        )
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Invoice not found") from exc
        logger.error("gRPC error looking up invoice: %s", exc)
        raise HTTPException(status_code=503, detail="Lightning service unavailable") from exc
    except Exception as exc:
        logger.error("Unexpected error fetching invoice: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


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
    async with _runtime_engine().connect() as conn:
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

    async with _runtime_engine().connect() as conn:
        user = await get_user_by_id(conn, principal.id)
        if user is None or _row_value(user, "deleted_at") is not None:
            raise _invalid_access_token_error()
        if not _row_value(user, "totp_secret"):
            raise ContractError(
                code="two_factor_not_enabled",
                message="Two-factor authentication must be enabled before withdrawing.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if not _verify_totp_code(_row_value(user, "totp_secret"), two_fa_code):
            raise ContractError(
                code="invalid_2fa_code",
                message="Two-factor authentication code is invalid.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        wallet = await get_or_create_wallet(conn, principal.id)
        fee_sat = _estimate_onchain_fee(body.fee_rate_sat_vb)
        row = await create_onchain_withdrawal(
            conn,
            wallet_id=str(_row_value(wallet, "id")),
            amount_sat=body.amount_sat,
            fee_sat=fee_sat,
            txid=_generate_txid(
                wallet_id=str(_row_value(wallet, "id")),
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
        txid=_row_value(row, "txid"),
        amount_sat=_row_value(row, "amount_sat"),
        fee_sat=fee_sat,
        status=_row_value(row, "status"),
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
    async with _runtime_engine().connect() as conn:
        wallet = await get_or_create_wallet(conn, principal.id)
        rows = await list_wallet_transactions(conn, str(_row_value(wallet, "id")))

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


if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
