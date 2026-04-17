from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_settings
from common import build_platform_signer
from common.custody import derive_platform_signing_material, derive_wallet_escrow_material
from common.db.metadata import escrows as escrows_table
from common.db.metadata import nostr_identities as nostr_identities_table
from common.db.metadata import orders as orders_table
from common.db.metadata import token_balances as token_balances_table
from common.db.metadata import tokens as tokens_table
from common.db.metadata import treasury as treasury_table
from common.db.metadata import trades as trades_table
from common.db.metadata import users as users_table
from common.db.metadata import wallets as wallets_table
from common.db.metadata import disputes as disputes_table
from marketplace.escrow import build_liquid_2of3_escrow, compress_xonly_pubkey, derive_compressed_pubkey


_OPEN_ORDER_STATUSES = ("open", "partially_filled")
_ESCROW_EXPIRATION = timedelta(hours=24)
settings = get_settings(service_name="marketplace", default_port=8003)
_ESCROW_FEE_RESERVE_SAT = max(int(settings.marketplace_escrow_fee_reserve_sat), 0)


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _row_value(row: object, key: str, default: object | None = None):
    if isinstance(row, dict):
        return row.get(key, default)

    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, default)


def _remaining_quantity(row: object) -> int:
    quantity = int(_row_value(row, "quantity", 0))
    filled_quantity = int(_row_value(row, "filled_quantity", 0))
    return max(quantity - filled_quantity, 0)


def _validate_trade_inputs(
    *,
    buy_order: object,
    sell_order: object,
    quantity: int,
    price_sat: int,
) -> None:
    if quantity <= 0:
        raise ValueError("quantity_must_be_positive")
    if price_sat <= 0:
        raise ValueError("price_sat_must_be_positive")
    if _row_value(buy_order, "side") != "buy" or _row_value(sell_order, "side") != "sell":
        raise ValueError("invalid_order_side")
    if _row_value(buy_order, "token_id") != _row_value(sell_order, "token_id"):
        raise ValueError("token_mismatch")
    if quantity > _remaining_quantity(buy_order) or quantity > _remaining_quantity(sell_order):
        raise ValueError("order_insufficient_quantity")


async def get_user_by_id(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(users_table).where(users_table.c.id == _as_uuid(user_id))
    )
    return result.fetchone()


async def get_wallet_by_user_id(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(wallets_table).where(wallets_table.c.user_id == _as_uuid(user_id))
    )
    return result.fetchone()


async def get_nostr_identity_by_user_id(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(nostr_identities_table)
        .where(nostr_identities_table.c.user_id == _as_uuid(user_id))
        .order_by(nostr_identities_table.c.created_at.asc(), nostr_identities_table.c.id.asc())
        .limit(1)
    )
    return result.fetchone()


async def get_token_by_id(
    conn: AsyncConnection,
    token_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(tokens_table).where(tokens_table.c.id == _as_uuid(token_id))
    )
    return result.fetchone()


async def get_token_balance_for_user(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
    token_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(token_balances_table)
        .where(token_balances_table.c.user_id == _as_uuid(user_id))
        .where(token_balances_table.c.token_id == _as_uuid(token_id))
    )
    return result.fetchone()


async def get_reserved_sell_quantity(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
    token_id: str | uuid.UUID,
) -> int:
    remaining = orders_table.c.quantity - orders_table.c.filled_quantity
    stmt = (
        sa.select(sa.func.coalesce(sa.func.sum(remaining), 0))
        .select_from(orders_table)
        .where(orders_table.c.user_id == _as_uuid(user_id))
        .where(orders_table.c.token_id == _as_uuid(token_id))
        .where(orders_table.c.side == "sell")
        .where(orders_table.c.status.in_(_OPEN_ORDER_STATUSES))
    )
    result = await conn.execute(stmt)
    return int(result.scalar_one())


