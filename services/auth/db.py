"""Database helpers for the auth service.

All queries use core SQLAlchemy expressions against the shared metadata
defined in services/common/db/metadata.py so there is a single source of
truth for the schema.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

# Re-use the canonical table objects
from pathlib import Path
import sys

# Allow importing common package regardless of working directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db.metadata import users as users_table, wallets as wallets_table


async def get_user_by_email(
    conn: AsyncConnection, email: str
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(users_table).where(users_table.c.email == email)
    )
    return result.fetchone()


async def get_user_by_id(
    conn: AsyncConnection, user_id: str
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(users_table).where(users_table.c.id == user_id)
    )
    return result.fetchone()


async def create_user(
    conn: AsyncConnection,
    *,
    email: str,
    password_hash: str,
    display_name: str,
) -> sa.engine.Row:
    """Insert a new user and return the full row."""
    new_id = uuid.uuid4()
    now = datetime.now(tz=timezone.utc)
    await conn.execute(
        sa.insert(users_table).values(
            id=new_id,
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            role="user",
            created_at=now,
            updated_at=now,
        )
    )
    await conn.commit()
    row = await get_user_by_id(conn, str(new_id))
    assert row is not None  # just inserted
    return row
