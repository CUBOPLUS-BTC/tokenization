from __future__ import annotations

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db.metadata import assets as assets_table
from common.db.metadata import tokens as tokens_table
from common.db.metadata import users as users_table


_EVALUABLE_ASSET_STATUSES = ("pending", "approved", "rejected")


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


async def create_asset(
    conn: AsyncConnection,
    *,
    owner_id: str,
    name: str,
    description: str,
    category: str,
    valuation_sat: int,
    documents_url: str,
) -> sa.engine.Row:
    now = _utc_now()
    result = await conn.execute(
        sa.insert(assets_table)
        .values(
            id=uuid.uuid4(),
            owner_id=_as_uuid(owner_id),
            name=name,
            description=description,
            category=category,
            valuation_sat=valuation_sat,
            documents_url=documents_url,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        .returning(assets_table)
    )
    row = result.fetchone()
    await conn.commit()
    assert row is not None
    return row


async def get_asset_by_id(
    conn: AsyncConnection,
    asset_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(
            assets_table,
            tokens_table.c.id.label("token_id"),
            tokens_table.c.taproot_asset_id,
            tokens_table.c.total_supply,
            tokens_table.c.circulating_supply,
            tokens_table.c.unit_price_sat,
            tokens_table.c.minted_at,
        )
        .select_from(
            assets_table.outerjoin(tokens_table, tokens_table.c.asset_id == assets_table.c.id)
        )
        .where(assets_table.c.id == _as_uuid(asset_id))
    )
    return result.fetchone()


async def begin_asset_evaluation(
    conn: AsyncConnection,
    *,
    asset_id: str | uuid.UUID,
    owner_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    now = _utc_now()
    result = await conn.execute(
        sa.update(assets_table)
        .where(assets_table.c.id == _as_uuid(asset_id))
        .where(assets_table.c.owner_id == _as_uuid(owner_id))
        .where(assets_table.c.status.in_(_EVALUABLE_ASSET_STATUSES))
        .values(
            status="evaluating",
            updated_at=now,
        )
        .returning(assets_table)
    )
    row = result.fetchone()
    await conn.commit()
    return row


async def complete_asset_evaluation(
    conn: AsyncConnection,
    *,
    asset_id: str | uuid.UUID,
    ai_score: float,
    ai_analysis: dict[str, object],
    projected_roi: float,
    status: str,
) -> sa.engine.Row | None:
    now = _utc_now()
    result = await conn.execute(
        sa.update(assets_table)
        .where(assets_table.c.id == _as_uuid(asset_id))
        .where(assets_table.c.status == "evaluating")
        .values(
            ai_score=ai_score,
            ai_analysis=ai_analysis,
            projected_roi=projected_roi,
            status=status,
            updated_at=now,
        )
        .returning(assets_table)
    )
    row = result.fetchone()
    await conn.commit()
    return row


async def reset_asset_evaluation(
    conn: AsyncConnection,
    *,
    asset_id: str | uuid.UUID,
    fallback_status: str,
) -> sa.engine.Row | None:
    now = _utc_now()
    result = await conn.execute(
        sa.update(assets_table)
        .where(assets_table.c.id == _as_uuid(asset_id))
        .where(assets_table.c.status == "evaluating")
        .values(
            status=fallback_status,
            updated_at=now,
        )
        .returning(assets_table)
    )
    row = result.fetchone()
    await conn.commit()
    return row


async def list_assets(
    conn: AsyncConnection,
    *,
    asset_status: str | None = None,
    category: str | None = None,
) -> list[sa.engine.Row]:
    stmt = sa.select(assets_table)

    if asset_status is not None:
        stmt = stmt.where(assets_table.c.status == asset_status)

    if category is not None:
        stmt = stmt.where(assets_table.c.category == category)

    stmt = stmt.order_by(assets_table.c.created_at.desc(), assets_table.c.id.desc())
    result = await conn.execute(stmt)
    return result.fetchall()