async def get_reserved_buy_commitment(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> int:
    remaining_commitment = (orders_table.c.quantity - orders_table.c.filled_quantity) * orders_table.c.price_sat
    stmt = (
        sa.select(sa.func.coalesce(sa.func.sum(remaining_commitment), 0))
        .select_from(orders_table)
        .where(orders_table.c.user_id == _as_uuid(user_id))
        .where(orders_table.c.side == "buy")
        .where(orders_table.c.status.in_(_OPEN_ORDER_STATUSES))
    )
    result = await conn.execute(stmt)
    return int(result.scalar_one())


async def create_order(
    conn: AsyncConnection,
    *,
    user_id: str | uuid.UUID,
    token_id: str | uuid.UUID,
    side: str,
    order_type: str,
    quantity: int,
    price_sat: int,
    trigger_price_sat: int | None = None,
    triggered_at: datetime | None = None,
) -> sa.engine.Row:
    now = _utc_now()
    result = await conn.execute(
        sa.insert(orders_table)
        .values(
            id=uuid.uuid4(),
            user_id=_as_uuid(user_id),
            token_id=_as_uuid(token_id),
            side=side,
            order_type=order_type,
            quantity=quantity,
            price_sat=price_sat,
            trigger_price_sat=trigger_price_sat,
            triggered_at=triggered_at,
            filled_quantity=0,
            status="open",
            created_at=now,
            updated_at=now,
        )
        .returning(orders_table)
    )
    row = result.fetchone()
    await conn.commit()
    assert row is not None
    return row


async def get_order_by_id(
    conn: AsyncConnection,
    order_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(orders_table).where(orders_table.c.id == _as_uuid(order_id))
    )
    return result.fetchone()


async def list_orders(
    conn: AsyncConnection,
    *,
    token_id: str | uuid.UUID | None = None,
    side: str | None = None,
    status: str | None = None,
) -> list[sa.engine.Row]:
    stmt = sa.select(orders_table)

    if token_id is not None:
        stmt = stmt.where(orders_table.c.token_id == _as_uuid(token_id))
    if side is not None:
        stmt = stmt.where(orders_table.c.side == side)
    if status is not None:
        stmt = stmt.where(orders_table.c.status == status)

    stmt = stmt.order_by(orders_table.c.created_at.desc(), orders_table.c.id.desc())
    result = await conn.execute(stmt)
    return result.fetchall()


async def list_trades(
    conn: AsyncConnection,
    *,
    token_id: str | uuid.UUID | None = None,
) -> list[sa.engine.Row]:
    order_column = sa.func.coalesce(trades_table.c.settled_at, trades_table.c.created_at)
    stmt = sa.select(trades_table)

    if token_id is not None:
        stmt = stmt.where(trades_table.c.token_id == _as_uuid(token_id))

    stmt = stmt.order_by(order_column.desc(), trades_table.c.id.desc())
    result = await conn.execute(stmt)
    return result.fetchall()


async def get_latest_treasury_entry(conn: AsyncConnection) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(treasury_table)
        .order_by(treasury_table.c.created_at.desc(), treasury_table.c.id.desc())
        .limit(1)
    )
    return result.fetchone()


def _treasury_balance_delta(*, entry_type: str, amount_sat: int) -> int:
    if amount_sat <= 0:
        raise ValueError("treasury_amount_must_be_positive")

    if entry_type == "fee_income":
        return amount_sat
    if entry_type == "disbursement":
        return -amount_sat
    if entry_type == "adjustment":
        return amount_sat
    raise ValueError("invalid_treasury_entry_type")


async def create_treasury_entry(
    conn: AsyncConnection,
    *,
    entry_type: str,
    amount_sat: int,
    description: str | None = None,
    source_trade_id: str | uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> sa.engine.Row:
    latest_entry = await get_latest_treasury_entry(conn)
    current_balance = int(_row_value(latest_entry, "balance_after_sat", 0))
    balance_after_sat = current_balance + _treasury_balance_delta(
        entry_type=entry_type,
        amount_sat=amount_sat,
    )
    timestamp = created_at or _utc_now()

    result = await conn.execute(
        sa.insert(treasury_table)
        .values(
            id=uuid.uuid4(),
            source_trade_id=None if source_trade_id is None else _as_uuid(source_trade_id),
            type=entry_type,
            amount_sat=amount_sat,
            balance_after_sat=balance_after_sat,
            description=description,
            created_at=timestamp,
        )
        .returning(treasury_table)
    )
    row = result.fetchone()
    assert row is not None
    return row


async def record_trade_fee_income(
    conn: AsyncConnection,
    *,
    trade_row: object,
) -> sa.engine.Row | None:
    fee_sat = int(_row_value(trade_row, "fee_sat", 0))
    if fee_sat <= 0:
        return None

    trade_id = _as_uuid(_row_value(trade_row, "id"))
    existing_result = await conn.execute(
        sa.select(treasury_table)
        .where(treasury_table.c.type == "fee_income")
        .where(treasury_table.c.source_trade_id == trade_id)
        .limit(1)
    )
    existing_entry = existing_result.fetchone()
    if existing_entry is not None:
        return existing_entry

    settled_at = _row_value(trade_row, "settled_at")
    return await create_treasury_entry(
        conn,
        entry_type="fee_income",
        amount_sat=fee_sat,
        source_trade_id=trade_id,
        description=f"Fee income from trade {trade_id}",
        created_at=settled_at if isinstance(settled_at, datetime) else None,
    )


async def get_trade_by_id(
    conn: AsyncConnection,
    trade_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(trades_table).where(trades_table.c.id == _as_uuid(trade_id))
    )
    return result.fetchone()


async def get_escrow_by_trade_id(
    conn: AsyncConnection,
    trade_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(escrows_table).where(escrows_table.c.trade_id == _as_uuid(trade_id))
    )
    return result.fetchone()


async def list_escrows_by_status(
    conn: AsyncConnection,
    *,
    statuses: tuple[str, ...],
) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(escrows_table)
        .where(escrows_table.c.status.in_(statuses))
        .order_by(escrows_table.c.created_at.asc(), escrows_table.c.id.asc())
    )
    return list(result.fetchall())


async def cancel_order(
    conn: AsyncConnection,
    *,
    order_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.update(orders_table)
        .where(orders_table.c.id == _as_uuid(order_id))
        .where(orders_table.c.user_id == _as_uuid(user_id))
        .where(orders_table.c.status.in_(_OPEN_ORDER_STATUSES))
        .values(
            status="cancelled",
            updated_at=_utc_now(),
        )
        .returning(orders_table)
    )
    row = result.fetchone()
    await conn.commit()
    return row


