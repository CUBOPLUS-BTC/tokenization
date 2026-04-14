"""Database helpers for the wallet service."""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sys
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_settings
from common.db.metadata import assets as assets_table
from common.db.metadata import token_balances as token_balances_table
from common.db.metadata import tokens as tokens_table
from common.db.metadata import trades as trades_table
from common.db.metadata import transactions as transactions_table
from common.db.metadata import users as users_table
from common.db.metadata import wallets as wallets_table


os.environ.setdefault("TAPD_MACAROON_PATH", "")
os.environ.setdefault("TAPD_TLS_CERT_PATH", "")

settings = get_settings(service_name="wallet", default_port=8001)
_engine: AsyncEngine | None = None


def _make_async_url(sync_url: str) -> str:
    url = sync_url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(_make_async_url(settings.database_url), pool_pre_ping=True)
    return _engine


async def get_db_conn() -> AsyncIterator[AsyncConnection]:
    async with get_engine().connect() as conn:
        yield conn


async def get_user_by_id(
    conn: AsyncConnection,
    user_id: str,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(users_table).where(users_table.c.id == _as_uuid(user_id))
    )
    return result.fetchone()


async def get_user_2fa_secret(conn: AsyncConnection, user_id: str) -> str | None:
    result = await conn.execute(
        sa.select(users_table.c.totp_secret).where(users_table.c.id == _as_uuid(user_id))
    )
    return result.scalar_one_or_none()


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


async def get_token_balances_for_user(
    conn: AsyncConnection,
    user_id: str,
) -> list[dict[str, Any]]:
    latest_trade_prices = (
        sa.select(
            trades_table.c.token_id.label("token_id"),
            trades_table.c.price_sat.label("market_price_sat"),
            sa.func.row_number()
            .over(
                partition_by=trades_table.c.token_id,
                order_by=(
                    sa.func.coalesce(trades_table.c.settled_at, trades_table.c.created_at).desc(),
                    trades_table.c.id.desc(),
                ),
            )
            .label("price_rank"),
        )
        .where(trades_table.c.status == "settled")
        .subquery()
    )

    stmt = (
        sa.select(
            token_balances_table.c.token_id,
            assets_table.c.name.label("asset_name"),
            token_balances_table.c.balance,
            sa.func.coalesce(
                latest_trade_prices.c.market_price_sat,
                tokens_table.c.unit_price_sat,
            ).label("unit_price_sat"),
        )
        .select_from(
            token_balances_table
            .join(tokens_table, token_balances_table.c.token_id == tokens_table.c.id)
            .join(assets_table, tokens_table.c.asset_id == assets_table.c.id)
            .outerjoin(
                latest_trade_prices,
                sa.and_(
                    latest_trade_prices.c.token_id == token_balances_table.c.token_id,
                    latest_trade_prices.c.price_rank == 1,
                ),
            )
        )
        .where(token_balances_table.c.user_id == _as_uuid(user_id))
    )
    result = await conn.execute(stmt)
    return [dict(row) for row in result.mappings().all()]


async def create_transaction(
    conn: AsyncConnection,
    *,
    wallet_id: str | uuid.UUID,
    type: str,
    amount_sat: int,
    direction: str,
    status: str,
    txid: str | None = None,
    ln_payment_hash: str | None = None,
    description: str | None = None,
    confirmed_at: datetime | None = None,
) -> sa.engine.Row:
    result = await conn.execute(
        sa.insert(transactions_table)
        .values(
            id=uuid.uuid4(),
            wallet_id=_as_uuid(wallet_id),
            type=type,
            amount_sat=amount_sat,
            direction=direction,
            status=status,
            txid=txid,
            ln_payment_hash=ln_payment_hash,
            description=description,
            created_at=_utc_now(),
            confirmed_at=confirmed_at,
        )
        .returning(transactions_table)
    )
    row = result.fetchone()
    await conn.commit()
    assert row is not None
    return row


async def update_transaction_status(
    conn: AsyncConnection,
    transaction_id: str | uuid.UUID,
    status: str,
    confirmed_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {"status": status}
    if confirmed_at is not None:
        values["confirmed_at"] = confirmed_at

    await conn.execute(
        sa.update(transactions_table)
        .where(transactions_table.c.id == _as_uuid(transaction_id))
        .values(**values)
    )
    await conn.commit()


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
