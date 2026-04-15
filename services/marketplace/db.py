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
from marketplace.escrow import compress_xonly_pubkey, derive_compressed_pubkey, generate_2of3_multisig_address


_OPEN_ORDER_STATUSES = ("open", "partially_filled")
_ESCROW_EXPIRATION = timedelta(hours=24)
settings = get_settings(service_name="marketplace", default_port=8003)


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
    quantity: int,
    price_sat: int,
) -> sa.engine.Row:
    now = _utc_now()
    result = await conn.execute(
        sa.insert(orders_table)
        .values(
            id=uuid.uuid4(),
            user_id=_as_uuid(user_id),
            token_id=_as_uuid(token_id),
            side=side,
            quantity=quantity,
            price_sat=price_sat,
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


def _platform_escrow_pubkey() -> str:
    secret = settings.wallet_encryption_key or settings.jwt_secret or settings.service_name
    return derive_compressed_pubkey(f"platform-escrow:{secret}".encode("utf-8"))


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
    seed_material = (
        b"user-escrow-pubkey:"
        + user_uuid.bytes
        + b":"
        + derivation_path.encode("utf-8")
        + b":"
        + seed_bytes
    )
    return derive_compressed_pubkey(seed_material)


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
    now = _utc_now()

    buyer_pubkey = await _resolve_escrow_pubkey(conn, buyer_id)
    seller_pubkey = await _resolve_escrow_pubkey(conn, seller_id)
    platform_pubkey = _platform_escrow_pubkey()
    multisig_address = generate_2of3_multisig_address(
        (buyer_pubkey, seller_pubkey, platform_pubkey),
        settings.bitcoin_network,
    )

    try:
        await decrement_token_balance(conn, user_id=seller_id, token_id=token_id, quantity=quantity)
        await apply_order_fill(conn, order_row=buy_order, quantity=quantity)
        await apply_order_fill(conn, order_row=sell_order, quantity=quantity)

        trade_result = await conn.execute(
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
                multisig_address=multisig_address,
                buyer_pubkey=buyer_pubkey,
                seller_pubkey=seller_pubkey,
                platform_pubkey=platform_pubkey,
                locked_amount_sat=total_sat,
                funding_txid=None,
                release_txid=None,
                status="created",
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
) -> tuple[sa.engine.Row, sa.engine.Row]:
    now = _utc_now()

    try:
        escrow_result = await conn.execute(
            sa.update(escrows_table)
            .where(escrows_table.c.trade_id == _as_uuid(trade_id))
            .where(escrows_table.c.status == "created")
            .values(
                funding_txid=funding_txid,
                status="funded",
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


_SIGNATURE_THRESHOLD = 2


async def process_escrow_signature(
    conn: AsyncConnection,
    *,
    escrow_row: object,
    trade_row: object,
    buy_order: object,
    sell_order: object,
    signer_role: str,
    signature: str,
    platform_signature: str,
) -> tuple[sa.engine.Row, sa.engine.Row]:
    """Record a party's signature and the platform counter-signature.

    If the 2-of-3 threshold is satisfied the escrow is released and the
    trade is settled atomically: buyer receives tokens, seller is credited
    with the sale proceeds, and the buyer's wallet is debited.

    Returns ``(trade_row, escrow_row)`` reflecting the final state.
    """
    escrow_id = _as_uuid(_row_value(escrow_row, "id"))
    trade_id = _as_uuid(_row_value(trade_row, "id"))
    now = _utc_now()

    existing: dict = _row_value(escrow_row, "collected_signatures") or {}
    updated_sigs = dict(existing)
    updated_sigs[signer_role] = signature
    updated_sigs["platform"] = platform_signature

    threshold_met = len(updated_sigs) >= _SIGNATURE_THRESHOLD

    try:
        if threshold_met:
            release_txid = _generate_release_txid(escrow_id, trade_id)

            escrow_result = await conn.execute(
                sa.update(escrows_table)
                .where(escrows_table.c.id == escrow_id)
                .where(escrows_table.c.status == "funded")
                .values(
                    collected_signatures=updated_sigs,
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

            buyer_id = _row_value(buy_order, "user_id")
            seller_id = _row_value(sell_order, "user_id")
            token_id = _row_value(trade_row, "token_id")
            quantity = int(_row_value(trade_row, "quantity", 0))
            total_sat = int(_row_value(trade_row, "total_sat", 0))
            fee_sat = int(_row_value(trade_row, "fee_sat", 0))

            buyer_wallet = await get_wallet_by_user_id(conn, buyer_id)
            seller_wallet = await get_wallet_by_user_id(conn, seller_id)
            if buyer_wallet is None or seller_wallet is None:
                raise LookupError("wallet_not_found")

            await debit_wallet_balance(conn, wallet_row=buyer_wallet, amount_sat=total_sat + fee_sat)
            await credit_wallet_balance(conn, wallet_row=seller_wallet, amount_sat=total_sat)
            await increment_token_balance(conn, user_id=buyer_id, token_id=token_id, quantity=quantity)
            await record_trade_fee_income(conn, trade_row=trade_row)
        else:
            escrow_result = await conn.execute(
                sa.update(escrows_table)
                .where(escrows_table.c.id == escrow_id)
                .where(escrows_table.c.status == "funded")
                .values(
                    collected_signatures=updated_sigs,
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

    return trade_row, escrow_row


def _generate_release_txid(escrow_id: uuid.UUID, trade_id: uuid.UUID) -> str:
    import hashlib
    import time as _time

    payload = f"release:{escrow_id}:{trade_id}:{_time.time_ns()}".encode()
    return hashlib.sha256(payload).hexdigest()


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
) -> tuple[sa.engine.Row, sa.engine.Row, sa.engine.Row]:
    """Resolve an open dispute.

    ``resolution`` must be ``'release'`` or ``'refund'``.

    * ``release``: debit buyer wallet, credit seller wallet, transfer tokens to
      buyer, set escrow to ``released``, trade to ``settled``.
    * ``refund``: return locked tokens to seller, set escrow to ``refunded``,
      trade to ``settled``.

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

        buyer_id = _row_value(buy_order, "user_id")
        seller_id = _row_value(sell_order, "user_id")
        token_id = _row_value(trade_row, "token_id")
        quantity = int(_row_value(trade_row, "quantity", 0))
        total_sat = int(_row_value(trade_row, "total_sat", 0))
        fee_sat = int(_row_value(trade_row, "fee_sat", 0))

        if resolution == "release":
            # Transfer value: buyer pays sats, seller delivers tokens to buyer
            buyer_wallet = await get_wallet_by_user_id(conn, buyer_id)
            seller_wallet = await get_wallet_by_user_id(conn, seller_id)
            if buyer_wallet is None or seller_wallet is None:
                raise LookupError("wallet_not_found")

            await debit_wallet_balance(conn, wallet_row=buyer_wallet, amount_sat=total_sat + fee_sat)
            await credit_wallet_balance(conn, wallet_row=seller_wallet, amount_sat=total_sat)
            await increment_token_balance(conn, user_id=buyer_id, token_id=token_id, quantity=quantity)

            new_escrow_status = "released"
        else:
            # Refund: return locked tokens to seller (tokens were decremented at escrow creation)
            await increment_token_balance(conn, user_id=seller_id, token_id=token_id, quantity=quantity)
            new_escrow_status = "refunded"

        # Update escrow status
        escrow_update_result = await conn.execute(
            sa.update(escrows_table)
            .where(escrows_table.c.trade_id == trade_id_uuid)
            .where(escrows_table.c.status == "disputed")
            .values(status=new_escrow_status, updated_at=now)
            .returning(escrows_table)
        )
        escrow_row = escrow_update_result.fetchone()
        if escrow_row is None:
            raise LookupError("escrow_update_failed")

        # Update trade status
        trade_update_result = await conn.execute(
            sa.update(trades_table)
            .where(trades_table.c.id == trade_id_uuid)
            .values(status="settled", settled_at=now)
            .returning(trades_table)
        )
        trade_row = trade_update_result.fetchone()
        if trade_row is None:
            raise LookupError("trade_update_failed")
        if resolution == "release":
            await record_trade_fee_income(conn, trade_row=trade_row)

        # Resolve the dispute record
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