async def find_best_match(
    conn: AsyncConnection,
    *,
    token_id: str | uuid.UUID,
    incoming_side: str,
    incoming_price: int,
    requester_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    stmt = sa.select(orders_table).where(orders_table.c.token_id == _as_uuid(token_id))
    stmt = stmt.where(orders_table.c.status.in_(_OPEN_ORDER_STATUSES))
    stmt = stmt.where(orders_table.c.user_id != _as_uuid(requester_id))
    stmt = stmt.where(orders_table.c.quantity > orders_table.c.filled_quantity)
    stmt = stmt.where(
        sa.or_(
            orders_table.c.order_type == "limit",
            orders_table.c.triggered_at.is_not(None),
        )
    )

    if incoming_side == "buy":
        stmt = stmt.where(orders_table.c.side == "sell")
        stmt = stmt.where(orders_table.c.price_sat <= incoming_price)
        stmt = stmt.order_by(
            orders_table.c.price_sat.asc(),
            orders_table.c.created_at.asc(),
            orders_table.c.id.asc(),
        )
    else:
        stmt = stmt.where(orders_table.c.side == "buy")
        stmt = stmt.where(orders_table.c.price_sat >= incoming_price)
        stmt = stmt.order_by(
            orders_table.c.price_sat.desc(),
            orders_table.c.created_at.asc(),
            orders_table.c.id.asc(),
        )

    result = await conn.execute(stmt.limit(1))
    return result.fetchone()


async def get_last_trade_price_for_token(
    conn: AsyncConnection,
    token_id: str | uuid.UUID,
) -> int | None:
    result = await conn.execute(
        sa.select(trades_table.c.price_sat)
        .where(trades_table.c.token_id == _as_uuid(token_id))
        .where(trades_table.c.status == "settled")
        .order_by(
            sa.func.coalesce(trades_table.c.settled_at, trades_table.c.created_at).desc(),
            trades_table.c.id.desc(),
        )
        .limit(1)
    )
    value = result.scalar_one_or_none()
    return None if value is None else int(value)


async def get_reference_price_for_token(
    conn: AsyncConnection,
    token_id: str | uuid.UUID,
) -> int | None:
    result = await conn.execute(
        sa.select(trades_table.c.price_sat)
        .where(trades_table.c.token_id == _as_uuid(token_id))
        .order_by(
            sa.func.coalesce(trades_table.c.settled_at, trades_table.c.created_at).desc(),
            trades_table.c.id.desc(),
        )
        .limit(1)
    )
    price = result.scalar_one_or_none()
    if price is not None:
        return int(price)

    token_result = await conn.execute(
        sa.select(tokens_table.c.unit_price_sat)
        .where(tokens_table.c.id == _as_uuid(token_id))
        .limit(1)
    )
    token_price = token_result.scalar_one_or_none()
    return None if token_price is None else int(token_price)


async def activate_triggered_orders(
    conn: AsyncConnection,
    *,
    token_id: str | uuid.UUID,
    reference_price: int,
) -> list[sa.engine.Row]:
    now = _utc_now()
    trigger_condition = sa.or_(
        sa.and_(
            orders_table.c.side == "buy",
            orders_table.c.trigger_price_sat <= reference_price,
        ),
        sa.and_(
            orders_table.c.side == "sell",
            orders_table.c.trigger_price_sat >= reference_price,
        ),
    )
    result = await conn.execute(
        sa.update(orders_table)
        .where(orders_table.c.token_id == _as_uuid(token_id))
        .where(orders_table.c.status.in_(_OPEN_ORDER_STATUSES))
        .where(orders_table.c.order_type == "stop_limit")
        .where(orders_table.c.triggered_at.is_(None))
        .where(trigger_condition)
        .values(triggered_at=now, updated_at=now)
        .returning(orders_table)
    )
    rows = result.fetchall()
    if rows:
        await conn.commit()
    return rows


async def get_trade_volume_24h(
    conn: AsyncConnection,
    token_id: str | uuid.UUID,
) -> int:
    since = _utc_now() - timedelta(hours=24)
    result = await conn.execute(
        sa.select(sa.func.coalesce(sa.func.sum(trades_table.c.quantity), 0))
        .where(trades_table.c.token_id == _as_uuid(token_id))
        .where(trades_table.c.status == "settled")
        .where(sa.func.coalesce(trades_table.c.settled_at, trades_table.c.created_at) >= since)
    )
    return int(result.scalar_one())


async def debit_wallet_balance(
    conn: AsyncConnection,
    *,
    wallet_row: object,
    amount_sat: int,
) -> None:
    onchain_balance = int(_row_value(wallet_row, "onchain_balance_sat", 0))
    lightning_balance = int(_row_value(wallet_row, "lightning_balance_sat", 0))
    total_balance = onchain_balance + lightning_balance

    if total_balance < amount_sat:
        raise ValueError("insufficient_wallet_balance")

    onchain_debit = min(onchain_balance, amount_sat)
    lightning_debit = amount_sat - onchain_debit

    await conn.execute(
        sa.update(wallets_table)
        .where(wallets_table.c.id == _row_value(wallet_row, "id"))
        .values(
            onchain_balance_sat=onchain_balance - onchain_debit,
            lightning_balance_sat=lightning_balance - lightning_debit,
            updated_at=_utc_now(),
        )
    )


async def credit_wallet_balance(
    conn: AsyncConnection,
    *,
    wallet_row: object,
    amount_sat: int,
) -> None:
    current_onchain = int(_row_value(wallet_row, "onchain_balance_sat", 0))
    await conn.execute(
        sa.update(wallets_table)
        .where(wallets_table.c.id == _row_value(wallet_row, "id"))
        .values(
            onchain_balance_sat=current_onchain + amount_sat,
            updated_at=_utc_now(),
        )
    )


async def decrement_token_balance(
    conn: AsyncConnection,
    *,
    user_id: str | uuid.UUID,
    token_id: str | uuid.UUID,
    quantity: int,
) -> None:
    result = await conn.execute(
        sa.update(token_balances_table)
        .where(token_balances_table.c.user_id == _as_uuid(user_id))
        .where(token_balances_table.c.token_id == _as_uuid(token_id))
        .where(token_balances_table.c.balance >= quantity)
        .values(
            balance=token_balances_table.c.balance - quantity,
            updated_at=_utc_now(),
        )
        .returning(token_balances_table.c.id)
    )
    if result.fetchone() is None:
        raise ValueError("insufficient_token_balance")


async def increment_token_balance(
    conn: AsyncConnection,
    *,
    user_id: str | uuid.UUID,
    token_id: str | uuid.UUID,
    quantity: int,
) -> None:
    now = _utc_now()
    stmt = pg_insert(token_balances_table).values(
        id=uuid.uuid4(),
        user_id=_as_uuid(user_id),
        token_id=_as_uuid(token_id),
        balance=quantity,
        updated_at=now,
    )
    await conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[token_balances_table.c.user_id, token_balances_table.c.token_id],
            set_={
                "balance": token_balances_table.c.balance + quantity,
                "updated_at": now,
            },
        )
    )


