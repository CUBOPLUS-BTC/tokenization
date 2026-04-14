from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

# Add parent directory to path to allow imports from common
sys.path.append(str(Path(__file__).resolve().parents[1]))
from common.db.metadata import wallets, token_balances, tokens, assets

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
