from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from io import BytesIO
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
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
import uvicorn

# Add services directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from common import get_readiness_payload, get_settings, install_http_security, record_audit_event
from common import (
    OnRampError,
    accrue_pending_yield_for_user,
    create_onramp_session,
    default_onramp_notices,
    describe_custody_record,
    describe_custody_settings,
    get_user_yield_accruals,
    list_onramp_provider_views,
    summarize_yield_for_user,
)
from common.logging import configure_structured_logging
from common.metrics import metrics, mount_metrics_endpoint, record_business_event
from common.alerting import alert_dispatcher, AlertSeverity, configure_alerting
from auth.kyc_db import get_kyc_status, is_kyc_verified

from wallet_auth import get_current_user_id, require_2fa
from db import (
    create_onchain_withdrawal,
    create_transaction,
    get_db_conn,
    get_engine,
    get_or_create_wallet,
    get_token_balances_for_user,
    get_transaction_by_payment_hash,
    get_user_by_id,
    get_wallet_by_user_id,
    list_wallet_transactions,
    get_next_derivation_index,
    recompute_lightning_balance,
    release_onchain_balance,
    reserve_onchain_balance,
    save_wallet_address,
)
import asyncio
from .reconciliation import reconciliation_loop, lightning_sync_loop, sync_wallet_lightning_state
from .key_manager import KeyManager
from .liquid_rpc import ElementsRPCError, get_liquid_rpc
from .lnd_client import LNDClient
from .schemas import (
    CustodyStatusResponse,
    FiatOnRampProviderStatus,
    FiatOnRampProvidersResponse,
    FiatOnRampSessionRequest,
    FiatOnRampSessionResponse,
    OnchainAddressResponse,
    OnchainWithdrawalRequest,
    OnchainWithdrawalResponse,
    FeeEstimateLevel,
    FeeEstimateResponse,
    PegInAddressResponse,
    PegInClaimRequest,
    PegInClaimResponse,
    PegOutRequest,
    PegOutResponse,
    TransactionHistoryItem,
    TransactionHistoryResponse,
    TransactionType,
)
from schemas_lnd import (
    Invoice,
    InvoiceCreate,
    InvoiceStatus,
    Payment,
    PaymentCreate,
    PaymentStatus,
    Bolt11DecodeRequest,
    Bolt11DecodeResponse,
    RouteHintHop,
    RouteHintOut,
)
from schemas_wallet import (
    TokenBalance,
    WalletResponse,
    WalletSummary,
    YieldAccrualOut,
    YieldSummary,
    YieldSummaryResponse,
    YieldTokenSummary,
)

logger = logging.getLogger(__name__)

os.environ.setdefault("ELEMENTS_RPC_HOST", "localhost")
os.environ.setdefault("ELEMENTS_RPC_PORT", "7041")
os.environ.setdefault("ELEMENTS_RPC_USER", "user")
os.environ.setdefault("ELEMENTS_RPC_PASSWORD", "pass")
os.environ.setdefault("ELEMENTS_NETWORK", "elementsregtest")

settings = get_settings(service_name="wallet", default_port=8001)
configure_structured_logging(service_name=settings.service_name, log_level=settings.log_level)
configure_alerting(settings)
lnd_client = LNDClient(settings)

_ALGORITHM = "HS256"
_DEFAULT_TX_VSIZE = 141
_TOTP_DIGITS = 6
_TOTP_PERIOD_SECONDS = 30
_bearer_scheme = HTTPBearer(auto_error=False)
_engine: AsyncEngine | Any | None = None
_WITHDRAWAL_IDEMPOTENCY_TTL_SECONDS = 600
_withdrawal_idempotency_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_withdrawal_idempotency_inflight: set[tuple[str, str]] = set()


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

    # Start background tasks
    recon_task = asyncio.create_task(reconciliation_loop(engine, settings))
    ln_task = asyncio.create_task(lightning_sync_loop(engine, lnd_client))
    
    yield
    
    recon_task.cancel()
    ln_task.cancel()
    try:
        await asyncio.gather(recon_task, ln_task)
    except asyncio.CancelledError:
        pass
        
    await engine.dispose()


@asynccontextmanager
async def _noop_lifespan(app: FastAPI):
    yield


