from __future__ import annotations

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db.metadata import assets as assets_table
from common.db.metadata import token_balances as token_balances_table
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
    asset_id: uuid.UUID | None = None,
    owner_id: str,
    name: str,
    description: str,
    category: str,
    valuation_sat: int,
    documents_url: str | None,
    documents_storage_key: str | None = None,
    documents_filename: str | None = None,
    documents_content_type: str | None = None,
    documents_size_bytes: int | None = None,
) -> sa.engine.Row:
    now = _utc_now()
    result = await conn.execute(
        sa.insert(assets_table)
        .values(
            id=asset_id or uuid.uuid4(),
            owner_id=_as_uuid(owner_id),
            name=name,
            description=description,
            category=category,
            valuation_sat=valuation_sat,
            documents_url=documents_url,
            documents_storage_key=documents_storage_key,
            documents_filename=documents_filename,
            documents_content_type=documents_content_type,
            documents_size_bytes=documents_size_bytes,
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
            tokens_table.c.liquid_asset_id,
            tokens_table.c.total_supply,
            tokens_table.c.circulating_supply,
            tokens_table.c.unit_price_sat,
            tokens_table.c.visibility,
            tokens_table.c.metadata.label("token_metadata"),
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


async def create_asset_token(
    conn: AsyncConnection,
    *,
    asset_id: str | uuid.UUID,
    owner_id: str | uuid.UUID,
    liquid_asset_id: str,
    total_supply: int,
    circulating_supply: int,
    unit_price_sat: int,
    visibility: str,
    issuance_metadata: dict[str, object] | None,
) -> sa.engine.Row | None:
    now = _utc_now()
    token_id = uuid.uuid4()
    resolved_asset_id = liquid_asset_id
    if not resolved_asset_id:
        raise ValueError("liquid_asset_id is required")

    try:
        updated_asset = await conn.execute(
            sa.update(assets_table)
            .where(assets_table.c.id == _as_uuid(asset_id))
            .where(assets_table.c.owner_id == _as_uuid(owner_id))
            .where(assets_table.c.status == "approved")
            .values(
                status="tokenized",
                updated_at=now,
            )
            .returning(assets_table.c.id)
        )
        if updated_asset.fetchone() is None:
            await conn.rollback()
            return None

        await conn.execute(
            sa.insert(tokens_table).values(
                id=token_id,
                asset_id=_as_uuid(asset_id),
                liquid_asset_id=resolved_asset_id,
                total_supply=total_supply,
                circulating_supply=circulating_supply,
                unit_price_sat=unit_price_sat,
                visibility=visibility,
                metadata=issuance_metadata,
                minted_at=now,
                created_at=now,
            )
        )
        await conn.execute(
            sa.insert(token_balances_table).values(
                id=uuid.uuid4(),
                user_id=_as_uuid(owner_id),
                token_id=token_id,
                balance=circulating_supply,
                updated_at=now,
            )
        )
        await conn.commit()
    except IntegrityError:
        await conn.rollback()
        raise

    return await get_asset_by_id(conn, asset_id)
