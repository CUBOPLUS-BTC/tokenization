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
    api_keys as api_keys_table,
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


async def get_api_key_by_name(
    conn: AsyncConnection,
    *,
    user_id: str,
    name: str,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(api_keys_table)
        .where(api_keys_table.c.user_id == _as_uuid(user_id))
        .where(api_keys_table.c.name == name)
    )
    return result.fetchone()


async def get_api_key_by_prefix(
    conn: AsyncConnection,
    *,
    key_prefix: str,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(api_keys_table)
        .where(api_keys_table.c.key_prefix == key_prefix)
    )
    return result.fetchone()


async def get_api_key_by_id(
    conn: AsyncConnection,
    *,
    key_id: str,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(api_keys_table)
        .where(api_keys_table.c.id == _as_uuid(key_id))
    )
    return result.fetchone()


async def create_api_key(
    conn: AsyncConnection,
    *,
    user_id: str,
    name: str,
    key_prefix: str,
    key_hash: str,
    scopes: list[str],
    expires_at: datetime | None,
    created_by: str,
) -> sa.engine.Row:
    new_id = uuid.uuid4()
    now = datetime.now(tz=timezone.utc)
    await conn.execute(
        sa.insert(api_keys_table).values(
            id=new_id,
            user_id=_as_uuid(user_id),
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            scopes=scopes,
            expires_at=expires_at,
            revoked=False,
            created_at=now,
            created_by=_as_uuid(created_by),
        )
    )
    await conn.commit()
    row = await get_api_key_by_id(conn, key_id=str(new_id))
    assert row is not None
    return row


async def list_api_keys_for_user(
    conn: AsyncConnection,
    *,
    user_id: str,
) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(api_keys_table)
        .where(api_keys_table.c.user_id == _as_uuid(user_id))
        .order_by(api_keys_table.c.created_at.desc(), api_keys_table.c.id.desc())
    )
    return list(result.fetchall())


async def revoke_api_key(
    conn: AsyncConnection,
    *,
    key_id: str,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.update(api_keys_table)
        .where(api_keys_table.c.id == _as_uuid(key_id))
        .where(api_keys_table.c.revoked.is_(False))
        .values(revoked=True)
        .returning(api_keys_table)
    )
    row = result.fetchone()
    if row is None:
        await conn.rollback()
        return None
    await conn.commit()
    return row


async def rotate_api_key(
    conn: AsyncConnection,
    *,
    key_id: str,
    key_prefix: str,
    key_hash: str,
) -> sa.engine.Row | None:
    now = datetime.now(tz=timezone.utc)
    result = await conn.execute(
        sa.update(api_keys_table)
        .where(api_keys_table.c.id == _as_uuid(key_id))
        .where(api_keys_table.c.revoked.is_(False))
        .values(
            key_prefix=key_prefix,
            key_hash=key_hash,
            last_used_at=None,
            created_at=now,
        )
        .returning(api_keys_table)
    )
    row = result.fetchone()
    if row is None:
        await conn.rollback()
        return None
    await conn.commit()
    return row


async def touch_api_key_last_used(
    conn: AsyncConnection,
    *,
    key_id: str,
    used_at: datetime | None = None,
) -> None:
    await conn.execute(
        sa.update(api_keys_table)
        .where(api_keys_table.c.id == _as_uuid(key_id))
        .values(last_used_at=used_at or datetime.now(tz=timezone.utc))
    )
    await conn.commit()


async def enable_2fa(
    conn: AsyncConnection,
    user_id: str,
    totp_secret: str,
    backup_codes: list[str],
) -> None:
    """Set the TOTP secret and backup codes for a user."""
    await conn.execute(
        sa.update(users_table)
        .where(users_table.c.id == _as_uuid(user_id))
        .values(totp_secret=totp_secret, backup_codes=backup_codes)
    )
    await conn.commit()


async def set_user_2fa_verified(conn: AsyncConnection, user_id: str) -> None:
    """Mark the user's 2FA as verified."""
    await conn.execute(
        sa.update(users_table)
        .where(users_table.c.id == _as_uuid(user_id))
        .values(is_verified=True)
    )
    await conn.commit()


async def get_user_2fa_secret(
    conn: AsyncConnection, user_id: str
) -> str | None:
    """Return the user's totp_secret if set."""
    result = await conn.execute(
        sa.select(users_table.c.totp_secret).where(
            users_table.c.id == _as_uuid(user_id)
        )
    )
    return result.scalar()


async def create_user(
    conn: AsyncConnection,
    *,
    email: str,
    password_hash: str,
    display_name: str,
    referral_code: str,
    referrer_id: str | uuid.UUID | None = None,
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
            referral_code=referral_code,
            referrer_id=None if referrer_id is None else _as_uuid(referrer_id),
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
    referral_code: str,
) -> sa.engine.Row:
    """Insert a new user initialized via Nostr (no email/password)."""
    new_id = uuid.uuid4()
    now = datetime.now(tz=timezone.utc)
    await conn.execute(
        sa.insert(users_table).values(
            id=new_id,
            display_name=display_name,
            role="user",
            referral_code=referral_code,
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
