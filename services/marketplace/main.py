from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import hashlib
import logging
from pathlib import Path
import sys
import time
from typing import Any
import uuid

from embit import ec
from embit.liquid.pset import PSET
from fastapi import Depends, FastAPI, Query, Request, Security, WebSocket, WebSocketDisconnect, status
from fastapi import Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth.jwt_utils import decode_token
from common import (
    InternalEventBus,
    RedisStreamFeed,
    RedisStreamMirror,
    build_platform_signer,
    decode_resume_token,
    encode_resume_token,
    get_readiness_payload,
    get_settings,
    install_http_security,
    record_audit_event,
)
from common.db.metadata import wallet_addresses as wallet_addresses_table
from common.logging import configure_structured_logging
from common.metrics import metrics, mount_metrics_endpoint, record_business_event
from common.alerting import alert_dispatcher, AlertSeverity, configure_alerting
from marketplace.escrow import derive_private_key
from marketplace.db import (
    activate_triggered_orders,
    cancel_order,
    create_order,
    create_trade_escrow,
    expire_unfunded_escrow,
    find_best_match,
    get_reference_price_for_token,
    get_dispute_by_trade_id,
    get_escrow_by_trade_id,
    get_last_trade_price_for_token,
    get_order_by_id,
    get_reserved_buy_commitment,
    get_reserved_sell_quantity,
    get_token_balance_for_user,
    get_token_by_id,
    get_trade_by_id,
    get_trade_volume_24h,
    get_user_by_id,
    get_wallet_by_user_id,
    list_escrows_by_status,
    list_orders,
    list_trades,
    mark_escrow_funded,
    open_dispute,
    process_escrow_signature,
    record_escrow_signature,
    resolve_escrow_signing_material,
    resolve_dispute,
    update_escrow_settlement_metadata,
)
from marketplace.liquid_rpc import ElementsRPCError, FundingObservation, get_liquid_rpc
from auth.kyc_db import get_kyc_status, is_kyc_verified
from marketplace.schemas import (
    CancelOrderResponse,
    CancelledOrderOut,
    DisputeOpenRequest,
    DisputeOut,
    DisputeResolveRequest,
    DisputeResponse,
    EscrowOut,
    EscrowResponse,
    EscrowSignRequest,
    OrderBookLevel,
    OrderBookResponse,
    OrderCreateRequest,
    OrderListResponse,
    OrderOut,
    OrderResponse,
    TradeListResponse,
    TradeOut,
)
from wallet.key_manager import KeyManager


settings = get_settings(service_name="marketplace", default_port=8003)
_bearer_scheme = HTTPBearer(auto_error=False)
_engine: AsyncEngine | object | None = None
configure_structured_logging(service_name=settings.service_name, log_level=settings.log_level)
logger = logging.getLogger(__name__)
_event_bus = InternalEventBus()
_event_bus.subscribe("trade.matched", RedisStreamMirror(settings.redis_url))
_event_bus.subscribe("escrow.funded", RedisStreamMirror(settings.redis_url))
_realtime_feed = RedisStreamFeed(settings.redis_url)
_event_bus.subscribe("escrow.expired", RedisStreamMirror(settings.redis_url))
_event_bus.subscribe("escrow.released", RedisStreamMirror(settings.redis_url))
configure_alerting(settings)
_liquid_rpc_client = (
    get_liquid_rpc(settings)
    if settings.elements_rpc_password
    else None
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
    role: str


def _make_async_url(sync_url: str) -> str:
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


def _runtime_engine() -> AsyncEngine | object:
    global _engine
    if _engine is None:
        _engine = create_async_engine(_make_async_url(settings.database_url), pool_pre_ping=True)
    return _engine


@asynccontextmanager
async def _lifespan(app: FastAPI):
    engine = _runtime_engine()
    stop_event = asyncio.Event()
    watcher_task: asyncio.Task[None] | None = None
    if _liquid_rpc_client is not None:
        watcher_task = asyncio.create_task(_escrow_watcher_loop(stop_event))
    yield
    stop_event.set()
    if watcher_task is not None:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _jwt_secret() -> str:
    return settings.jwt_secret or "dev-secret-change-me"


def _normalize_uuid_claim(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


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
        return row[key]
    except (KeyError, TypeError, IndexError):
        return default


def _order_out(row: object) -> OrderOut:
    triggered_at = _row_value(row, "triggered_at")
    order_type = _row_value(row, "order_type", "limit")
    return OrderOut(
        id=_row_value(row, "id"),
        token_id=_row_value(row, "token_id"),
        side=_row_value(row, "side"),
        order_type=order_type,
        quantity=int(_row_value(row, "quantity", 0)),
        price_sat=int(_row_value(row, "price_sat", 0)),
        trigger_price_sat=_row_value(row, "trigger_price_sat"),
        triggered_at=triggered_at,
        is_triggered=order_type == "limit" or triggered_at is not None,
        filled_quantity=int(_row_value(row, "filled_quantity", 0)),
        status=_row_value(row, "status"),
        created_at=_row_value(row, "created_at"),
    )


def _trade_out(row: object) -> TradeOut:
    return TradeOut(
        id=_row_value(row, "id"),
        token_id=_row_value(row, "token_id"),
        quantity=int(_row_value(row, "quantity", 0)),
        price_sat=int(_row_value(row, "price_sat", 0)),
        total_sat=int(_row_value(row, "total_sat", 0)),
        fee_sat=int(_row_value(row, "fee_sat", 0)),
        status=_row_value(row, "status"),
        created_at=_row_value(row, "created_at"),
        settled_at=_row_value(row, "settled_at"),
    )


def _escrow_out(row: object) -> EscrowOut:
    settlement_metadata = dict(_row_value(row, "settlement_metadata") or {})
    settlement_metadata.pop("blinding_private_key", None)
    return EscrowOut(
        id=_row_value(row, "id"),
        trade_id=_row_value(row, "trade_id"),
        multisig_address=_row_value(row, "multisig_address"),
        locked_amount_sat=int(_row_value(row, "locked_amount_sat", 0)),
        funding_txid=_row_value(row, "funding_txid"),
        release_txid=_row_value(row, "release_txid"),
        refund_txid=_row_value(row, "refund_txid"),
        status=_row_value(row, "status"),
        expires_at=_row_value(row, "expires_at"),
        settlement_metadata=settlement_metadata or None,
    )


def _dispute_out(row: object) -> DisputeOut:
    return DisputeOut(
        id=_row_value(row, "id"),
        trade_id=_row_value(row, "trade_id"),
        opened_by=_row_value(row, "opened_by"),
        reason=_row_value(row, "reason"),
        status=_row_value(row, "status"),
        resolution=_row_value(row, "resolution"),
        resolved_by=_row_value(row, "resolved_by"),
        resolved_at=_row_value(row, "resolved_at"),
        created_at=_row_value(row, "created_at"),
    )


def _remaining_quantity(row: object) -> int:
    return int(_row_value(row, "quantity", 0)) - int(_row_value(row, "filled_quantity", 0))


def _wallet_total_balance(row: object) -> int:
    return int(_row_value(row, "onchain_balance_sat", 0)) + int(_row_value(row, "lightning_balance_sat", 0))


def _stop_order_triggered(*, side: str, trigger_price_sat: int, reference_price: int | None) -> bool:
    if reference_price is None:
        return False
    if side == "buy":
        return reference_price >= trigger_price_sat
    return reference_price <= trigger_price_sat


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _system_request(path: str, *, method: str = "POST") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("marketplace", settings.service_port),
        }
    )


def _sats_to_btc(value_sat: int) -> float:
    return value_sat / 100_000_000.0


def _signature_bucket(collected_signatures: dict | None, *, path: str) -> dict[str, dict[str, Any]]:
    return dict((collected_signatures or {}).get(path) or {})