app = FastAPI(title="Wallet Service", lifespan=_lifespan)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_http_security(
    app,
    settings,
    sensitive_paths=(
        "/lightning/payments",
        "/wallet/onchain/withdraw",
        "/wallet/pegout",
        "/onchain/withdraw",
    ),
)
mount_metrics_endpoint(app, settings)
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


def _withdrawal_cache_key(wallet_id: str, idempotency_key: str) -> tuple[str, str]:
    return wallet_id, idempotency_key.strip()[:128]


def _get_cached_withdrawal_response(wallet_id: str, idempotency_key: str) -> dict[str, Any] | None:
    cache_key = _withdrawal_cache_key(wallet_id, idempotency_key)
    cached = _withdrawal_idempotency_cache.get(cache_key)
    if cached is None:
        return None

    created_at, payload = cached
    if time.time() - created_at > _WITHDRAWAL_IDEMPOTENCY_TTL_SECONDS:
        _withdrawal_idempotency_cache.pop(cache_key, None)
        return None

    return payload


def _mark_withdrawal_inflight(wallet_id: str, idempotency_key: str) -> bool:
    cache_key = _withdrawal_cache_key(wallet_id, idempotency_key)
    if cache_key in _withdrawal_idempotency_inflight:
        return False
    _withdrawal_idempotency_inflight.add(cache_key)
    return True


def _clear_withdrawal_inflight(wallet_id: str, idempotency_key: str) -> None:
    _withdrawal_idempotency_inflight.discard(_withdrawal_cache_key(wallet_id, idempotency_key))


def _store_cached_withdrawal_response(wallet_id: str, idempotency_key: str, payload: dict[str, Any]) -> None:
    _withdrawal_idempotency_cache[_withdrawal_cache_key(wallet_id, idempotency_key)] = (time.time(), payload)


def _infer_bolt11_network(payment_request: str) -> str:
    lowered = payment_request.strip().lower()
    if lowered.startswith("lnbcrt"):
        return "regtest"
    if lowered.startswith("lntb"):
        return "signet" if settings.bitcoin_network == "signet" else "testnet"
    if lowered.startswith("lnbc"):
        return "mainnet"
    return settings.bitcoin_network