async def apply_order_fill(
    conn: AsyncConnection,
    *,
    order_row: object,
    quantity: int,
) -> None:
    if quantity <= 0:
        raise ValueError("quantity_must_be_positive")

    current_filled = int(_row_value(order_row, "filled_quantity", 0))
    total_quantity = int(_row_value(order_row, "quantity", 0))
    new_filled = min(current_filled + quantity, total_quantity)
    if new_filled <= current_filled:
        raise ValueError("order_already_filled")

    new_status = "filled" if new_filled == total_quantity else "partially_filled"

    await conn.execute(
        sa.update(orders_table)
        .where(orders_table.c.id == _row_value(order_row, "id"))
        .values(
            filled_quantity=new_filled,
            status=new_status,
            updated_at=_utc_now(),
        )
    )


async def revert_order_fill(
    conn: AsyncConnection,
    *,
    order_row: object,
    quantity: int,
) -> None:
    if quantity <= 0:
        raise ValueError("quantity_must_be_positive")

    current_filled = int(_row_value(order_row, "filled_quantity", 0))
    new_filled = max(current_filled - quantity, 0)
    if new_filled == 0:
        new_status = "open"
    elif new_filled < int(_row_value(order_row, "quantity", 0)):
        new_status = "partially_filled"
    else:
        new_status = "filled"

    await conn.execute(
        sa.update(orders_table)
        .where(orders_table.c.id == _row_value(order_row, "id"))
        .values(
            filled_quantity=new_filled,
            status=new_status,
            updated_at=_utc_now(),
        )
    )


def _platform_escrow_pubkey() -> str:
    signer = build_platform_signer(settings)
    public_key = signer.public_key()
    if not public_key:
        raise RuntimeError("platform_signer_public_key_unavailable")
    return public_key


