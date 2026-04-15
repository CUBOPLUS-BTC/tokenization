"""Database helpers for KYC verification state management.

Provides CRUD operations on the ``kyc_verifications`` table so that both the
auth service (user-facing) and the marketplace service (trade enforcement) can
query and mutate verification records.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db.metadata import kyc_verifications as kyc_table


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def get_kyc_status(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    """Return the KYC verification row for *user_id*, or ``None``."""
    result = await conn.execute(
        sa.select(kyc_table).where(kyc_table.c.user_id == _as_uuid(user_id))
    )
    return result.fetchone()


async def create_kyc_record(
    conn: AsyncConnection,
    *,
    user_id: str | uuid.UUID,
    document_url: str | None = None,
    notes: str | None = None,
) -> sa.engine.Row:
    """Insert a new KYC record in ``pending`` state for the user."""
    now = _utc_now()
    result = await conn.execute(
        sa.insert(kyc_table)
        .values(
            id=uuid.uuid4(),
            user_id=_as_uuid(user_id),
            status="pending",
            document_url=document_url,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        .returning(kyc_table)
    )
    row = result.fetchone()
    await conn.commit()
    assert row is not None
    return row


async def update_kyc_status(
    conn: AsyncConnection,
    *,
    user_id: str | uuid.UUID,
    new_status: str,
    reviewed_by: str | uuid.UUID,
    rejection_reason: str | None = None,
    notes: str | None = None,
) -> sa.engine.Row | None:
    """Transition the KYC status for *user_id*.

    Returns the updated row if the transition was applied, ``None`` otherwise.
    """
    if new_status not in ("verified", "rejected", "expired", "pending"):
        raise ValueError("invalid_kyc_status")

    now = _utc_now()
    values: dict = {
        "status": new_status,
        "reviewed_by": _as_uuid(reviewed_by),
        "reviewed_at": now,
        "updated_at": now,
    }
    if rejection_reason is not None:
        values["rejection_reason"] = rejection_reason
    if notes is not None:
        values["notes"] = notes

    result = await conn.execute(
        sa.update(kyc_table)
        .where(kyc_table.c.user_id == _as_uuid(user_id))
        .values(**values)
        .returning(kyc_table)
    )
    row = result.fetchone()
    if row is None:
        await conn.rollback()
        return None
    await conn.commit()
    return row


async def list_kyc_records(
    conn: AsyncConnection,
    *,
    status_filter: str | None = None,
) -> list[sa.engine.Row]:
    """List all KYC verification records, optionally filtered by status."""
    stmt = sa.select(kyc_table).order_by(
        kyc_table.c.updated_at.desc(), kyc_table.c.id.desc()
    )
    if status_filter is not None:
        stmt = stmt.where(kyc_table.c.status == status_filter)
    result = await conn.execute(stmt)
    return result.fetchall()


def is_kyc_verified(kyc_row: sa.engine.Row | None) -> bool:
    """Check if a KYC row represents a verified user."""
    if kyc_row is None:
        return False
    mapping = getattr(kyc_row, "_mapping", None)
    if mapping is not None:
        return mapping.get("status") == "verified"
    return getattr(kyc_row, "status", None) == "verified"
