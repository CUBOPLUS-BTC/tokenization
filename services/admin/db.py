from __future__ import annotations

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db.metadata import courses as courses_table
from common.db.metadata import disputes as disputes_table
from common.db.metadata import treasury as treasury_table
from common.db.metadata import users as users_table


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _row_value(row: object, key: str, default: object | None = None):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, default)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def list_users(
    conn: AsyncConnection,
    *,
    role: str | None = None,
) -> list[sa.engine.Row]:
    stmt = (
        sa.select(users_table)
        .where(users_table.c.deleted_at.is_(None))
        .order_by(users_table.c.created_at.desc(), users_table.c.id.desc())
    )
    if role is not None:
        stmt = stmt.where(users_table.c.role == role)
    result = await conn.execute(stmt)
    return result.fetchall()


async def get_user_by_id(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(users_table).where(users_table.c.id == _as_uuid(user_id))
    )
    return result.fetchone()


async def update_user_role(
    conn: AsyncConnection,
    *,
    user_id: str | uuid.UUID,
    new_role: str,
) -> sa.engine.Row | None:
    """Update the role for a non-deleted user. Returns the updated row or None."""
    now = _utc_now()
    result = await conn.execute(
        sa.update(users_table)
        .where(users_table.c.id == _as_uuid(user_id))
        .where(users_table.c.deleted_at.is_(None))
        .values(role=new_role, updated_at=now)
        .returning(users_table)
    )
    row = result.fetchone()
    if row is None:
        return None
    await conn.commit()
    return row


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

async def create_course(
    conn: AsyncConnection,
    *,
    title: str,
    description: str,
    content_url: str,
    category: str,
    difficulty: str,
) -> sa.engine.Row:
    now = _utc_now()
    result = await conn.execute(
        sa.insert(courses_table)
        .values(
            id=uuid.uuid4(),
            title=title,
            description=description,
            content_url=content_url,
            category=category,
            difficulty=difficulty,
            is_published=False,
            created_at=now,
            updated_at=now,
        )
        .returning(courses_table)
    )
    row = result.fetchone()
    assert row is not None
    await conn.commit()
    return row


# ---------------------------------------------------------------------------
# Treasury
# ---------------------------------------------------------------------------

async def get_latest_treasury_entry(conn: AsyncConnection) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(treasury_table)
        .order_by(treasury_table.c.created_at.desc(), treasury_table.c.id.desc())
        .limit(1)
    )
    return result.fetchone()


async def list_treasury_entries(
    conn: AsyncConnection,
    *,
    limit: int = 50,
    cursor_id: str | None = None,
) -> list[sa.engine.Row]:
    stmt = sa.select(treasury_table).order_by(
        treasury_table.c.created_at.desc(), treasury_table.c.id.desc()
    )
    if cursor_id is not None:
        # Cursor-based: entries older than (or equal-created-at with lower id) the cursor
        cursor_row_result = await conn.execute(
            sa.select(treasury_table).where(
                treasury_table.c.id == _as_uuid(cursor_id)
            )
        )
        cursor_row = cursor_row_result.fetchone()
        if cursor_row is not None:
            cursor_ts = _row_value(cursor_row, "created_at")
            cursor_uuid = _row_value(cursor_row, "id")
            stmt = stmt.where(
                sa.or_(
                    treasury_table.c.created_at < cursor_ts,
                    sa.and_(
                        treasury_table.c.created_at == cursor_ts,
                        treasury_table.c.id < cursor_uuid,
                    ),
                )
            )
    result = await conn.execute(stmt.limit(limit))
    return result.fetchall()


async def disburse_treasury(
    conn: AsyncConnection,
    *,
    amount_sat: int,
    description: str,
) -> sa.engine.Row:
    """Insert a disbursement entry, decrementing the running balance."""
    latest_entry = await get_latest_treasury_entry(conn)
    current_balance = int(_row_value(latest_entry, "balance_after_sat", 0))
    if current_balance < amount_sat:
        raise ValueError("insufficient_treasury_balance")

    balance_after_sat = current_balance - amount_sat
    now = _utc_now()
    result = await conn.execute(
        sa.insert(treasury_table)
        .values(
            id=uuid.uuid4(),
            source_trade_id=None,
            type="disbursement",
            amount_sat=amount_sat,
            balance_after_sat=balance_after_sat,
            description=description,
            created_at=now,
        )
        .returning(treasury_table)
    )
    row = result.fetchone()
    assert row is not None
    await conn.commit()
    return row


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------

async def get_dispute_by_trade_id(
    conn: AsyncConnection,
    trade_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(disputes_table).where(
            disputes_table.c.trade_id == _as_uuid(trade_id)
        )
    )
    return result.fetchone()