async def _resolve_escrow_pubkey(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> str:
    nostr_identity = await get_nostr_identity_by_user_id(conn, user_id)
    if nostr_identity is not None:
        return compress_xonly_pubkey(str(_row_value(nostr_identity, "pubkey", "")))

    wallet_row = await get_wallet_by_user_id(conn, user_id)
    if wallet_row is None:
        raise LookupError("wallet_not_found")

    seed_bytes = bytes(_row_value(wallet_row, "encrypted_seed", b""))
    if not seed_bytes:
        raise LookupError("wallet_not_found")

    derivation_path = str(_row_value(wallet_row, "derivation_path", ""))
    user_uuid = _as_uuid(user_id)
    seed_material = derive_wallet_escrow_material(
        user_id=user_uuid,
        derivation_path=derivation_path,
        encrypted_seed=seed_bytes,
    )
    return derive_compressed_pubkey(seed_material)


async def resolve_escrow_signing_material(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> bytes | None:
    nostr_identity = await get_nostr_identity_by_user_id(conn, user_id)
    if nostr_identity is not None:
        return None

    wallet_row = await get_wallet_by_user_id(conn, user_id)
    if wallet_row is None:
        raise LookupError("wallet_not_found")

    seed_bytes = bytes(_row_value(wallet_row, "encrypted_seed", b""))
    if not seed_bytes:
        raise LookupError("wallet_not_found")

    derivation_path = str(_row_value(wallet_row, "derivation_path", ""))
    return derive_wallet_escrow_material(
        user_id=_as_uuid(user_id),
        derivation_path=derivation_path,
        encrypted_seed=seed_bytes,
    )


async def update_escrow_settlement_metadata(
    conn: AsyncConnection,
    *,
    escrow_id: str | uuid.UUID,
    settlement_metadata: dict,
) -> sa.engine.Row:
    result = await conn.execute(
        sa.update(escrows_table)
        .where(escrows_table.c.id == _as_uuid(escrow_id))
        .values(settlement_metadata=settlement_metadata, updated_at=_utc_now())
        .returning(escrows_table)
    )
    row = result.fetchone()
    if row is None:
        raise LookupError("escrow_not_found")
    await conn.commit()
    return row


def _merge_settlement_metadata(existing: dict | None, updates: dict | None) -> dict:
    merged = dict(existing or {})
    if updates:
        merged.update(updates)
    return merged


def _updated_signature_payload(
    existing: dict | None,
    *,
    signature_path: str,
    signer_role: str,
    signature_record: dict,
) -> dict:
    payload = dict(existing or {})
    path_payload = dict(payload.get(signature_path) or {})
    path_payload[signer_role] = signature_record
    payload[signature_path] = path_payload
    return payload


async def create_trade_escrow(
    conn: AsyncConnection,
    *,
    buy_order: object,
    sell_order: object,
    quantity: int,
    price_sat: int,
    fee_sat: int = 0,
) -> tuple[sa.engine.Row, sa.engine.Row]:
    _validate_trade_inputs(
        buy_order=buy_order,
        sell_order=sell_order,
        quantity=quantity,
        price_sat=price_sat,
    )

    buyer_id = _row_value(buy_order, "user_id")
    seller_id = _row_value(sell_order, "user_id")
    token_id = _row_value(buy_order, "token_id")
    total_sat = quantity * price_sat
    locked_amount_sat = total_sat + int(fee_sat) + _ESCROW_FEE_RESERVE_SAT
    now = _utc_now()
    trade_id = uuid.uuid4()

    buyer_pubkey = await _resolve_escrow_pubkey(conn, buyer_id)
    seller_pubkey = await _resolve_escrow_pubkey(conn, seller_id)
    platform_pubkey = _platform_escrow_pubkey()
    escrow_details = build_liquid_2of3_escrow(
        (buyer_pubkey, seller_pubkey, platform_pubkey),
        settings.elements_network,
        blinding_seed=derive_platform_signing_material(settings, purpose=f"escrow-blinding:{trade_id}"),
    )

    try:
        await decrement_token_balance(conn, user_id=seller_id, token_id=token_id, quantity=quantity)
        await apply_order_fill(conn, order_row=buy_order, quantity=quantity)
        await apply_order_fill(conn, order_row=sell_order, quantity=quantity)

        trade_result = await conn.execute(
            sa.insert(trades_table)
            .values(
                id=trade_id,
                buy_order_id=_row_value(buy_order, "id"),
                sell_order_id=_row_value(sell_order, "id"),
                token_id=token_id,
                quantity=quantity,
                price_sat=price_sat,
                total_sat=total_sat,
                fee_sat=fee_sat,
                status="pending",
                created_at=now,
                settled_at=None,
            )
            .returning(trades_table)
        )
        trade_row = trade_result.fetchone()
        assert trade_row is not None

        escrow_result = await conn.execute(
            sa.insert(escrows_table)
            .values(
                id=uuid.uuid4(),
                trade_id=_row_value(trade_row, "id"),
                multisig_address=escrow_details.confidential_address,
                buyer_pubkey=buyer_pubkey,
                seller_pubkey=seller_pubkey,
                platform_pubkey=platform_pubkey,
                locked_amount_sat=locked_amount_sat,
                funding_txid=None,
                release_txid=None,
                refund_txid=None,
                status="created",
                settlement_metadata={
                    "unconfidential_address": escrow_details.unconfidential_address,
                    "witness_script": escrow_details.witness_script_hex,
                    "script_pubkey": escrow_details.script_pubkey_hex,
                    "blinding_pubkey": escrow_details.blinding_pubkey,
                    "blinding_private_key": escrow_details.blinding_private_key,
                    "seller_payout_amount_sat": total_sat,
                    "marketplace_fee_amount_sat": int(fee_sat),
                    "fee_reserve_sat": _ESCROW_FEE_RESERVE_SAT,
                    "funding_amount_sat": locked_amount_sat,
                },
                expires_at=now + _ESCROW_EXPIRATION,
                created_at=now,
                updated_at=now,
            )
            .returning(escrows_table)
        )
        escrow_row = escrow_result.fetchone()
        assert escrow_row is not None

        await conn.commit()
    except Exception:
        await conn.rollback()
        raise

    return trade_row, escrow_row


async def settle_trade(
    conn: AsyncConnection,
    *,
    buy_order: object,
    sell_order: object,
    quantity: int,
    price_sat: int,
    fee_sat: int = 0,
) -> sa.engine.Row:
    _validate_trade_inputs(
        buy_order=buy_order,
        sell_order=sell_order,
        quantity=quantity,
        price_sat=price_sat,
    )

    buyer_id = _row_value(buy_order, "user_id")
    seller_id = _row_value(sell_order, "user_id")
    token_id = _row_value(buy_order, "token_id")
    total_sat = quantity * price_sat
    now = _utc_now()

    try:
        buyer_wallet = await get_wallet_by_user_id(conn, buyer_id)
        seller_wallet = await get_wallet_by_user_id(conn, seller_id)
        if buyer_wallet is None or seller_wallet is None:
            raise LookupError("wallet_not_found")

        await debit_wallet_balance(conn, wallet_row=buyer_wallet, amount_sat=total_sat + fee_sat)
        await credit_wallet_balance(conn, wallet_row=seller_wallet, amount_sat=total_sat)
        await decrement_token_balance(conn, user_id=seller_id, token_id=token_id, quantity=quantity)
        await increment_token_balance(conn, user_id=buyer_id, token_id=token_id, quantity=quantity)
        await apply_order_fill(conn, order_row=buy_order, quantity=quantity)
        await apply_order_fill(conn, order_row=sell_order, quantity=quantity)

        result = await conn.execute(
            sa.insert(trades_table)
            .values(
                id=uuid.uuid4(),
                buy_order_id=_row_value(buy_order, "id"),
                sell_order_id=_row_value(sell_order, "id"),
                token_id=token_id,
                quantity=quantity,
                price_sat=price_sat,
                total_sat=total_sat,
                fee_sat=fee_sat,
                status="settled",
                created_at=now,
                settled_at=now,
            )
            .returning(trades_table)
        )
        trade_row = result.fetchone()
        assert trade_row is not None
        await record_trade_fee_income(conn, trade_row=trade_row)
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise

    return trade_row


async def mark_escrow_funded(
    conn: AsyncConnection,
    *,
    trade_id: str | uuid.UUID,
    funding_txid: str,
    settlement_metadata_update: dict | None = None,
) -> tuple[sa.engine.Row, sa.engine.Row]:
    now = _utc_now()
    settlement_metadata = settlement_metadata_update or {}

    try:
        escrow_result = await conn.execute(
            sa.update(escrows_table)
            .where(escrows_table.c.trade_id == _as_uuid(trade_id))
            .where(escrows_table.c.status == "created")
            .values(
                funding_txid=funding_txid,
                status="funded",
                settlement_metadata=escrows_table.c.settlement_metadata.op("||")(sa.cast(settlement_metadata, sa.JSON)),
                updated_at=now,
            )
            .returning(escrows_table)
        )
        escrow_row = escrow_result.fetchone()
        if escrow_row is None:
            raise LookupError("escrow_not_found")

        trade_result = await conn.execute(
            sa.update(trades_table)
            .where(trades_table.c.id == _as_uuid(trade_id))
            .values(status="escrowed")
            .returning(trades_table)
        )
        trade_row = trade_result.fetchone()
        if trade_row is None:
            raise LookupError("trade_not_found")

        await conn.commit()
    except Exception:
        await conn.rollback()
        raise

    return trade_row, escrow_row


async def record_escrow_signature(
    conn: AsyncConnection,
    *,
    escrow_row: object,
    signer_role: str,
    signature_path: str,
    signature_record: dict,
    settlement_metadata: dict | None = None,
) -> sa.engine.Row:
    escrow_id = _as_uuid(_row_value(escrow_row, "id"))
    now = _utc_now()
    existing_signatures = _row_value(escrow_row, "collected_signatures") or {}
    updated_signatures = _updated_signature_payload(
        existing_signatures,
        signature_path=signature_path,
        signer_role=signer_role,
        signature_record=signature_record,
    )
    updated_metadata = _merge_settlement_metadata(
        _row_value(escrow_row, "settlement_metadata") or {},
        settlement_metadata,
    )
    next_status = "inspection_pending" if _row_value(escrow_row, "status") == "funded" else _row_value(escrow_row, "status")

    try:
        escrow_result = await conn.execute(
            sa.update(escrows_table)
            .where(escrows_table.c.id == escrow_id)
            .where(escrows_table.c.status.in_(("funded", "inspection_pending")))
            .values(
                collected_signatures=updated_signatures,
                settlement_metadata=updated_metadata,
                status=next_status,
                updated_at=now,
            )
            .returning(escrows_table)
        )
        escrow_row = escrow_result.fetchone()
        if escrow_row is None:
            raise LookupError("escrow_not_found_or_state_conflict")
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise

    return escrow_row


async def process_escrow_signature(
    conn: AsyncConnection,
    *,
    escrow_row: object,
    trade_row: object,
    buy_order: object,
    sell_order: object,
    collected_signatures: dict,
    release_txid: str,
    settlement_metadata: dict | None = None,
) -> tuple[sa.engine.Row, sa.engine.Row]:
    escrow_id = _as_uuid(_row_value(escrow_row, "id"))
    trade_id = _as_uuid(_row_value(trade_row, "id"))
    now = _utc_now()
    updated_metadata = _merge_settlement_metadata(
        _row_value(escrow_row, "settlement_metadata") or {},
        settlement_metadata,
    )

    try:
        escrow_result = await conn.execute(
            sa.update(escrows_table)
            .where(escrows_table.c.id == escrow_id)
            .where(escrows_table.c.status.in_(("inspection_pending", "disputed")))
            .values(
                collected_signatures=collected_signatures,
                settlement_metadata=updated_metadata,
                release_txid=release_txid,
                status="released",
                updated_at=now,
            )
            .returning(escrows_table)
        )
        escrow_row = escrow_result.fetchone()
        if escrow_row is None:
            raise LookupError("escrow_not_found_or_state_conflict")

        trade_result = await conn.execute(
            sa.update(trades_table)
            .where(trades_table.c.id == trade_id)
            .values(status="settled", settled_at=now)
            .returning(trades_table)
        )
        trade_row = trade_result.fetchone()
        if trade_row is None:
            raise LookupError("trade_not_found")

        await increment_token_balance(
            conn,
            user_id=_row_value(buy_order, "user_id"),
            token_id=_row_value(trade_row, "token_id"),
            quantity=int(_row_value(trade_row, "quantity", 0)),
        )
        await record_trade_fee_income(conn, trade_row=trade_row)
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise

    return trade_row, escrow_row


async def expire_unfunded_escrow(
    conn: AsyncConnection,
    *,
    trade_row: object,
    escrow_row: object,
    buy_order: object,
    sell_order: object,
) -> tuple[sa.engine.Row, sa.engine.Row]:
    if _row_value(escrow_row, "status") != "created":
        raise LookupError("escrow_not_expirable")

    now = _utc_now()
    quantity = int(_row_value(trade_row, "quantity", 0))
    settlement_metadata = _merge_settlement_metadata(
        _row_value(escrow_row, "settlement_metadata") or {},
        {"expired_at": now.isoformat().replace("+00:00", "Z")},
    )

    try:
        await increment_token_balance(
            conn,
            user_id=_row_value(sell_order, "user_id"),
            token_id=_row_value(trade_row, "token_id"),
            quantity=quantity,
        )
        await revert_order_fill(conn, order_row=buy_order, quantity=quantity)
        await revert_order_fill(conn, order_row=sell_order, quantity=quantity)

        escrow_result = await conn.execute(
            sa.update(escrows_table)
            .where(escrows_table.c.id == _as_uuid(_row_value(escrow_row, "id")))
            .where(escrows_table.c.status == "created")
            .values(
                status="expired",
                settlement_metadata=settlement_metadata,
                updated_at=now,
            )
            .returning(escrows_table)
        )
        updated_escrow = escrow_result.fetchone()
        if updated_escrow is None:
            raise LookupError("escrow_not_found_or_state_conflict")

        trade_result = await conn.execute(
            sa.update(trades_table)
            .where(trades_table.c.id == _as_uuid(_row_value(trade_row, "id")))
            .where(trades_table.c.status == "pending")
            .values(status="cancelled")
            .returning(trades_table)
        )
        updated_trade = trade_result.fetchone()
        if updated_trade is None:
            raise LookupError("trade_not_found_or_state_conflict")

        await conn.commit()
    except Exception:
        await conn.rollback()
        raise

    return updated_trade, updated_escrow


async def get_dispute_by_trade_id(
    conn: AsyncConnection,
    trade_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(disputes_table).where(disputes_table.c.trade_id == _as_uuid(trade_id))
    )
    return result.fetchone()


async def open_dispute(
    conn: AsyncConnection,
    *,
    trade_id: str | uuid.UUID,
    opened_by: str | uuid.UUID,
    reason: str,
) -> sa.engine.Row:
    """Create a dispute for an escrowed trade.

    Sets the trade and its escrow to ``disputed`` status atomically, then
    inserts the dispute record.  Returns the new dispute row.
    """
    now = _utc_now()
    trade_id_uuid = _as_uuid(trade_id)

    try:
        trade_result = await conn.execute(
            sa.update(trades_table)
            .where(trades_table.c.id == trade_id_uuid)
            .where(trades_table.c.status == "escrowed")
            .values(status="disputed")
            .returning(trades_table)
        )
        if trade_result.fetchone() is None:
            raise LookupError("trade_not_found_or_state_conflict")

        escrow_result = await conn.execute(
            sa.update(escrows_table)
            .where(escrows_table.c.trade_id == trade_id_uuid)
            .where(escrows_table.c.status == "funded")
            .values(status="disputed", updated_at=now)
            .returning(escrows_table)
        )
        if escrow_result.fetchone() is None:
            raise LookupError("escrow_not_found_or_state_conflict")

        dispute_result = await conn.execute(
            sa.insert(disputes_table)
            .values(
                id=uuid.uuid4(),
                trade_id=trade_id_uuid,
                opened_by=_as_uuid(opened_by),
                reason=reason,
                status="open",
                resolution=None,
                resolved_by=None,
                resolved_at=None,
                created_at=now,
                updated_at=now,
            )
            .returning(disputes_table)
        )
        dispute_row = dispute_result.fetchone()
        assert dispute_row is not None

        await conn.commit()
    except Exception:
        await conn.rollback()
        raise

    return dispute_row


async def resolve_dispute(
    conn: AsyncConnection,
    *,
    trade_id: str | uuid.UUID,
    resolved_by: str | uuid.UUID,
    resolution: str,
    resolution_txid: str,
    collected_signatures: dict,
    settlement_metadata: dict | None = None,
) -> tuple[sa.engine.Row, sa.engine.Row, sa.engine.Row]:
    """Resolve an open dispute.

    ``resolution`` must be ``'release'`` or ``'refund'``.

    * ``release``: transfer tokens to buyer, set escrow to ``released``,
      trade to ``settled``, and persist the Liquid release txid.
    * ``refund``: return locked tokens to seller, set escrow to ``refunded``,
      trade to ``cancelled``, and persist the Liquid refund txid.

    Returns ``(dispute_row, trade_row, escrow_row)`` reflecting the final state.
    """
    if resolution not in ("release", "refund"):
        raise ValueError("invalid_resolution")

    now = _utc_now()
    trade_id_uuid = _as_uuid(trade_id)

    try:
        # Load trade
        trade_result = await conn.execute(
            sa.select(trades_table).where(trades_table.c.id == trade_id_uuid)
        )
        trade_row = trade_result.fetchone()
        if trade_row is None or _row_value(trade_row, "status") != "disputed":
            raise LookupError("trade_not_found_or_state_conflict")

        # Load escrow
        escrow_result = await conn.execute(
            sa.select(escrows_table).where(escrows_table.c.trade_id == trade_id_uuid)
        )
        escrow_row = escrow_result.fetchone()
        if escrow_row is None or _row_value(escrow_row, "status") != "disputed":
            raise LookupError("escrow_not_found_or_state_conflict")

        # Load orders to identify buyer and seller
        buy_order_result = await conn.execute(
            sa.select(orders_table).where(
                orders_table.c.id == _as_uuid(_row_value(trade_row, "buy_order_id"))
            )
        )
        buy_order = buy_order_result.fetchone()
        sell_order_result = await conn.execute(
            sa.select(orders_table).where(
                orders_table.c.id == _as_uuid(_row_value(trade_row, "sell_order_id"))
            )
        )
        sell_order = sell_order_result.fetchone()
        if buy_order is None or sell_order is None:
            raise LookupError("orders_not_found")

        seller_id = _row_value(sell_order, "user_id")
        token_id = _row_value(trade_row, "token_id")
        quantity = int(_row_value(trade_row, "quantity", 0))
        updated_metadata = _merge_settlement_metadata(
            _row_value(escrow_row, "settlement_metadata") or {},
            settlement_metadata,
        )

        if resolution == "release":
            await increment_token_balance(
                conn,
                user_id=_row_value(buy_order, "user_id"),
                token_id=token_id,
                quantity=quantity,
            )
            new_escrow_status = "released"
            trade_status = "settled"
        else:
            await increment_token_balance(conn, user_id=seller_id, token_id=token_id, quantity=quantity)
            new_escrow_status = "refunded"
            trade_status = "cancelled"

        escrow_update_result = await conn.execute(
            sa.update(escrows_table)
            .where(escrows_table.c.trade_id == trade_id_uuid)
            .where(escrows_table.c.status == "disputed")
            .values(
                status=new_escrow_status,
                release_txid=resolution_txid if resolution == "release" else escrows_table.c.release_txid,
                refund_txid=resolution_txid if resolution == "refund" else escrows_table.c.refund_txid,
                collected_signatures=collected_signatures,
                settlement_metadata=updated_metadata,
                updated_at=now,
            )
            .returning(escrows_table)
        )
        escrow_row = escrow_update_result.fetchone()
        if escrow_row is None:
            raise LookupError("escrow_update_failed")

        trade_update_result = await conn.execute(
            sa.update(trades_table)
            .where(trades_table.c.id == trade_id_uuid)
            .values(
                status=trade_status,
                settled_at=now if trade_status == "settled" else None,
            )
            .returning(trades_table)
        )
        trade_row = trade_update_result.fetchone()
        if trade_row is None:
            raise LookupError("trade_update_failed")
        if resolution == "release":
            await record_trade_fee_income(conn, trade_row=trade_row)

        dispute_update_result = await conn.execute(
            sa.update(disputes_table)
            .where(disputes_table.c.trade_id == trade_id_uuid)
            .where(disputes_table.c.status == "open")
            .values(
                status="resolved",
                resolution=resolution,
                resolved_by=_as_uuid(resolved_by),
                resolved_at=now,
                updated_at=now,
            )
            .returning(disputes_table)
        )
        dispute_row = dispute_update_result.fetchone()
        if dispute_row is None:
            raise LookupError("dispute_not_found_or_already_resolved")

        await conn.commit()
    except Exception:
        await conn.rollback()
        raise

    return dispute_row, trade_row, escrow_row
