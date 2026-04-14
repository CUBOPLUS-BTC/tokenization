from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

# Add parent directory to path to allow imports from common
sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import get_settings
from common.db.metadata import wallets, token_balances, tokens, assets, users, transactions

settings = get_settings(service_name="wallet", default_port=8001)

def _make_async_url(sync_url: str) -> str:
    """Convert standard postgres:// URL to asyncpg driver URL."""
    url = sync_url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url

_engine: sa.ext.asyncio.AsyncEngine | None = None

def get_engine() -> sa.ext.asyncio.AsyncEngine:
    global _engine
    if _engine is None:
        async_url = _make_async_url(settings.database_url)
        _engine = sa.ext.asyncio.create_async_engine(async_url, pool_pre_ping=True)
    return _engine

async def get_db_conn():
    """Dependency that provides an async database connection."""
    engine = get_engine()
    async with engine.connect() as conn:
        yield conn

async def get_user_2fa_secret(conn: AsyncConnection, user_id: str) -> str | None:
    """Fetch the TOTP secret for a user if 2FA is enabled."""
    stmt = sa.select(users.c.totp_secret).where(users.c.id == user_id)
    result = await conn.execute(stmt)
    return result.scalar()

async def create_transaction(
    conn: AsyncConnection,
    *,
    wallet_id: str,
    type: str,
    amount_sat: int,
    direction: str,
    status: str,
    ln_payment_hash: str | None = None,
    description: str | None = None,
) -> Any:
    """Insert a new transaction record."""
    stmt = sa.insert(transactions).values(
        wallet_id=wallet_id,
        type=type,
        amount_sat=amount_sat,
        direction=direction,
        status=status,
        ln_payment_hash=ln_payment_hash,
        description=description,
        created_at=sa.func.now(),
        updated_at=sa.func.now(),
    ).returning(transactions)
    result = await conn.execute(stmt)
    await conn.commit()
    return result.mappings().first()

async def update_transaction_status(
    conn: AsyncConnection,
    transaction_id: str,
    status: str,
    confirmed_at: Any | None = None,
) -> None:
    """Update the status of an existing transaction."""
    values = {"status": status, "updated_at": sa.func.now()}
    if confirmed_at:
        values["confirmed_at"] = confirmed_at
    
    stmt = sa.update(transactions).where(transactions.c.id == transaction_id).values(**values)
    await conn.execute(stmt)
    await conn.commit()

async def get_wallet_by_user_id(conn: AsyncConnection, user_id: str) -> Any | None:
    """Fetch the wallet record for a specific user."""
    stmt = sa.select(wallets).where(wallets.c.user_id == user_id)
    result = await conn.execute(stmt)
    return result.mappings().first()

async def get_token_balances_for_user(conn: AsyncConnection, user_id: str) -> list[dict[str, Any]]:
    """
    Fetch all token balances for a user, aggregated with asset and token metadata.
    Returns list of dicts with: token_id, asset_name, balance, unit_price_sat.
    """
    stmt = (
        sa.select(
            token_balances.c.token_id,
            assets.c.name.label("asset_name"),
            token_balances.c.balance,
            tokens.c.unit_price_sat
        )
        .select_from(
            token_balances
            .join(tokens, token_balances.c.token_id == tokens.c.id)
            .join(assets, tokens.c.asset_id == assets.c.id)
        )
        .where(token_balances.c.user_id == user_id)
    )
    result = await conn.execute(stmt)
    return [dict(r) for r in result.mappings().all()]
