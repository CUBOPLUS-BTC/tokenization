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

from common.db.metadata import (
    refresh_token_sessions as refresh_token_sessions_table,
    users as users_table,
    wallets as wallets_table,
    nostr_identities as nostr_identities_table,
)


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


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


async def create_refresh_session(
    conn: AsyncConnection,
    *,
    user_id: str,
    token_jti: str,
    expires_at: datetime,
) -> None:
    now = datetime.now(tz=timezone.utc)
    await conn.execute(
        sa.insert(refresh_token_sessions_table).values(
            id=uuid.uuid4(),
            user_id=_as_uuid(user_id),
            token_jti=_as_uuid(token_jti),
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
    )
    await conn.commit()


async def rotate_refresh_session(
    conn: AsyncConnection,
    *,
    user_id: str,
    current_token_jti: str,
    replacement_token_jti: str,
    replacement_expires_at: datetime,
) -> bool:
    now = datetime.now(tz=timezone.utc)
    current_uuid = _as_uuid(current_token_jti)
    replacement_uuid = _as_uuid(replacement_token_jti)
    user_uuid = _as_uuid(user_id)

    result = await conn.execute(
        sa.update(refresh_token_sessions_table)
        .where(refresh_token_sessions_table.c.user_id == user_uuid)
        .where(refresh_token_sessions_table.c.token_jti == current_uuid)
        .where(refresh_token_sessions_table.c.revoked_at.is_(None))
        .where(refresh_token_sessions_table.c.expires_at > now)
        .values(
            revoked_at=now,
            replaced_by_jti=replacement_uuid,
            updated_at=now,
        )
        .returning(refresh_token_sessions_table.c.id)
    )
    if result.fetchone() is None:
        await conn.rollback()
        return False

    await conn.execute(
        sa.insert(refresh_token_sessions_table).values(
            id=uuid.uuid4(),
            user_id=user_uuid,
            token_jti=replacement_uuid,
            expires_at=replacement_expires_at,
            created_at=now,
            updated_at=now,
        )
    )
    await conn.commit()
    return True


async def revoke_refresh_session(
    conn: AsyncConnection,
    *,
    user_id: str,
    token_jti: str,
) -> bool:
    now = datetime.now(tz=timezone.utc)
    result = await conn.execute(
        sa.update(refresh_token_sessions_table)
        .where(refresh_token_sessions_table.c.user_id == _as_uuid(user_id))
        .where(refresh_token_sessions_table.c.token_jti == _as_uuid(token_jti))
        .where(refresh_token_sessions_table.c.revoked_at.is_(None))
        .where(refresh_token_sessions_table.c.expires_at > now)
        .values(revoked_at=now, updated_at=now)
        .returning(refresh_token_sessions_table.c.id)
    )
    if result.fetchone() is None:
        await conn.rollback()
        return False

    await conn.commit()
    return True


async def get_nostr_identity_by_pubkey(
    conn: AsyncConnection, pubkey: str
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(nostr_identities_table).where(
            nostr_identities_table.c.pubkey == pubkey
        )
    )
    return result.fetchone()


async def create_nostr_user(
    conn: AsyncConnection,
    *,
    display_name: str,
) -> sa.engine.Row:
    """Insert a new user initialized via Nostr (no email/password)."""
    new_id = uuid.uuid4()
    now = datetime.now(tz=timezone.utc)
    await conn.execute(
        sa.insert(users_table).values(
            id=new_id,
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


async def create_nostr_identity(
    conn: AsyncConnection,
    *,
    user_id: str,
    pubkey: str,
    relay_urls: list[str] | None = None,
) -> None:
    now = datetime.now(tz=timezone.utc)
    await conn.execute(
        sa.insert(nostr_identities_table).values(
            id=uuid.uuid4(),
            user_id=_as_uuid(user_id),
            pubkey=pubkey,
            relay_urls=relay_urls,
            created_at=now,
        )
    )
    await conn.commit()