def _route_hints_from_pay_req(pay_req: Any) -> list[RouteHintOut]:
    route_hints: list[RouteHintOut] = []
    for route_hint in getattr(pay_req, "route_hints", []):
        hops = [
            RouteHintHop(
                node_id=getattr(hop, "node_id", ""),
                chan_id=str(getattr(hop, "chan_id", "")),
                fee_base_msat=int(getattr(hop, "fee_base_msat", 0)),
                fee_proportional_millionths=int(getattr(hop, "fee_proportional_millionths", 0)),
                cltv_expiry_delta=int(getattr(hop, "cltv_expiry_delta", 0)),
            )
            for hop in getattr(route_hint, "hop_hints", [])
        ]
        route_hints.append(RouteHintOut(hops=hops))
    return route_hints


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _render_qr_png(value: str, *, box_size: int = 8, border: int = 4) -> bytes:
    try:
        import qrcode
    except ImportError as exc:
        raise ContractError(
            code="qr_render_unavailable",
            message="QR rendering is unavailable on this deployment.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(value)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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


def _estimate_onchain_fee(fee_rate_sat_vb: int) -> int:
    return fee_rate_sat_vb * _DEFAULT_TX_VSIZE


async def _create_real_onchain_address(conn: AsyncConnection, wallet: sa.engine.Row):
    try:
        encrypted_seed = bytes(_row_value(wallet, "encrypted_seed", b""))
        key_mgr = KeyManager(
            settings.wallet_encryption_key,
            settings.bitcoin_network,
            elements_network=settings.elements_network,
        )
        seed = key_mgr.decrypt_seed(encrypted_seed)
    except Exception as exc:
        raise ContractError(
            code="wallet_seed_error",
            message="Failed to decrypt wallet seed.",
            status_code=500,
        ) from exc

    idx = await get_next_derivation_index(conn, str(_row_value(wallet, "id")))
    derived_address = key_mgr.derive_liquid_address(seed, idx)

    liquid_rpc = get_liquid_rpc(settings)
    try:
        await liquid_rpc.importaddress(
            derived_address.confidential_address,
            label=f"wallet_{_row_value(wallet, 'id')}",
            rescan=False,
        )
        await liquid_rpc.importblindingkey(
            derived_address.confidential_address,
            derived_address.blinding_private_key,
        )
    except ElementsRPCError as exc:
        logger.error("Failed to import Liquid address %s into Elements: %s", derived_address.confidential_address, exc)
        raise ContractError(
            code="elements_rpc_unavailable",
            message="Elements could not register the deposit address.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc

    await save_wallet_address(
        conn,
        wallet_id=str(_row_value(wallet, "id")),
        address=derived_address.confidential_address,
        derivation_index=idx,
        script_pubkey=derived_address.script_pubkey,
        imported_to_node=True,
    )

    return derived_address

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
        txHash=_row_value(row, "txid"),
        paymentHash=_row_value(row, "ln_payment_hash"),
        fee_sat=_row_value(row, "fee_sat"),
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

        try:
            await sync_wallet_lightning_state(conn, str(_row_value(wallet_row, "id")), lnd_client)
            wallet_row = await get_wallet_by_user_id(conn, user_id) or wallet_row
        except Exception as exc:
            logger.warning("Wallet summary returned cached Lightning balance for user %s: %s", user_id, exc)

        await accrue_pending_yield_for_user(conn, user_id)
        await conn.commit()
        token_rows = await get_token_balances_for_user(conn, user_id)
        total_yield_earned_sat, yield_rows = await summarize_yield_for_user(conn, user_id)

    yield_by_token = {
        str(_row_value(row, "token_id")): int(_row_value(row, "total_yield_sat", 0))
        for row in yield_rows
    }

    token_balances = [
        TokenBalance(
            token_id=row["token_id"],
            liquid_asset_id=row["liquid_asset_id"],
            asset_name=row["asset_name"],
            symbol=None,
            balance=row["balance"],
            unit_price_sat=row["unit_price_sat"],
            accrued_yield_sat=yield_by_token.get(str(row["token_id"]), 0),
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
            total_yield_earned_sat=total_yield_earned_sat,
            total_value_sat=onchain + lightning + tokens_valuation + total_yield_earned_sat,
        )
    )


@app.get(
    "/wallet/yield/summary",
    response_model=YieldSummaryResponse,
    tags=["Wallet"],
)
async def get_wallet_yield_summary(user_id: str = Depends(get_current_user_id)):
    async with _runtime_engine().connect() as conn:
        wallet_row = await get_wallet_by_user_id(conn, user_id)
        if not wallet_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Wallet not found for user",
            )

        await accrue_pending_yield_for_user(conn, user_id)
        await conn.commit()
        total_yield_earned_sat, by_token_rows = await summarize_yield_for_user(conn, user_id)
        accrual_rows = await get_user_yield_accruals(conn, user_id)

    return YieldSummaryResponse(
        yield_summary=YieldSummary(
            total_yield_earned_sat=total_yield_earned_sat,
            by_token=[
                YieldTokenSummary(
                    token_id=_row_value(row, "token_id"),
                    asset_name=_row_value(row, "asset_name"),
                    total_yield_sat=int(_row_value(row, "total_yield_sat", 0)),
                )
                for row in by_token_rows
            ],
            accruals=[
                YieldAccrualOut(
                    id=_row_value(row, "id"),
                    token_id=_row_value(row, "token_id"),
                    asset_name=_row_value(row, "asset_name"),
                    amount_sat=int(_row_value(row, "amount_sat", 0)),
                    quantity_held=int(_row_value(row, "quantity_held", 0)),
                    reference_price_sat=int(_row_value(row, "reference_price_sat", 0)),
                    annual_rate_pct=float(_row_value(row, "annual_rate_pct", 0)),
                    accrued_from=_row_value(row, "accrued_from"),
                    accrued_to=_row_value(row, "accrued_to"),
                    created_at=_row_value(row, "created_at"),
                )
                for row in accrual_rows
            ],
        )
    )


@app.get(
    "/wallet/custody",
    status_code=status.HTTP_200_OK,
    response_model=CustodyStatusResponse,
    summary="Return custody posture for the authenticated wallet",
)
async def get_wallet_custody_status(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        wallet = await get_or_create_wallet(conn, principal.id)

    encrypted_seed = bytes(_row_value(wallet, "encrypted_seed", b""))
    descriptor = describe_custody_record(encrypted_seed)
    custody = describe_custody_settings(settings)
    record_business_event("wallet_custody_status")
    return CustodyStatusResponse(
        configured_backend=custody.backend,
        wallet_backend=descriptor.backend,
        signer_backend=custody.signer_backend,
        state=custody.state,
        key_reference=descriptor.key_reference or custody.key_reference,
        signer_key_reference=custody.signer_key_reference,
        derivation_path=str(_row_value(wallet, "derivation_path", "")),
        seed_exportable=descriptor.exportable_seed,
        withdraw_requires_2fa=True,
        server_compromise_impact=custody.server_compromise_impact,
        disclaimers=list(custody.disclaimers),
    ).model_dump(mode="json")


@app.get(
    "/wallet/fiat/onramp/providers",
    status_code=status.HTTP_200_OK,
    response_model=FiatOnRampProvidersResponse,
    summary="List supported fiat-to-BTC on-ramp providers",
)
async def list_fiat_onramp_providers(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        kyc_row = await get_kyc_status(conn, principal.id)

    providers = list_onramp_provider_views(kyc_verified=is_kyc_verified(kyc_row))
    record_business_event("wallet_fiat_onramp_providers")
    return FiatOnRampProvidersResponse(
        providers=[
            FiatOnRampProviderStatus(
                provider_id=provider.provider_id,
                display_name=provider.display_name,
                state=provider.state,
                supported_fiat_currencies=list(provider.supported_fiat_currencies),
                supported_countries=list(provider.supported_countries),
                payment_methods=list(provider.payment_methods),
                min_fiat_amount=provider.min_fiat_amount,
                max_fiat_amount=provider.max_fiat_amount,
                requires_kyc=provider.requires_kyc,
                disclaimer=provider.disclaimer,
                external_handoff_url=provider.external_handoff_url,
            )
            for provider in providers
        ],
        compliance_notices=default_onramp_notices(),
    ).model_dump(mode="json")


@app.post(
    "/wallet/fiat/onramp/session",
    status_code=status.HTTP_201_CREATED,
    response_model=FiatOnRampSessionResponse,
    summary="Initiate an external fiat-to-BTC on-ramp handoff",
)
async def create_fiat_onramp_session(
    request: Request,
    body: FiatOnRampSessionRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        user = await get_user_by_id(conn, principal.id)
        if user is None or _row_value(user, "deleted_at") is not None:
            raise _invalid_access_token_error()

        wallet = await get_or_create_wallet(conn, principal.id)
        kyc_row = await get_kyc_status(conn, principal.id)

        try:
            deposit_address = await _create_real_onchain_address(conn, wallet)
            session = create_onramp_session(
                provider_id=body.provider_id,
                user_id=principal.id,
                wallet_id=str(_row_value(wallet, "id")),
                deposit_address=deposit_address.confidential_address,
                fiat_currency=body.fiat_currency,
                fiat_amount=body.fiat_amount,
                country_code=body.country_code,
                return_url=body.return_url,
                cancel_url=body.cancel_url,
                kyc_verified=is_kyc_verified(kyc_row),
                signing_secret=settings.jwt_secret,
            )
        except OnRampError as exc:
            raise ContractError(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
            ) from exc

        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="wallet.fiat_onramp_session",
            actor_id=principal.id,
            actor_role=_row_value(user, "role"),
            target_type="wallet",
            target_id=_row_value(wallet, "id"),
            metadata={
                "provider_id": body.provider_id,
                "fiat_currency": body.fiat_currency,
                "fiat_amount": str(body.fiat_amount),
                "country_code": body.country_code,
                "deposit_address_tail": session.deposit_address[-8:],
            },
        )

    record_business_event("wallet_fiat_onramp_session")
    return FiatOnRampSessionResponse(
        session_id=session.session_id,
        provider_id=session.provider_id,
        state=session.state,
        handoff_url=session.handoff_url,
        deposit_address=session.deposit_address,
        destination_wallet_id=session.destination_wallet_id,
        expires_at=session.expires_at,
        disclaimer=session.disclaimer,
        compliance_action=session.compliance_action,
    ).model_dump(mode="json")


@app.post("/lightning/invoices", response_model=Invoice, tags=["Lightning"])
async def create_invoice(
    req: InvoiceCreate,
    user_id: str = Depends(get_current_user_id),
    conn: AsyncConnection = Depends(get_db_conn),
):
    try:
        resp = lnd_client.create_invoice(memo=req.memo or "", amount_sats=req.amount_sats)

        wallet = await get_wallet_by_user_id(conn, user_id)
        if wallet is None:
            wallet = await get_or_create_wallet(conn, user_id)

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

        record_business_event("wallet_invoice_create")
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
        record_business_event("wallet_invoice_create", outcome="failure")
        raise HTTPException(status_code=503, detail="Lightning service unavailable") from exc
    except Exception as exc:
        logger.error("Unexpected error creating invoice: %s", exc)
        record_business_event("wallet_invoice_create", outcome="failure")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.post("/lightning/payments", response_model=Payment, tags=["Lightning"])
async def pay_invoice(
    request: Request,
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
            record_business_event("wallet_payment", outcome="failure")
            await alert_dispatcher.fire(
                severity=AlertSeverity.CRITICAL,
                title="Lightning payment failed",
                detail=resp.payment_error,
                source=settings.service_name,
                tags={"user_id": user_id},
            )

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
            fee_sat=resp.payment_route.total_fees if resp.payment_route else 0,
        )
        await sync_wallet_lightning_state(conn, str(_row_value(wallet, "id")), lnd_client)
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="wallet.lightning.pay",
            actor_id=user_id,
            target_type="wallet",
            target_id=_row_value(wallet, "id"),
            metadata={
                "amount_sat": amount_sat,
                "status": db_status,
                "payment_hash": resp.payment_hash.hex(),
            },
        )

        if not resp.payment_error:
            record_business_event("wallet_payment")
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
        record_business_event("wallet_payment", outcome="failure")
        raise HTTPException(status_code=503, detail="Lightning service unavailable") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Unexpected error paying invoice: %s", exc)
        record_business_event("wallet_payment", outcome="failure")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.get("/lightning/invoices/{r_hash}", response_model=Invoice, tags=["Lightning"])
async def get_invoice(
    r_hash: str,
    user_id: str = Depends(get_current_user_id),
):
    try:
        async with _runtime_engine().connect() as conn:
            wallet = await get_wallet_by_user_id(conn, user_id)
            if wallet is None:
                raise HTTPException(status_code=404, detail="Invoice not found")

            invoice_row = await get_transaction_by_payment_hash(
                conn,
                wallet_id=_row_value(wallet, "id"),
                payment_hash=r_hash,
                tx_type="ln_receive",
            )
            if invoice_row is None:
                raise HTTPException(status_code=404, detail="Invoice not found")

        ln_invoice = lnd_client.lookup_invoice(r_hash_str=r_hash)

        async with _runtime_engine().connect() as conn:
            try:
                await sync_wallet_lightning_state(conn, str(_row_value(wallet, "id")), lnd_client)
            except Exception as sync_exc:
                logger.warning("Invoice lookup returned live state but could not refresh wallet Lightning cache: %s", sync_exc)

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


@app.post("/lightning/decode", response_model=Bolt11DecodeResponse, tags=["Lightning"])
async def decode_bolt11(
    body: Bolt11DecodeRequest,
    user_id: str = Depends(get_current_user_id),
):
    try:
        req = lnd_client.decode_pay_req(payment_request=body.payment_request)

        created_at = datetime.fromtimestamp(req.timestamp, tz=timezone.utc)
        expires_at = created_at + timedelta(seconds=req.expiry)
        is_expired = expires_at < datetime.now(timezone.utc)

        record_business_event("wallet_bolt11_decode")
        return Bolt11DecodeResponse(
            payment_hash=req.payment_hash,
            amount_sat=req.num_satoshis or None,
            amount_msat=req.num_msat or None,
            description=_optional_str(req.description),
            description_hash=_optional_str(req.description_hash),
            timestamp=created_at,
            created_at=created_at,
            expiry=req.expiry,
            expires_at=expires_at,
            destination=_optional_str(req.destination),
            fallback_address=_optional_str(req.fallback_addr),
            network=_infer_bolt11_network(body.payment_request),
            route_hints=_route_hints_from_pay_req(req),
            is_expired=is_expired,
        )
    except grpc.RpcError as exc:
        logger.error("gRPC error decoding invoice: %s", exc)
        raise ContractError(
            code="invalid_bolt11",
            message="Invalid or unsupported Lightning invoice.",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc
    except Exception as exc:
        logger.error("Unexpected error decoding invoice: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.get(
    "/wallet/onchain/fees",
    status_code=status.HTTP_200_OK,
    response_model=FeeEstimateResponse,
    summary="Get current on-chain fee estimates",
)
@app.get(
    "/onchain/fees",
    status_code=status.HTTP_200_OK,
    response_model=FeeEstimateResponse,
    include_in_schema=False,
)
async def get_onchain_fees(
    _principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    liquid_rpc = get_liquid_rpc(settings)

    async def _estimate(blocks: int, default_sat_vb: int) -> FeeEstimateLevel:
        try:
            res = await liquid_rpc.estimatesmartfee(blocks)
            btc_kvb = res.get("feerate", -1)
            if btc_kvb > 0:
                sat_vb = int((btc_kvb * 100_000_000) / 1000)
                return FeeEstimateLevel(
                    sat_per_vb=max(1, sat_vb),
                    target_blocks=blocks,
                    source="elements_rpc",
                )
        except Exception as exc:
            logger.warning("Fee estimation failed for %s blocks: %s", blocks, exc)
        return FeeEstimateLevel(
            sat_per_vb=default_sat_vb,
            target_blocks=blocks,
            source="fallback",
        )

    # low = 12 blocks (~2 hrs), medium = 6 blocks (~1 hr), high = 2 blocks (~20 mins)
    low_level, medium_level, high_level = await asyncio.gather(
        _estimate(12, 1),
        _estimate(6, 5),
        _estimate(2, 10)
    )

    record_business_event(
        "wallet_fee_estimate",
        labels={
            "used_fallback": any(level.source == "fallback" for level in (low_level, medium_level, high_level))
        },
    )
    return FeeEstimateResponse(
        low=low_level,
        medium=medium_level,
        high=high_level,
    )


@app.get(
    "/wallet/qr",
    status_code=status.HTTP_200_OK,
    summary="Render a PNG QR code for a wallet string payload",
)
@app.get(
    "/qr",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def render_qr_code(
    value: str = Query(min_length=1, max_length=4096),
    box_size: int = Query(default=8, ge=1, le=20),
    border: int = Query(default=4, ge=0, le=20),
    _principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    payload = _render_qr_png(value, box_size=box_size, border=border)
    record_business_event("wallet_qr_render")
    return Response(content=payload, media_type="image/png")

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
        wallet = await get_or_create_wallet(conn, principal.id)
        address_bundle = await _create_real_onchain_address(conn, wallet)

    record_business_event("wallet_onchain_address_create")
    return OnchainAddressResponse(
        address=address_bundle.confidential_address,
        unconfidential_address=address_bundle.unconfidential_address,
        type="liquid_confidential",
    ).model_dump()


@app.get(
    "/wallet/pegin/address",
    status_code=status.HTTP_200_OK,
    response_model=PegInAddressResponse,
    summary="Create a mainchain peg-in deposit address",
)
async def get_pegin_address(
    _principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    liquid_rpc = get_liquid_rpc(settings)
    try:
        payload = await liquid_rpc.getpeginaddress()
    except ElementsRPCError as exc:
        raise ContractError(
            code="elements_rpc_unavailable",
            message="Elements could not create a peg-in address.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc

    record_business_event("wallet_pegin_address_create")
    return PegInAddressResponse(
        mainchain_address=str(payload.get("mainchain_address") or payload.get("mainchainaddress") or ""),
        claim_script=str(payload.get("claim_script") or payload.get("claimscript") or ""),
    ).model_dump()


@app.post(
    "/wallet/pegin/claim",
    status_code=status.HTTP_200_OK,
    response_model=PegInClaimResponse,
    summary="Claim a confirmed mainchain peg-in on Liquid",
)
async def claim_pegin(
    request: Request,
    body: PegInClaimRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    liquid_rpc = get_liquid_rpc(settings)
    try:
        result = await liquid_rpc.claimpegin(
            body.raw_transaction,
            body.txout_proof,
            body.claim_script,
        )
    except ElementsRPCError as exc:
        raise ContractError(
            code="pegin_claim_failed",
            message="Elements rejected the peg-in claim.",
            status_code=status.HTTP_502_BAD_GATEWAY,
        ) from exc

    txid = str(result.get("txid") or result.get("hex") or "")
    async with _runtime_engine().connect() as conn:
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="wallet.pegin.claim",
            actor_id=principal.id,
            target_type="transaction",
            target_id=txid or None,
            metadata={"claim_script": body.claim_script[-16:]},
        )

    record_business_event("wallet_pegin_claim")
    return PegInClaimResponse(txid=txid, status="pending").model_dump()


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
    request: Request,
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
        wallet_id = str(_row_value(wallet, "id"))
        idempotency_key = (request.headers.get("X-Idempotency-Key") or "").strip()
        if idempotency_key:
            cached = _get_cached_withdrawal_response(wallet_id, idempotency_key)
            if cached is not None:
                return cached
            if not _mark_withdrawal_inflight(wallet_id, idempotency_key):
                raise ContractError(
                    code="duplicate_request_in_flight",
                    message="A withdrawal with this idempotency key is already being processed.",
                    status_code=status.HTTP_409_CONFLICT,
                )

        fee_rate_btc_kvb = body.fee_rate_sat_vb / 100_000.0
        outputs = [{body.address: body.amount_sat / 100_000_000.0}]
        change_address = await _create_real_onchain_address(conn, wallet)
        options = {
            "feeRate": fee_rate_btc_kvb,
            "includeWatching": True,
            "changeAddress": change_address.confidential_address,
        }

        liquid_rpc = get_liquid_rpc(settings)
        try:
            funded = await liquid_rpc.walletcreatefundedpsbt([], outputs, options)
            pset_str = funded["psbt"]
            fee_sat = int(funded["fee"] * 100_000_000)
        except Exception as exc:
            if idempotency_key:
                _clear_withdrawal_inflight(wallet_id, idempotency_key)
            logger.error("Failed to fund Liquid PSET: %s", exc)
            raise ContractError(
                code="insufficient_funds",
                message="Wallet balance is insufficient for this withdrawal and fee or no UTXOs available.",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc

        total_cost_sat = body.amount_sat + fee_sat
        reserved = await reserve_onchain_balance(
            conn,
            wallet_id=wallet_id,
            total_cost_sat=total_cost_sat,
        )
        if not reserved:
            if idempotency_key:
                _clear_withdrawal_inflight(wallet_id, idempotency_key)
            raise ContractError(
                code="insufficient_funds",
                message="Wallet balance is insufficient for this withdrawal and fee.",
                status_code=status.HTTP_409_CONFLICT,
            )

        try:
            encrypted_seed = bytes(_row_value(wallet, "encrypted_seed", b""))
            key_mgr = KeyManager(
                settings.wallet_encryption_key,
                settings.bitcoin_network,
                elements_network=settings.elements_network,
            )
            seed = key_mgr.decrypt_seed(encrypted_seed)

            from common.db.metadata import wallet_addresses as wa_table
            result = await conn.execute(
                sa.select(wa_table.c.script_pubkey, wa_table.c.derivation_index)
                .where(wa_table.c.wallet_id == _row_value(wallet, "id"))
            )
            address_map = {row.script_pubkey: row.derivation_index for row in result}

            from embit.liquid.finalizer import finalize_psbt
            from embit.liquid.networks import NETWORKS as LIQUID_NETWORKS
            from embit.liquid.pset import PSET
            from embit import bip32

            pset = PSET.from_string(pset_str)
            network = LIQUID_NETWORKS[settings.elements_network]
            root = bip32.HDKey.from_seed(seed, version=network["xprv"])
            coin_type = 1776 if settings.elements_network == "liquidv1" else 1
            signed_inputs = 0

            for inp in pset.inputs:
                utxo = inp.utxo
                if utxo is None:
                    raise ValueError("PSET missing utxo")
                spk_hex = utxo.script_pubkey.data.hex()
                if spk_hex not in address_map:
                    raise ValueError(f"Unknown Liquid input script pubkey: {spk_hex}")

                idx = address_map[spk_hex]
                path = f"m/44'/{coin_type}'/0'/0/{idx}"
                signed_inputs += pset.sign_with(root.derive(path).key)

            if signed_inputs <= 0:
                raise ValueError("Failed to sign any Liquid inputs")

            finalized = finalize_psbt(pset)
            if finalized is None:
                raise ValueError("Failed to finalize Liquid PSET")

            txid = await liquid_rpc.sendrawtransaction(finalized.serialize().hex())

        except Exception as exc:
            await release_onchain_balance(
                conn,
                wallet_id=wallet_id,
                total_cost_sat=total_cost_sat,
            )
            if idempotency_key:
                _clear_withdrawal_inflight(wallet_id, idempotency_key)
            logger.error("Failed to sign/broadcast Liquid PSET: %s", exc)
            raise ContractError(
                code="transaction_failed",
                message=f"Failed to sign and broadcast transaction: {exc}",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            ) from exc

        try:
            row = await create_onchain_withdrawal(
                conn,
                wallet_id=wallet_id,
                amount_sat=body.amount_sat,
                fee_sat=fee_sat,
                txid=txid,
                description=f"Liquid withdrawal to {body.address}",
            )
            await record_audit_event(
                conn,
                settings=settings,
                request=request,
                action="wallet.onchain_withdraw",
                actor_id=principal.id,
                actor_role=_row_value(user, "role"),
                target_type="transaction",
                target_id=_row_value(row, "id"),
                metadata={
                    "amount_sat": body.amount_sat,
                    "fee_sat": fee_sat,
                    "address_tail": body.address[-8:],
                },
            )
        except Exception:
            if idempotency_key:
                _clear_withdrawal_inflight(wallet_id, idempotency_key)
            raise

    record_business_event("wallet_onchain_withdrawal")
    response_payload = OnchainWithdrawalResponse(
        txid=txid,
        amount_sat=body.amount_sat,
        fee_sat=fee_sat,
        status=_row_value(row, "status"),
    ).model_dump()
    if idempotency_key:
        _store_cached_withdrawal_response(wallet_id, idempotency_key, response_payload)
        _clear_withdrawal_inflight(wallet_id, idempotency_key)
    return response_payload


@app.post(
    "/wallet/pegout",
    status_code=status.HTTP_200_OK,
    response_model=PegOutResponse,
    summary="Submit a Liquid peg-out to a mainchain Bitcoin address",
)
async def pegout_to_mainchain(
    request: Request,
    body: PegOutRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    two_fa_code: str | None = Header(default=None, alias="X-2FA-Code"),
):
    if not two_fa_code:
        raise ContractError(
            code="two_factor_required",
            message="X-2FA-Code header is required for peg-outs.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    async with _runtime_engine().connect() as conn:
        user = await get_user_by_id(conn, principal.id)
        if user is None or _row_value(user, "deleted_at") is not None:
            raise _invalid_access_token_error()
        if not _row_value(user, "totp_secret"):
            raise ContractError(
                code="two_factor_not_enabled",
                message="Two-factor authentication must be enabled before peg-outs.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if not _verify_totp_code(_row_value(user, "totp_secret"), two_fa_code):
            raise ContractError(
                code="invalid_2fa_code",
                message="Two-factor authentication code is invalid.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        wallet = await get_or_create_wallet(conn, principal.id)
        wallet_id = str(_row_value(wallet, "id"))
        reserved = await reserve_onchain_balance(
            conn,
            wallet_id=wallet_id,
            total_cost_sat=body.amount_sat,
        )
        if not reserved:
            raise ContractError(
                code="insufficient_funds",
                message="Wallet balance is insufficient for this peg-out.",
                status_code=status.HTTP_409_CONFLICT,
            )

        liquid_rpc = get_liquid_rpc(settings)
        try:
            txid = await liquid_rpc.sendtomainchain(body.mainchain_address, body.amount_sat / 100_000_000.0)
        except Exception as exc:
            await release_onchain_balance(conn, wallet_id=wallet_id, total_cost_sat=body.amount_sat)
            raise ContractError(
                code="pegout_failed",
                message="Elements rejected the peg-out request.",
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc

        row = await create_onchain_withdrawal(
            conn,
            wallet_id=wallet_id,
            amount_sat=body.amount_sat,
            fee_sat=0,
            txid=txid,
            description=f"Liquid peg-out to {body.mainchain_address}",
        )
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="wallet.pegout",
            actor_id=principal.id,
            actor_role=_row_value(user, "role"),
            target_type="transaction",
            target_id=_row_value(row, "id"),
            metadata={
                "amount_sat": body.amount_sat,
                "mainchain_address_tail": body.mainchain_address[-8:],
            },
        )

    record_business_event("wallet_pegout")
    return PegOutResponse(txid=txid, amount_sat=body.amount_sat, status="pending").model_dump()


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
