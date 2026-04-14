from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import sys
from typing import Any
import uuid

from fastapi import Depends, FastAPI, Query, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth.jwt_utils import decode_token
from common import InternalEventBus, RedisStreamMirror, get_readiness_payload, get_settings
from marketplace.bitcoin_rpc import BitcoinRPCClient, BitcoinRPCError, FundingObservation
from marketplace.db import (
    cancel_order,
    create_order,
    create_trade_escrow,
    find_best_match,
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
    list_orders,
    list_trades,
    mark_escrow_funded,
)
from marketplace.schemas import (
    CancelOrderResponse,
    CancelledOrderOut,
    EscrowOut,
    EscrowResponse,
    OrderBookLevel,
    OrderBookResponse,
    OrderCreateRequest,
    OrderListResponse,
    OrderOut,
    OrderResponse,
    TradeListResponse,
    TradeOut,
)


settings = get_settings(service_name="marketplace", default_port=8003)
_bearer_scheme = HTTPBearer(auto_error=False)
_engine: AsyncEngine | object | None = None
logger = logging.getLogger(__name__)
_event_bus = InternalEventBus()
_event_bus.subscribe("trade.matched", RedisStreamMirror(settings.redis_url))
_event_bus.subscribe("escrow.funded", RedisStreamMirror(settings.redis_url))
_bitcoin_rpc_client = (
    BitcoinRPCClient(
        host=settings.bitcoin_rpc_host,
        port=settings.bitcoin_rpc_port,
        username=settings.bitcoin_rpc_user,
        password=settings.bitcoin_rpc_password,
    )
    if settings.bitcoin_rpc_password
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
    yield
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
    return OrderOut(
        id=_row_value(row, "id"),
        token_id=_row_value(row, "token_id"),
        side=_row_value(row, "side"),
        quantity=int(_row_value(row, "quantity", 0)),
        price_sat=int(_row_value(row, "price_sat", 0)),
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
    return EscrowOut(
        id=_row_value(row, "id"),
        trade_id=_row_value(row, "trade_id"),
        multisig_address=_row_value(row, "multisig_address"),
        locked_amount_sat=int(_row_value(row, "locked_amount_sat", 0)),
        funding_txid=_row_value(row, "funding_txid"),
        status=_row_value(row, "status"),
        expires_at=_row_value(row, "expires_at"),
    )


def _remaining_quantity(row: object) -> int:
    return int(_row_value(row, "quantity", 0)) - int(_row_value(row, "filled_quantity", 0))


def _wallet_total_balance(row: object) -> int:
    return int(_row_value(row, "onchain_balance_sat", 0)) + int(_row_value(row, "lightning_balance_sat", 0))


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


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
    await _event_bus.publish("escrow.funded", payload)


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


async def _scan_escrow_funding(escrow_row: object) -> FundingObservation | None:
    if _bitcoin_rpc_client is None:
        return None

    try:
        return await asyncio.to_thread(
            _bitcoin_rpc_client.scan_address,
            str(_row_value(escrow_row, "multisig_address", "")),
        )
    except BitcoinRPCError:
        logger.exception("Escrow funding check failed for trade %s", _row_value(escrow_row, "trade_id"))
        return None


async def _refresh_escrow_funding(
    conn: object,
    *,
    trade_row: object,
    escrow_row: object,
) -> tuple[object, object, bool]:
    if _row_value(escrow_row, "status") != "created" or _row_value(escrow_row, "funding_txid") is not None:
        return trade_row, escrow_row, False

    observation = await _scan_escrow_funding(escrow_row)
    if observation is None:
        return trade_row, escrow_row, False

    locked_amount_sat = int(_row_value(escrow_row, "locked_amount_sat", 0))
    if observation.total_amount_sat < locked_amount_sat:
        return trade_row, escrow_row, False

    updated_trade_row, updated_escrow_row = await mark_escrow_funded(
        conn,
        trade_id=_row_value(trade_row, "id"),
        funding_txid=observation.txid,
    )
    return updated_trade_row, updated_escrow_row, True


app = FastAPI(title="Marketplace Service", lifespan=_lifespan)


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

    try:
        claims = decode_token(
            credentials.credentials,
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

        order_row = await create_order(
            conn,
            user_id=principal.id,
            token_id=body.token_id,
            side=body.side,
            quantity=body.quantity,
            price_sat=body.price_sat,
        )

        published_trades: list[tuple[object, object, object, object]] = []

        while True:
            current_order = await get_order_by_id(conn, _row_value(order_row, "id"))
            if current_order is None or _remaining_quantity(current_order) <= 0:
                order_row = current_order or order_row
                break

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
            order_row = await get_order_by_id(conn, _row_value(order_row, "id")) or order_row

    for trade_row, escrow_row, buy_order, sell_order in published_trades:
        try:
            await _publish_trade_matched(
                trade_row,
                escrow_row=escrow_row,
                buy_order=buy_order,
                sell_order=sell_order,
            )
        except Exception:
            logger.exception("Trade event publish failed for trade %s", _row_value(trade_row, "id"))

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

        trade_row, escrow_row, funding_persisted = await _refresh_escrow_funding(
            conn,
            trade_row=trade_row,
            escrow_row=escrow_row,
        )

    if funding_persisted:
        try:
            await _publish_escrow_funded(
                trade_row,
                escrow_row=escrow_row,
                buy_order=buy_order,
                sell_order=sell_order,
            )
        except Exception:
            logger.exception("Escrow funded event publish failed for trade %s", trade_id)

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


if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
