"""Database helpers for the wallet service."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import secrets
import sys
import uuid

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db.metadata import transactions as transactions_table
from common.db.metadata import users as users_table
from common.db.metadata import wallets as wallets_table


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def get_user_by_id(
    conn: AsyncConnection,
    user_id: str,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(users_table).where(users_table.c.id == _as_uuid(user_id))
    )
    return result.fetchone()


async def get_wallet_by_user_id(
    conn: AsyncConnection,
    user_id: str,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(wallets_table).where(wallets_table.c.user_id == _as_uuid(user_id))
    )
    return result.fetchone()


async def get_or_create_wallet(
    conn: AsyncConnection,
    user_id: str,
) -> sa.engine.Row:
    existing = await get_wallet_by_user_id(conn, user_id)
    if existing is not None:
        return existing

    now = _utc_now()
    wallet_id = uuid.uuid4()
    user_uuid = _as_uuid(user_id)

    try:
        await conn.execute(
            sa.insert(wallets_table).values(
                id=wallet_id,
                user_id=user_uuid,
                onchain_balance_sat=0,
                lightning_balance_sat=0,
                encrypted_seed=secrets.token_bytes(32),
                derivation_path="m/86'/0'/0'",
                created_at=now,
                updated_at=now,
            )
        )
        await conn.commit()
    except IntegrityError:
        await conn.rollback()

    wallet = await get_wallet_by_user_id(conn, user_id)
    assert wallet is not None
    return wallet


async def create_onchain_withdrawal(
    conn: AsyncConnection,
    *,
    wallet_id: str,
    amount_sat: int,
    fee_sat: int,
    txid: str,
    description: str | None,
) -> sa.engine.Row | None:
    wallet_uuid = _as_uuid(wallet_id)
    now = _utc_now()
    total_cost = amount_sat + fee_sat

    updated_wallet = await conn.execute(
        sa.update(wallets_table)
        .where(wallets_table.c.id == wallet_uuid)
        .where(wallets_table.c.onchain_balance_sat >= total_cost)
        .values(
            onchain_balance_sat=wallets_table.c.onchain_balance_sat - total_cost,
            updated_at=now,
        )
        .returning(wallets_table.c.id)
    )
    if updated_wallet.fetchone() is None:
        await conn.rollback()
        return None

    result = await conn.execute(
        sa.insert(transactions_table)
        .values(
            id=uuid.uuid4(),
            wallet_id=wallet_uuid,
            type="withdrawal",
            amount_sat=amount_sat,
            direction="out",
            status="pending",
            txid=txid,
            description=description,
            created_at=now,
        )
        .returning(transactions_table)
    )
    row = result.fetchone()
    await conn.commit()
    assert row is not None
    return row


async def list_wallet_transactions(
    conn: AsyncConnection,
    wallet_id: str,
) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(transactions_table)
        .where(transactions_table.c.wallet_id == _as_uuid(wallet_id))
        .order_by(transactions_table.c.created_at.desc(), transactions_table.c.id.desc())
    )
    return list(result.fetchall())