def _signature_record(
    *,
    signer_role: str,
    actor_id: str | None,
    signature_fingerprint: str,
    source: str,
) -> dict[str, Any]:
    return {
        "role": signer_role,
        "actor_id": actor_id,
        "signature": signature_fingerprint,
        "source": source,
        "signed_at": _utc_now_iso(),
    }


def _build_page(rows: list[object], *, cursor: str | None, limit: int, label: str) -> tuple[list[object], str | None]:
    start_index = 0

    if cursor is not None:
        try:
            cursor_uuid = str(uuid.UUID(cursor))
        except ValueError as exc:
            raise ContractError(
                code="invalid_cursor",
                message=f"Cursor must be a valid {label} UUID.",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            ) from exc

        for index, row in enumerate(rows):
            if str(_row_value(row, "id")) == cursor_uuid:
                start_index = index + 1
                break
        else:
            raise ContractError(
                code="invalid_cursor",
                message=f"Cursor does not match a {label} in this result set.",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

    page = rows[start_index : start_index + limit]
    has_more = start_index + len(page) < len(rows)
    next_cursor = str(_row_value(page[-1], "id")) if page and has_more else None
    return page, next_cursor


async def _publish_trade_matched(
    trade_row: object,
    *,
    escrow_row: object,
    buy_order: object,
    sell_order: object,
) -> None:
    payload = {
        "event": "trade_matched",
        "trade_id": str(_row_value(trade_row, "id")),
        "token_id": str(_row_value(trade_row, "token_id")),
        "buy_order_id": str(_row_value(trade_row, "buy_order_id")),
        "sell_order_id": str(_row_value(trade_row, "sell_order_id")),
        "buyer_id": str(_row_value(buy_order, "user_id")),
        "seller_id": str(_row_value(sell_order, "user_id")),
        "quantity": int(_row_value(trade_row, "quantity", 0)),
        "price_sat": int(_row_value(trade_row, "price_sat", 0)),
        "total_sat": int(_row_value(trade_row, "total_sat", 0)),
        "fee_sat": int(_row_value(trade_row, "fee_sat", 0)),
        "status": _row_value(trade_row, "status"),
        "settled_at": _isoformat(_row_value(trade_row, "settled_at")),
        "escrow_id": str(_row_value(escrow_row, "id")),
        "multisig_address": _row_value(escrow_row, "multisig_address"),
        "escrow_status": _row_value(escrow_row, "status"),
        "escrow_expires_at": _isoformat(_row_value(escrow_row, "expires_at")),
    }
    record_business_event("trade_match")
    await _event_bus.publish("trade.matched", payload)


async def _publish_escrow_funded(
    trade_row: object,
    *,
    escrow_row: object,
    buy_order: object,
    sell_order: object,
) -> None:
    payload = {
        "event": "escrow_funded",
        "trade_id": str(_row_value(trade_row, "id")),
        "token_id": str(_row_value(trade_row, "token_id")),
        "escrow_id": str(_row_value(escrow_row, "id")),
        "buyer_id": str(_row_value(buy_order, "user_id")),
        "seller_id": str(_row_value(sell_order, "user_id")),
        "multisig_address": _row_value(escrow_row, "multisig_address"),
        "locked_amount_sat": int(_row_value(escrow_row, "locked_amount_sat", 0)),
        "funding_txid": _row_value(escrow_row, "funding_txid"),
        "status": _row_value(escrow_row, "status"),
    }
    record_business_event("escrow_fund")
    await _event_bus.publish("escrow.funded", payload)


async def _publish_escrow_expired(
    trade_row: object,
    *,
    escrow_row: object,
    buy_order: object,
    sell_order: object,
) -> None:
    payload = {
        "event": "escrow_expired",
        "trade_id": str(_row_value(trade_row, "id")),
        "escrow_id": str(_row_value(escrow_row, "id")),
        "buyer_id": str(_row_value(buy_order, "user_id")),
        "seller_id": str(_row_value(sell_order, "user_id")),
        "status": _row_value(escrow_row, "status"),
    }
    record_business_event("escrow_expire")
    await _event_bus.publish("escrow.expired", payload)


def _token_not_found_error() -> ContractError:
    return ContractError(
        code="token_not_found",
        message="Token not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _wallet_not_found_error() -> ContractError:
    return ContractError(
        code="wallet_not_found",
        message="Wallet not found for user.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _insufficient_sats_error() -> ContractError:
    return ContractError(
        code="insufficient_sats",
        message="Insufficient wallet balance for this buy order.",
        status_code=status.HTTP_409_CONFLICT,
    )


def _insufficient_token_balance_error() -> ContractError:
    return ContractError(
        code="insufficient_token_balance",
        message="Insufficient token balance for this sell order.",
        status_code=status.HTTP_409_CONFLICT,
    )


def _invalid_access_token_error() -> ContractError:
    return ContractError(
        code="invalid_token",
        message="Access token is invalid or expired.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def _invalid_resume_token_error() -> ContractError:
    return ContractError(
        code="invalid_resume_token",
        message="Resume token is invalid.",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _trade_not_found_error() -> ContractError:
    return ContractError(
        code="trade_not_found",
        message="Trade not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _escrow_not_found_error() -> ContractError:
    return ContractError(
        code="escrow_not_found",
        message="Escrow not found for this trade.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


async def _enforce_kyc_threshold(
    conn: object,
    user_id: str,
    total_value_sat: int,
) -> None:
    """Block the order if the trade value exceeds the KYC threshold and
    the user has not completed identity verification.

    Does nothing when ``kyc_trade_threshold_sat`` is 0 (disabled).
    """
    threshold = settings.kyc_trade_threshold_sat
    if threshold <= 0 or total_value_sat < threshold:
        return

    kyc_row = await get_kyc_status(conn, user_id)
    if is_kyc_verified(kyc_row):
        return

    if kyc_row is None:
        raise ContractError(
            code="kyc_required",
            message=(
                f"Identity verification is required for trades valued at or above "
                f"{threshold:,} sats. Please submit a KYC request at /auth/kyc/submit."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    kyc_status_value = getattr(kyc_row, "status", None)
    mapping = getattr(kyc_row, "_mapping", None)
    if mapping is not None:
        kyc_status_value = mapping.get("status", kyc_status_value)

    if kyc_status_value == "pending":
        raise ContractError(
            code="kyc_pending",
            message=(
                "Your identity verification is still pending review. "
                "High-value trades are blocked until verification is approved."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if kyc_status_value == "rejected":
        raise ContractError(
            code="kyc_rejected",
            message=(
                "Your identity verification was rejected. "
                "Please contact support or resubmit your KYC documents."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # expired or any other non-verified status
    raise ContractError(
        code="kyc_not_verified",
        message=(
            "Your identity verification has expired or is incomplete. "
            "Please resubmit your KYC documents before placing high-value trades."
        ),
        status_code=status.HTTP_403_FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# 2FA helpers (TOTP) used by the sign endpoint
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
    except (ValueError, Exception):
        return False


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


def _derive_platform_release_signature(escrow_id: uuid.UUID, trade_id: uuid.UUID) -> str:
    """Derive a deterministic platform counter-signature for escrow release."""
    msg = f"escrow-release:{escrow_id}:{trade_id}".encode()
    signer = build_platform_signer(settings)
    return signer.sign(purpose="escrow-release", message=msg)


async def _publish_escrow_released(
    trade_row: object,
    *,
    escrow_row: object,
    buy_order: object,
    sell_order: object,
) -> None:
    payload = {
        "event": "escrow_released",
        "trade_id": str(_row_value(trade_row, "id")),
        "escrow_id": str(_row_value(escrow_row, "id")),
        "buyer_id": str(_row_value(buy_order, "user_id")),
        "seller_id": str(_row_value(sell_order, "user_id")),
        "release_txid": _row_value(escrow_row, "release_txid"),
        "status": _row_value(escrow_row, "status"),
        "trade_status": _row_value(trade_row, "status"),
        "settled_at": _isoformat(_row_value(trade_row, "settled_at")),
    }
    record_business_event("escrow_release")
    await _event_bus.publish("escrow.released", payload)


async def _record_settlement_failure(
    *,
    stage: str,
    detail: str,
    trade_id: str | None = None,
    escrow_id: str | None = None,
) -> None:
    labels = {"stage": stage}
    if trade_id:
        labels["trade_id"] = trade_id
    if escrow_id:
        labels["escrow_id"] = escrow_id

    record_business_event("settlement_failure", outcome="failure", labels=labels)
    metrics.inc("marketplace_settlement_failures_total", labels={"stage": stage})
    await alert_dispatcher.fire(
        severity=AlertSeverity.CRITICAL,
        title="Marketplace settlement failure",
        detail=detail,
        source=settings.service_name,
        tags=labels,
    )


async def _scan_escrow_funding(escrow_row: object) -> FundingObservation | None:
    if _liquid_rpc_client is None:
        return None

    try:
        settlement_metadata = _row_value(escrow_row, "settlement_metadata") or {}
        address = str(
            settlement_metadata.get("unconfidential_address")
            or _row_value(escrow_row, "multisig_address", "")
        )
        return await _liquid_rpc_client.scan_address(address)
    except ElementsRPCError:
        logger.exception("Escrow funding check failed for trade %s", _row_value(escrow_row, "trade_id"))
        await _record_settlement_failure(
            stage="escrow_funding_scan",
            detail=f"Escrow funding scan failed for trade {_row_value(escrow_row, 'trade_id')}.",
            trade_id=str(_row_value(escrow_row, "trade_id")),
            escrow_id=str(_row_value(escrow_row, "id")),
        )
        return None


async def _register_escrow_watch_address(escrow_row: object) -> None:
    if _liquid_rpc_client is None:
        return

    settlement_metadata = _row_value(escrow_row, "settlement_metadata") or {}
    blinding_private_key = settlement_metadata.get("blinding_private_key")

    try:
        await _liquid_rpc_client.importaddress(
            str(_row_value(escrow_row, "multisig_address", "")),
            label=f"escrow_{_row_value(escrow_row, 'id')}",
            rescan=False,
        )
        if blinding_private_key:
            await _liquid_rpc_client.importblindingkey(
                str(_row_value(escrow_row, "multisig_address", "")),
                str(blinding_private_key),
            )
    except ElementsRPCError as exc:
        logger.exception("Failed to import escrow address %s into Elements", _row_value(escrow_row, "id"))
        await _record_settlement_failure(
            stage="escrow_watch_import",
            detail=f"Elements rejected escrow watch address import for trade {_row_value(escrow_row, 'trade_id')}: {exc}",
            trade_id=str(_row_value(escrow_row, "trade_id")),
            escrow_id=str(_row_value(escrow_row, "id")),
        )


def _merge_pset_inputs(base_pset: PSET, signed_pset: PSET) -> PSET:
    for index, input_scope in enumerate(base_pset.inputs):
        if index < len(signed_pset.inputs):
            input_scope.update(signed_pset.inputs[index])
    return base_pset


async def _ensure_wallet_settlement_address(
    conn: object,
    *,
    user_id: str,
    metadata_key: str,
    settlement_metadata: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    existing_address = settlement_metadata.get(metadata_key)
    if isinstance(existing_address, str) and existing_address:
        return existing_address, settlement_metadata

    wallet_row = await get_wallet_by_user_id(conn, user_id)
    if wallet_row is None:
        raise ContractError(
            code="wallet_not_found",
            message="Wallet not found for user.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if not settings.wallet_encryption_key:
        raise ContractError(
            code="wallet_seed_error",
            message="Wallet encryption key is unavailable for settlement address derivation.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    encrypted_seed = bytes(_row_value(wallet_row, "encrypted_seed", b""))
    key_mgr = KeyManager(
        settings.wallet_encryption_key,
        settings.bitcoin_network,
        elements_network=settings.elements_network,
    )
    try:
        seed = key_mgr.decrypt_seed(encrypted_seed)
    except Exception as exc:
        raise ContractError(
            code="wallet_seed_error",
            message="Failed to decrypt wallet seed.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc

    result = await conn.execute(
        sa.select(sa.func.max(wallet_addresses_table.c.derivation_index))
        .where(wallet_addresses_table.c.wallet_id == _row_value(wallet_row, "id"))
    )
    next_index = result.scalar_one_or_none()
    derived = key_mgr.derive_liquid_address(seed, 0 if next_index is None else int(next_index) + 1)

    if _liquid_rpc_client is not None:
        try:
            await _liquid_rpc_client.importaddress(
                derived.confidential_address,
                label=f"wallet_{_row_value(wallet_row, 'id')}",
                rescan=False,
            )
            await _liquid_rpc_client.importblindingkey(
                derived.confidential_address,
                derived.blinding_private_key,
            )
        except ElementsRPCError as exc:
            raise ContractError(
                code="elements_rpc_unavailable",
                message="Elements could not register the settlement address.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc

    await conn.execute(
        sa.insert(wallet_addresses_table).values(
            id=uuid.uuid4(),
            wallet_id=_row_value(wallet_row, "id"),
            address=derived.confidential_address,
            derivation_index=0 if next_index is None else int(next_index) + 1,
            script_pubkey=derived.script_pubkey,
            imported_to_node=True,
            created_at=datetime.now(tz=timezone.utc),
        )
    )
    await conn.commit()

    updated_metadata = dict(settlement_metadata)
    updated_metadata[metadata_key] = derived.confidential_address
    return derived.confidential_address, updated_metadata


async def _prepare_escrow_transaction_pset(
    conn: object,
    *,
    trade_row: object,
    escrow_row: object,
    payout_mode: str,
) -> object:
    if _liquid_rpc_client is None:
        return escrow_row

    settlement_metadata = dict(_row_value(escrow_row, "settlement_metadata") or {})
    status_value = str(_row_value(escrow_row, "status") or "")
    metadata_key = f"{payout_mode}_unsigned_pset"
    if settlement_metadata.get(metadata_key):
        return escrow_row

    funding_inputs = list(settlement_metadata.get("funding_inputs") or [])
    if not funding_inputs:
        return escrow_row

    from embit import script as embit_script
    from embit.liquid.transaction import LSIGHASH

    buy_order = await get_order_by_id(conn, _row_value(trade_row, "buy_order_id"))
    sell_order = await get_order_by_id(conn, _row_value(trade_row, "sell_order_id"))
    if buy_order is None or sell_order is None:
        raise ContractError(
            code="trade_not_found",
            message="Trade not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    buyer_refund_address, settlement_metadata = await _ensure_wallet_settlement_address(
        conn,
        user_id=str(_row_value(buy_order, "user_id")),
        metadata_key="buyer_refund_address",
        settlement_metadata=settlement_metadata,
    )
    seller_payout_address, settlement_metadata = await _ensure_wallet_settlement_address(
        conn,
        user_id=str(_row_value(sell_order, "user_id")),
        metadata_key="seller_payout_address",
        settlement_metadata=settlement_metadata,
    )

    outputs: list[dict[str, float]] = []
    if payout_mode == "release":
        if status_value not in {"funded", "inspection_pending", "disputed"}:
            return escrow_row

        seller_payout_sat = int(settlement_metadata.get("seller_payout_amount_sat") or _row_value(trade_row, "total_sat", 0))
        outputs.append({seller_payout_address: _sats_to_btc(seller_payout_sat)})
        marketplace_fee_sat = int(settlement_metadata.get("marketplace_fee_amount_sat") or _row_value(trade_row, "fee_sat", 0))
        if marketplace_fee_sat > 0:
            treasury_fee_address = settlement_metadata.get("treasury_fee_address")
            if not treasury_fee_address:
                treasury_fee_address = await _liquid_rpc_client.getnewaddress(
                    label=f"escrow_fee_{_row_value(escrow_row, 'id')}",
                    address_type="bech32",
                )
                settlement_metadata["treasury_fee_address"] = treasury_fee_address
            outputs.append({str(treasury_fee_address): _sats_to_btc(marketplace_fee_sat)})
    else:
        if status_value != "disputed":
            return escrow_row
        refund_amount_sat = int(_row_value(trade_row, "total_sat", 0)) + int(_row_value(trade_row, "fee_sat", 0))
        outputs.append({buyer_refund_address: _sats_to_btc(refund_amount_sat)})

    funded = await _liquid_rpc_client.walletcreatefundedpsbt(
        [{"txid": entry["txid"], "vout": int(entry["vout"])} for entry in funding_inputs],
        outputs,
        {
            "includeWatching": True,
            "changeAddress": buyer_refund_address,
            "add_inputs": False,
        },
    )

    pset = PSET.from_string(str(funded["psbt"]))
    target_script_pubkey = str(settlement_metadata.get("script_pubkey") or "")
    witness_script = embit_script.Script(bytes.fromhex(str(settlement_metadata.get("witness_script") or "")))
    escrow_input_count = 0
    for input_scope in pset.inputs:
        utxo = input_scope.utxo
        if utxo is None:
            continue
        if utxo.script_pubkey.data.hex() != target_script_pubkey:
            continue
        input_scope.witness_script = witness_script
        input_scope.sighash_type = LSIGHASH.ALL
        escrow_input_count += 1

    if escrow_input_count == 0:
        raise ContractError(
            code="settlement_pset_invalid",
            message="Settlement PSET did not include the expected escrow inputs.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    settlement_metadata.update(
        {
            metadata_key: pset.to_string(),
            f"{payout_mode}_estimated_fee_sat": int(float(funded.get("fee", 0)) * 100_000_000),
        }
    )
    return await update_escrow_settlement_metadata(
        conn,
        escrow_id=_row_value(escrow_row, "id"),
        settlement_metadata=settlement_metadata,
    )


async def _watch_escrows_once() -> None:
    async with _runtime_engine().connect() as conn:
        escrow_rows = await list_escrows_by_status(conn, statuses=("created",))
        for escrow_row in escrow_rows:
            try:
                trade_row = await get_trade_by_id(conn, _row_value(escrow_row, "trade_id"))
                if trade_row is None:
                    continue

                buy_order = await get_order_by_id(conn, _row_value(trade_row, "buy_order_id"))
                sell_order = await get_order_by_id(conn, _row_value(trade_row, "sell_order_id"))
                if buy_order is None or sell_order is None:
                    continue

                expires_at = _row_value(escrow_row, "expires_at")
                if isinstance(expires_at, datetime):
                    expiry = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
                    if expiry <= datetime.now(tz=timezone.utc):
                        await _record_settlement_failure(
                            stage="escrow_funding_timeout",
                            detail=f"Escrow funding expired for trade {_row_value(trade_row, 'id')}.",
                            trade_id=str(_row_value(trade_row, "id")),
                            escrow_id=str(_row_value(escrow_row, "id")),
                        )
                        updated_trade_row, updated_escrow_row = await expire_unfunded_escrow(
                            conn,
                            trade_row=trade_row,
                            escrow_row=escrow_row,
                            buy_order=buy_order,
                            sell_order=sell_order,
                        )
                        await record_audit_event(
                            conn,
                            settings=settings,
                            request=_system_request(f"/internal/escrows/{_row_value(trade_row, 'id')}/expire"),
                            action="marketplace.escrow.expire",
                            actor_role="system",
                            target_type="escrow",
                            target_id=_row_value(updated_escrow_row, "id"),
                            metadata={"trade_id": str(_row_value(updated_trade_row, "id"))},
                        )
                        await _publish_escrow_expired(
                            updated_trade_row,
                            escrow_row=updated_escrow_row,
                            buy_order=buy_order,
                            sell_order=sell_order,
                        )
                        continue

                observation = await _scan_escrow_funding(escrow_row)
                if observation is None or observation.total_amount_sat < int(_row_value(escrow_row, "locked_amount_sat", 0)):
                    continue

                try:
                    updated_trade_row, updated_escrow_row = await mark_escrow_funded(
                        conn,
                        trade_id=_row_value(trade_row, "id"),
                        funding_txid=observation.txid,
                        settlement_metadata_update={
                            "funding_inputs": observation.utxos,
                            "funding_total_amount_sat": observation.total_amount_sat,
                            "funded_at": _utc_now_iso(),
                        },
                    )
                except LookupError:
                    continue

                await _prepare_escrow_transaction_pset(
                    conn,
                    trade_row=updated_trade_row,
                    escrow_row=updated_escrow_row,
                    payout_mode="release",
                )
                await _publish_escrow_funded(
                    updated_trade_row,
                    escrow_row=updated_escrow_row,
                    buy_order=buy_order,
                    sell_order=sell_order,
                )
            except Exception:
                logger.exception("Escrow watcher failed for trade %s", _row_value(escrow_row, "trade_id"))


async def _escrow_watcher_loop(stop_event: asyncio.Event) -> None:
    interval = max(int(settings.marketplace_escrow_watch_interval_seconds), 1)
    while not stop_event.is_set():
        try:
            await _watch_escrows_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Escrow watcher iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


app = FastAPI(title="Marketplace Service", lifespan=_lifespan)
install_http_security(
    app,
    settings,
    sensitive_paths=(
        "/orders",
        "/escrows/",
        "/trades/",
    ),
)
mount_metrics_endpoint(app, settings)


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


async def _get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> AuthenticatedPrincipal:
    if credentials is None:
        raise ContractError(
            code="authentication_required",
            message="Authentication is required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return await _principal_from_access_token(credentials.credentials)


async def _principal_from_access_token(access_token: str) -> AuthenticatedPrincipal:
    try:
        claims = decode_token(
            access_token,
            _jwt_secret(),
            expected_type="access",
        )
    except JWTError as exc:
        raise _invalid_access_token_error() from exc

    user_id = _normalize_uuid_claim(claims.get("sub"))
    role = str(claims.get("role") or "user")
    if user_id is None:
        raise _invalid_access_token_error()

    async with _runtime_engine().connect() as conn:
        row = await get_user_by_id(conn, user_id)

    if row is None or _row_value(row, "deleted_at") is not None:
        raise _invalid_access_token_error()

    return AuthenticatedPrincipal(id=user_id, role=role)


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _best_prices(rows: list[object]) -> tuple[int | None, int | None]:
    best_bid: int | None = None
    best_ask: int | None = None

    for row in rows:
        status_value = _row_value(row, "status")
        if status_value not in {"open", "partially_filled"}:
            continue

        remaining_quantity = _remaining_quantity(row)
        if remaining_quantity <= 0:
            continue

        price_sat = int(_row_value(row, "price_sat", 0))
        if _row_value(row, "side") == "buy":
            if best_bid is None or price_sat > best_bid:
                best_bid = price_sat
        else:
            if best_ask is None or price_sat < best_ask:
                best_ask = price_sat

    return best_bid, best_ask


async def _price_snapshot(token_id: uuid.UUID) -> dict[str, Any] | None:
    async with _runtime_engine().connect() as conn:
        token_row = await get_token_by_id(conn, token_id)
        if token_row is None:
            return None

        rows = await list_orders(conn, token_id=token_id)
        last_trade_price = await get_last_trade_price_for_token(conn, token_id)
        volume_24h = await get_trade_volume_24h(conn, token_id)

    bid, ask = _best_prices(rows)
    return {
        "token_id": str(token_id),
        "last_price_sat": int(last_trade_price) if last_trade_price is not None else None,
        "bid": bid,
        "ask": ask,
        "volume_24h": int(volume_24h),
        "timestamp": _utc_now_iso(),
    }


def _price_message(event_id: str | None, snapshot: dict[str, Any]) -> dict[str, Any]:
    message = {
        "event": "price_update",
        "data": snapshot,
    }
    if event_id is not None:
        message["id"] = event_id
    return message


def _notification_message(principal_id: str, *, topic: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if topic == "trade.matched":
        if payload.get("buyer_id") == principal_id:
            order_id = payload.get("buy_order_id")
        elif payload.get("seller_id") == principal_id:
            order_id = payload.get("sell_order_id")
        else:
            return None

        return {
            "event": "order_filled",
            "data": {
                "order_id": order_id,
                "trade_id": payload.get("trade_id"),
                "token_id": payload.get("token_id"),
                "filled_quantity": payload.get("quantity"),
                "price_sat": payload.get("price_sat"),
                "status": payload.get("status"),
            },
        }

    if topic == "escrow.funded":
        participant_ids = {payload.get("buyer_id"), payload.get("seller_id")}
        if principal_id not in participant_ids:
            return None

        return {
            "event": "escrow_funded",
            "data": {
                "trade_id": payload.get("trade_id"),
                "token_id": payload.get("token_id"),
                "escrow_id": payload.get("escrow_id"),
                "txid": payload.get("funding_txid"),
                "status": payload.get("status"),
            },
        }

    if topic == "escrow.released":
        participant_ids = {payload.get("buyer_id"), payload.get("seller_id")}
        if principal_id not in participant_ids:
            return None

        return {
            "event": "escrow_released",
            "data": {
                "trade_id": payload.get("trade_id"),
                "escrow_id": payload.get("escrow_id"),
                "txid": payload.get("release_txid"),
                "status": payload.get("status"),
                "trade_status": payload.get("trade_status"),
                "settled_at": payload.get("settled_at"),
            },
        }

    if topic == "escrow.expired":
        participant_ids = {payload.get("buyer_id"), payload.get("seller_id")}
        if principal_id not in participant_ids:
            return None

        return {
            "event": "escrow_expired",
            "data": {
                "trade_id": payload.get("trade_id"),
                "escrow_id": payload.get("escrow_id"),
                "status": payload.get("status"),
            },
        }

    if topic == "ai.evaluation.complete":
        if payload.get("owner_id") != principal_id:
            return None

        return {
            "event": "ai_evaluation_complete",
            "data": {
                "asset_id": payload.get("asset_id"),
                "ai_score": payload.get("ai_score"),
                "projected_roi": payload.get("projected_roi"),
                "status": payload.get("status"),
                "completed_at": payload.get("completed_at"),
            },
        }

    return None


async def _websocket_auth_payload(websocket: WebSocket) -> dict[str, Any]:
    access_token = websocket.query_params.get("access_token")
    resume_token = websocket.query_params.get("resume_token")

    if access_token:
        return {
            "access_token": access_token,
            "resume_token": resume_token,
        }

    authorization = websocket.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return {
            "access_token": authorization[7:].strip(),
            "resume_token": resume_token,
        }

    try:
        message = await asyncio.wait_for(websocket.receive_json(), timeout=5)
    except Exception as exc:
        raise ContractError(
            code="authentication_required",
            message="Authentication is required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    if not isinstance(message, dict):
        raise ContractError(
            code="authentication_required",
            message="Authentication is required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return {
        "access_token": message.get("access_token"),
        "resume_token": message.get("resume_token", resume_token),
    }


async def _close_websocket_for_contract_error(websocket: WebSocket, exc: ContractError) -> None:
    await websocket.send_json({"error": {"code": exc.code, "message": exc.message}})
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=exc.message)


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


@app.post("/orders", status_code=status.HTTP_201_CREATED, response_model=OrderResponse)
async def place_order(
    request: Request,
    body: OrderCreateRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        token_row = await get_token_by_id(conn, body.token_id)
        if token_row is None:
            raise _token_not_found_error()

        wallet_row = await get_wallet_by_user_id(conn, principal.id)
        if wallet_row is None:
            raise _wallet_not_found_error()

        if body.side == "buy":
            reserved_buy_commitment = await get_reserved_buy_commitment(conn, principal.id)
            available_sats = _wallet_total_balance(wallet_row) - reserved_buy_commitment
            if available_sats < body.quantity * body.price_sat:
                raise _insufficient_sats_error()
        else:
            balance_row = await get_token_balance_for_user(conn, principal.id, body.token_id)
            reserved_sell_quantity = await get_reserved_sell_quantity(conn, principal.id, body.token_id)
            current_balance = int(_row_value(balance_row, "balance", 0))
            available_balance = current_balance - reserved_sell_quantity
            if available_balance < body.quantity:
                raise _insufficient_token_balance_error()

        # Enforce KYC for high-value trades
        trade_total_sat = body.quantity * body.price_sat
        await _enforce_kyc_threshold(conn, principal.id, trade_total_sat)

        triggered_at = None
        if body.order_type == "stop_limit" and body.trigger_price_sat is not None:
            reference_price = await get_reference_price_for_token(conn, body.token_id)
            if _stop_order_triggered(
                side=body.side,
                trigger_price_sat=body.trigger_price_sat,
                reference_price=reference_price,
            ):
                triggered_at = datetime.now(tz=timezone.utc)

        order_row = await create_order(
            conn,
            user_id=principal.id,
            token_id=body.token_id,
            side=body.side,
            order_type=body.order_type,
            quantity=body.quantity,
            price_sat=body.price_sat,
            trigger_price_sat=body.trigger_price_sat,
            triggered_at=triggered_at,
        )

        published_trades: list[tuple[object, object, object, object]] = []

        while triggered_at is not None or body.order_type == "limit":
            current_order = await get_order_by_id(conn, _row_value(order_row, "id"))
            if current_order is None or _remaining_quantity(current_order) <= 0:
                order_row = current_order or order_row
                break

            if body.order_type == "stop_limit":
                latest_reference_price = await get_reference_price_for_token(conn, body.token_id)
            else:
                latest_reference_price = None
            if latest_reference_price is not None:
                await activate_triggered_orders(
                    conn,
                    token_id=body.token_id,
                    reference_price=latest_reference_price,
                )

            matched_order = await find_best_match(
                conn,
                token_id=body.token_id,
                incoming_side=body.side,
                incoming_price=body.price_sat,
                requester_id=principal.id,
            )
            if matched_order is None or _remaining_quantity(matched_order) <= 0:
                order_row = current_order
                break

            fill_quantity = min(_remaining_quantity(current_order), _remaining_quantity(matched_order))
            trade_price = int(_row_value(matched_order, "price_sat", 0))

            buy_order = current_order if body.side == "buy" else matched_order
            sell_order = current_order if body.side == "sell" else matched_order

            try:
                trade_row, escrow_row = await create_trade_escrow(
                    conn,
                    buy_order=buy_order,
                    sell_order=sell_order,
                    quantity=fill_quantity,
                    price_sat=trade_price,
                )
            except ValueError as exc:
                if str(exc) == "insufficient_token_balance":
                    await cancel_order(
                        conn,
                        order_id=_row_value(matched_order, "id"),
                        user_id=_row_value(matched_order, "user_id"),
                    )
                    continue
                if str(exc) == "order_insufficient_quantity":
                    order_row = await get_order_by_id(conn, _row_value(order_row, "id")) or order_row
                    continue
                raise
            except LookupError as exc:
                if str(exc) == "wallet_not_found":
                    await cancel_order(
                        conn,
                        order_id=_row_value(matched_order, "id"),
                        user_id=_row_value(matched_order, "user_id"),
                    )
                    continue
                raise

            published_trades.append((trade_row, escrow_row, buy_order, sell_order))
            await activate_triggered_orders(
                conn,
                token_id=body.token_id,
                reference_price=trade_price,
            )
            order_row = await get_order_by_id(conn, _row_value(order_row, "id")) or order_row

        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="marketplace.order.place",
            actor_id=principal.id,
            actor_role=principal.role,
            target_type="order",
            target_id=_row_value(order_row, "id"),
            metadata={
                "token_id": str(body.token_id),
                "side": body.side,
                "order_type": body.order_type,
                "quantity": body.quantity,
                "price_sat": body.price_sat,
                "trigger_price_sat": body.trigger_price_sat,
                "matched_trades": len(published_trades),
            },
        )

    for trade_row, escrow_row, buy_order, sell_order in published_trades:
        try:
            await _register_escrow_watch_address(escrow_row)
        except Exception:
            logger.exception("Escrow watch registration failed for trade %s", _row_value(trade_row, "id"))
        try:
            await _publish_trade_matched(
                trade_row,
                escrow_row=escrow_row,
                buy_order=buy_order,
                sell_order=sell_order,
            )
        except Exception:
            logger.exception("Trade event publish failed for trade %s", _row_value(trade_row, "id"))

    record_business_event("order_place")
    return OrderResponse(order=_order_out(order_row)).model_dump(mode="json")


@app.get("/orders", response_model=OrderListResponse)
async def get_orders(
    token_id: uuid.UUID | None = Query(default=None),
    side: str | None = Query(default=None, pattern="^(buy|sell)$"),
    status_filter: str | None = Query(default=None, alias="status", pattern="^(open|partially_filled|filled|cancelled)$"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        rows = await list_orders(
            conn,
            token_id=token_id,
            side=side,
            status=status_filter,
        )

    page, next_cursor = _build_page(rows, cursor=cursor, limit=limit, label="order")
    return OrderListResponse(
        orders=[_order_out(row) for row in page],
        next_cursor=next_cursor,
    ).model_dump(mode="json")


@app.get("/orderbook/{token_id}", response_model=OrderBookResponse)
async def get_order_book(
    token_id: uuid.UUID,
    _principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        token_row = await get_token_by_id(conn, token_id)
        if token_row is None:
            raise _token_not_found_error()

        rows = await list_orders(conn, token_id=token_id)
        last_trade_price = await get_last_trade_price_for_token(conn, token_id)
        volume_24h = await get_trade_volume_24h(conn, token_id)

    bids: dict[int, int] = defaultdict(int)
    asks: dict[int, int] = defaultdict(int)
    for row in rows:
        status_value = _row_value(row, "status")
        if status_value not in {"open", "partially_filled"}:
            continue

        remaining_quantity = _remaining_quantity(row)
        if remaining_quantity <= 0:
            continue

        if _row_value(row, "order_type", "limit") == "stop_limit" and _row_value(row, "triggered_at") is None:
            continue

        price_sat = int(_row_value(row, "price_sat", 0))
        if _row_value(row, "side") == "buy":
            bids[price_sat] += remaining_quantity
        else:
            asks[price_sat] += remaining_quantity

    return OrderBookResponse(
        token_id=token_id,
        bids=[OrderBookLevel(price_sat=price, total_quantity=quantity) for price, quantity in sorted(bids.items(), reverse=True)],
        asks=[OrderBookLevel(price_sat=price, total_quantity=quantity) for price, quantity in sorted(asks.items())],
        last_trade_price_sat=last_trade_price,
        volume_24h=volume_24h,
    ).model_dump(mode="json")


@app.delete("/orders/{order_id}", response_model=CancelOrderResponse)
async def delete_order(
    request: Request,
    order_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        existing_order = await get_order_by_id(conn, order_id)
        if existing_order is None:
            raise ContractError(
                code="order_not_found",
                message="Order not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if str(_row_value(existing_order, "user_id")) != principal.id:
            raise ContractError(
                code="forbidden",
                message="You do not have permission to access this resource.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if _row_value(existing_order, "status") not in {"open", "partially_filled"}:
            raise ContractError(
                code="order_state_conflict",
                message="Only open or partially filled orders can be cancelled.",
                status_code=status.HTTP_409_CONFLICT,
            )

        cancelled_order = await cancel_order(conn, order_id=order_id, user_id=principal.id)
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="marketplace.order.cancel",
            actor_id=principal.id,
            actor_role=principal.role,
            target_type="order",
            target_id=order_id,
            metadata={"status": "cancelled"},
        )

    assert cancelled_order is not None
    return CancelOrderResponse(
        order=CancelledOrderOut(id=_row_value(cancelled_order, "id"), status="cancelled"),
    ).model_dump(mode="json")


@app.get("/escrows/{trade_id}", response_model=EscrowResponse)
async def get_escrow_details(
    trade_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        trade_row = await get_trade_by_id(conn, trade_id)
        if trade_row is None:
            raise _trade_not_found_error()

        buy_order = await get_order_by_id(conn, _row_value(trade_row, "buy_order_id"))
        sell_order = await get_order_by_id(conn, _row_value(trade_row, "sell_order_id"))
        if buy_order is None or sell_order is None:
            raise _trade_not_found_error()

        participant_ids = {
            str(_row_value(buy_order, "user_id")),
            str(_row_value(sell_order, "user_id")),
        }
        if principal.role != "admin" and principal.id not in participant_ids:
            raise ContractError(
                code="forbidden",
                message="You do not have permission to access this resource.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        escrow_row = await get_escrow_by_trade_id(conn, trade_id)
        if escrow_row is None:
            raise _escrow_not_found_error()

    return EscrowResponse(escrow=_escrow_out(escrow_row)).model_dump(mode="json")


@app.post("/escrows/{trade_id}/sign", response_model=EscrowResponse)
async def sign_escrow_release(
    request: Request,
    trade_id: uuid.UUID,
    body: EscrowSignRequest,
    x_2fa_code: str | None = Header(default=None, alias="X-2FA-Code"),
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        trade_row = await get_trade_by_id(conn, trade_id)
        if trade_row is None:
            raise _trade_not_found_error()

        buy_order = await get_order_by_id(conn, _row_value(trade_row, "buy_order_id"))
        sell_order = await get_order_by_id(conn, _row_value(trade_row, "sell_order_id"))
        if buy_order is None or sell_order is None:
            raise _trade_not_found_error()

        buyer_id = str(_row_value(buy_order, "user_id"))
        seller_id = str(_row_value(sell_order, "user_id"))

        if principal.id == buyer_id:
            signer_role = "buyer"
        elif principal.id == seller_id:
            signer_role = "seller"
        else:
            raise ContractError(
                code="forbidden",
                message="You are not a participant in this trade.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        escrow_row = await get_escrow_by_trade_id(conn, trade_id)
        if escrow_row is None:
            raise _escrow_not_found_error()

        escrow_status = str(_row_value(escrow_row, "status") or "")
        if escrow_status not in {"funded", "inspection_pending"}:
            raise ContractError(
                code="escrow_state_conflict",
                message="Escrow must be funded and awaiting participant approval before signatures can be submitted.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if escrow_status == "funded" and signer_role != "seller":
            raise ContractError(
                code="escrow_state_conflict",
                message="The seller must acknowledge delivery before buyer approval can be submitted.",
                status_code=status.HTTP_409_CONFLICT,
            )

        await _check_2fa(conn, principal.id, x_2fa_code)
        escrow_row = await _prepare_escrow_transaction_pset(
            conn,
            trade_row=trade_row,
            escrow_row=escrow_row,
            payout_mode="release",
        )

        settlement_metadata = dict(_row_value(escrow_row, "settlement_metadata") or {})
        unsigned_pset = settlement_metadata.get("release_unsigned_pset")
        if not unsigned_pset:
            raise ContractError(
                code="settlement_pset_missing",
                message="Escrow settlement PSET is not available yet.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if _liquid_rpc_client is None:
            raise ContractError(
                code="elements_rpc_unavailable",
                message="Elements RPC is unavailable for escrow settlement.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        collected_signatures = dict(_row_value(escrow_row, "collected_signatures") or {})
        release_signatures = _signature_bucket(collected_signatures, path="release")
        if signer_role in release_signatures:
            raise ContractError(
                code="signature_already_recorded",
                message="This participant has already signed the release transaction.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if signer_role == "buyer" and "seller" not in release_signatures:
            raise ContractError(
                code="escrow_state_conflict",
                message="Buyer approval is only available after the seller has signed for delivery.",
                status_code=status.HTTP_409_CONFLICT,
            )

        base_pset = settlement_metadata.get("release_signed_pset") or unsigned_pset
        pset = PSET.from_string(str(base_pset))
        if body.pset is not None:
            submitted_pset = PSET.from_string(body.pset)
            pset = _merge_pset_inputs(pset, submitted_pset)

        signer_material = await resolve_escrow_signing_material(conn, principal.id)
        if signer_material is None and body.pset is None:
            raise ContractError(
                code="signed_pset_required",
                message="A signer-supplied PSET is required for this escrow participant.",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        signature_source = "pset_upload" if body.pset is not None else "custodial_wallet"
        if signer_material is not None:
            pset.sign_with(derive_private_key(signer_material))
        signature_fingerprint = hashlib.sha256(pset.to_string().encode("utf-8")).hexdigest()
        signature_record = _signature_record(
            signer_role=signer_role,
            actor_id=principal.id,
            signature_fingerprint=signature_fingerprint,
            source=signature_source,
        )
        release_signatures[signer_role] = signature_record
        collected_signatures["release"] = release_signatures
        settlement_metadata["release_signed_pset"] = pset.to_string()
        settlement_metadata["release_last_signed_by"] = signer_role

        if {"buyer", "seller"}.issubset(release_signatures):
            from embit.liquid.finalizer import finalize_psbt

            processed = await _liquid_rpc_client.walletprocesspsbt(pset.to_string(), sign=False)
            final_pset = PSET.from_string(str(processed.get("psbt") or pset.to_string()))
            finalized_tx = finalize_psbt(final_pset)
            if finalized_tx is None:
                raise ContractError(
                    code="settlement_pset_incomplete",
                    message="Escrow settlement PSET is incomplete after both participants signed.",
                    status_code=status.HTTP_409_CONFLICT,
                )

            txid = await _liquid_rpc_client.sendrawtransaction(finalized_tx.serialize().hex())
            settlement_metadata.update(
                {
                    "release_signed_pset": final_pset.to_string(),
                    "broadcast_at": _utc_now_iso(),
                    "release_txid": txid,
                }
            )
            try:
                trade_row, escrow_row = await process_escrow_signature(
                    conn,
                    escrow_row=escrow_row,
                    trade_row=trade_row,
                    buy_order=buy_order,
                    sell_order=sell_order,
                    collected_signatures=collected_signatures,
                    release_txid=txid,
                    settlement_metadata=settlement_metadata,
                )
            except Exception as exc:
                await _record_settlement_failure(
                    stage="escrow_release_persist",
                    detail=f"Escrow release broadcast for trade {trade_id} succeeded but DB persistence failed: {exc}",
                    trade_id=str(trade_id),
                    escrow_id=str(_row_value(escrow_row, 'id')),
                )
                raise
        else:
            escrow_row = await record_escrow_signature(
                conn,
                escrow_row=escrow_row,
                signer_role=signer_role,
                signature_path="release",
                signature_record=signature_record,
                settlement_metadata=settlement_metadata,
            )
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="marketplace.escrow.sign_release",
            actor_id=principal.id,
            actor_role=principal.role,
            target_type="escrow",
            target_id=_row_value(escrow_row, "id"),
            metadata={"trade_id": str(trade_id), "signer_role": signer_role},
        )

    if _row_value(escrow_row, "status") == "released":
        try:
            await _publish_escrow_released(
                trade_row,
                escrow_row=escrow_row,
                buy_order=buy_order,
                sell_order=sell_order,
            )
        except Exception:
            logger.exception("Escrow released event publish failed for trade %s", trade_id)

    return EscrowResponse(escrow=_escrow_out(escrow_row)).model_dump(mode="json")


@app.get("/trades", response_model=TradeListResponse)
async def get_trade_history(
    token_id: uuid.UUID | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        if token_id is not None and await get_token_by_id(conn, token_id) is None:
            raise _token_not_found_error()

        rows = await list_trades(conn, token_id=token_id)

    page, next_cursor = _build_page(rows, cursor=cursor, limit=limit, label="trade")
    return TradeListResponse(
        trades=[_trade_out(row) for row in page],
        next_cursor=next_cursor,
    ).model_dump(mode="json")


@app.websocket("/ws/prices/{token_id}")
async def price_stream(websocket: WebSocket, token_id: uuid.UUID):
    last_event_id = websocket.query_params.get("last_event_id")

    await websocket.accept()

    if last_event_id is None:
        snapshot = await _price_snapshot(token_id)
        if snapshot is None:
            await websocket.send_json(
                {
                    "error": {
                        "code": "token_not_found",
                        "message": "Token not found.",
                    }
                }
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Token not found.")
            return

        await websocket.send_json(_price_message(None, snapshot))

    try:
        async for stream_event in _realtime_feed.listen(
            ["trade.matched"],
            resume_from={"trade.matched": last_event_id} if last_event_id else None,
        ):
            if stream_event.payload.get("token_id") != str(token_id):
                continue

            snapshot = await _price_snapshot(token_id)
            if snapshot is None:
                continue

            await websocket.send_json(_price_message(stream_event.event_id, snapshot))
    except WebSocketDisconnect:
        return


@app.websocket("/ws/notifications")
async def notification_stream(websocket: WebSocket):
    await websocket.accept()

    try:
        auth_payload = await _websocket_auth_payload(websocket)
        access_token = auth_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ContractError(
                code="authentication_required",
                message="Authentication is required.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        principal = await _principal_from_access_token(access_token)

        try:
            resume_from = decode_resume_token(
                auth_payload.get("resume_token"),
                allowed_topics={"trade.matched", "escrow.funded", "escrow.expired", "escrow.released", "ai.evaluation.complete"},
            )
        except ValueError as exc:
            raise _invalid_resume_token_error() from exc
    except ContractError as exc:
        await _close_websocket_for_contract_error(websocket, exc)
        return

    try:
        async for stream_event in _realtime_feed.listen(
            ["trade.matched", "escrow.funded", "escrow.expired", "escrow.released", "ai.evaluation.complete"],
            resume_from=resume_from or None,
        ):
            message = _notification_message(
                principal.id,
                topic=stream_event.topic,
                payload=stream_event.payload,
            )
            if message is None:
                continue

            message["id"] = f"{stream_event.topic}:{stream_event.event_id}"
            message["resume_token"] = encode_resume_token(stream_event.positions)
            await websocket.send_json(message)
    except WebSocketDisconnect:
        return


@app.post(
    "/trades/{trade_id}/dispute",
    status_code=status.HTTP_201_CREATED,
    response_model=DisputeResponse,
)
async def create_dispute(
    request: Request,
    trade_id: uuid.UUID,
    body: DisputeOpenRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        trade_row = await get_trade_by_id(conn, trade_id)
        if trade_row is None:
            raise _trade_not_found_error()

        buy_order = await get_order_by_id(conn, _row_value(trade_row, "buy_order_id"))
        sell_order = await get_order_by_id(conn, _row_value(trade_row, "sell_order_id"))
        if buy_order is None or sell_order is None:
            raise _trade_not_found_error()

        participant_ids = {
            str(_row_value(buy_order, "user_id")),
            str(_row_value(sell_order, "user_id")),
        }
        if principal.id not in participant_ids:
            raise ContractError(
                code="forbidden",
                message="You are not a participant in this trade.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if _row_value(trade_row, "status") != "escrowed":
            raise ContractError(
                code="trade_state_conflict",
                message="Only escrowed trades can be disputed.",
                status_code=status.HTTP_409_CONFLICT,
            )

        existing_dispute = await get_dispute_by_trade_id(conn, trade_id)
        if existing_dispute is not None:
            raise ContractError(
                code="dispute_already_exists",
                message="A dispute has already been opened for this trade.",
                status_code=status.HTTP_409_CONFLICT,
            )

        try:
            dispute_row = await open_dispute(
                conn,
                trade_id=trade_id,
                opened_by=principal.id,
                reason=body.reason,
            )
        except LookupError as exc:
            raise ContractError(
                code="trade_state_conflict",
                message="Trade or escrow is not in a disputable state.",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="marketplace.dispute.open",
            actor_id=principal.id,
            actor_role=principal.role,
            target_type="dispute",
            target_id=_row_value(dispute_row, "id"),
            metadata={"trade_id": str(trade_id)},
        )

    record_business_event("dispute_open")
    return DisputeResponse(dispute=_dispute_out(dispute_row)).model_dump(mode="json")


@app.post(
    "/trades/{trade_id}/dispute/resolve",
    response_model=DisputeResponse,
)
async def resolve_trade_dispute(
    request: Request,
    trade_id: uuid.UUID,
    body: DisputeResolveRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    if principal.role != "admin":
        raise ContractError(
            code="forbidden",
            message="Only admins can resolve disputes.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    async with _runtime_engine().connect() as conn:
        trade_row = await get_trade_by_id(conn, trade_id)
        if trade_row is None:
            raise _trade_not_found_error()

        if _row_value(trade_row, "status") != "disputed":
            raise ContractError(
                code="trade_state_conflict",
                message="Trade is not in a disputed state.",
                status_code=status.HTTP_409_CONFLICT,
            )

        existing_dispute = await get_dispute_by_trade_id(conn, trade_id)
        if existing_dispute is None:
            raise ContractError(
                code="dispute_not_found",
                message="No open dispute found for this trade.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if _row_value(existing_dispute, "status") != "open":
            raise ContractError(
                code="dispute_already_resolved",
                message="This dispute has already been resolved.",
                status_code=status.HTTP_409_CONFLICT,
            )

        buy_order = await get_order_by_id(conn, _row_value(trade_row, "buy_order_id"))
        sell_order = await get_order_by_id(conn, _row_value(trade_row, "sell_order_id"))
        if buy_order is None or sell_order is None:
            raise _trade_not_found_error()

        escrow_row = await get_escrow_by_trade_id(conn, trade_id)
        if escrow_row is None or _row_value(escrow_row, "status") != "disputed":
            raise _escrow_not_found_error()
        if _liquid_rpc_client is None:
            raise ContractError(
                code="elements_rpc_unavailable",
                message="Elements RPC is unavailable for dispute resolution.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payout_mode = "release" if body.resolution == "release" else "refund"
        escrow_row = await _prepare_escrow_transaction_pset(
            conn,
            trade_row=trade_row,
            escrow_row=escrow_row,
            payout_mode=payout_mode,
        )

        settlement_metadata = dict(_row_value(escrow_row, "settlement_metadata") or {})
        unsigned_pset = settlement_metadata.get(f"{payout_mode}_unsigned_pset")
        if not unsigned_pset:
            raise ContractError(
                code="settlement_pset_missing",
                message="Settlement PSET is not available for this dispute resolution path.",
                status_code=status.HTTP_409_CONFLICT,
            )

        participant_role = "seller" if body.resolution == "release" else "buyer"
        participant_id = str(
            _row_value(sell_order, "user_id") if participant_role == "seller" else _row_value(buy_order, "user_id")
        )
        collected_signatures = dict(_row_value(escrow_row, "collected_signatures") or {})
        path_signatures = _signature_bucket(collected_signatures, path=payout_mode)

        pset = PSET.from_string(str(settlement_metadata.get(f"{payout_mode}_signed_pset") or unsigned_pset))
        if body.pset is not None:
            pset = _merge_pset_inputs(pset, PSET.from_string(body.pset))

        participant_source = "pset_upload" if body.pset is not None else "custodial_resolution"
        if participant_role not in path_signatures:
            signer_material = await resolve_escrow_signing_material(conn, participant_id)
            if signer_material is None and body.pset is None:
                raise ContractError(
                    code="signed_pset_required",
                    message="A participant-signed PSET is required for this dispute resolution path.",
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            if signer_material is not None:
                pset.sign_with(derive_private_key(signer_material))
            path_signatures[participant_role] = _signature_record(
                signer_role=participant_role,
                actor_id=participant_id,
                signature_fingerprint=hashlib.sha256(pset.to_string().encode("utf-8")).hexdigest(),
                source=participant_source,
            )

        platform_signer = build_platform_signer(settings)
        platform_private_key = platform_signer.private_key()
        if not platform_private_key:
            raise ContractError(
                code="platform_signer_unavailable",
                message="Platform signer private key is unavailable for dispute resolution.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        pset.sign_with(ec.PrivateKey(bytes.fromhex(platform_private_key)))
        path_signatures["platform"] = _signature_record(
            signer_role="platform",
            actor_id=principal.id,
            signature_fingerprint=hashlib.sha256(pset.to_string().encode("utf-8")).hexdigest(),
            source="platform_resolution",
        )
        collected_signatures[payout_mode] = path_signatures

        from embit.liquid.finalizer import finalize_psbt

        processed = await _liquid_rpc_client.walletprocesspsbt(pset.to_string(), sign=False)
        final_pset = PSET.from_string(str(processed.get("psbt") or pset.to_string()))
        finalized_tx = finalize_psbt(final_pset)
        if finalized_tx is None:
            raise ContractError(
                code="settlement_pset_incomplete",
                message="Dispute resolution PSET is incomplete after the required signatures were applied.",
                status_code=status.HTTP_409_CONFLICT,
            )

        txid = await _liquid_rpc_client.sendrawtransaction(finalized_tx.serialize().hex())
        settlement_metadata.update(
            {
                "resolution": body.resolution,
                f"{payout_mode}_signed_pset": final_pset.to_string(),
                f"{payout_mode}_broadcast_at": _utc_now_iso(),
                f"{payout_mode}_txid": txid,
            }
        )

        try:
            dispute_row, _trade_row, _escrow_row = await resolve_dispute(
                conn,
                trade_id=trade_id,
                resolved_by=principal.id,
                resolution=body.resolution,
                resolution_txid=txid,
                collected_signatures=collected_signatures,
                settlement_metadata=settlement_metadata,
            )
        except LookupError as exc:
            raise ContractError(
                code="resolution_conflict",
                message="Could not apply resolution due to a state conflict.",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        await record_audit_event(
            conn,
            settings=settings,
            request=request,
            action="marketplace.dispute.resolve",
            actor_id=principal.id,
            actor_role=principal.role,
            target_type="dispute",
            target_id=_row_value(dispute_row, "id"),
            metadata={"trade_id": str(trade_id), "resolution": body.resolution},
        )

    record_business_event("dispute_resolve")
    return DisputeResponse(dispute=_dispute_out(dispute_row)).model_dump(mode="json")


if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
