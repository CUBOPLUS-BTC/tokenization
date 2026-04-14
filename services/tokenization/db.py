from __future__ import annotations

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db.metadata import assets as assets_table
from common.db.metadata import users as users_table


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
