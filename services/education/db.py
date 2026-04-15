from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db.metadata import courses as courses_table
from common.db.metadata import enrollments as enrollments_table
from common.db.metadata import users as users_table


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _normalize_progress(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def get_user_by_id(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(users_table).where(users_table.c.id == _as_uuid(user_id))
    )
    return result.fetchone()


async def list_courses(
    conn: AsyncConnection,
    *,
    category: str | None = None,
    difficulty: str | None = None,
) -> list[sa.engine.Row]:
    stmt = sa.select(courses_table).where(courses_table.c.is_published.is_(True))

    if category is not None:
        stmt = stmt.where(courses_table.c.category == category)
    if difficulty is not None:
        stmt = stmt.where(courses_table.c.difficulty == difficulty)

    stmt = stmt.order_by(courses_table.c.created_at.desc(), courses_table.c.id.desc())
    result = await conn.execute(stmt)
    return result.fetchall()


async def get_course_by_id(
    conn: AsyncConnection,
    course_id: str | uuid.UUID,
    *,
    published_only: bool = True,
) -> sa.engine.Row | None:
    stmt = sa.select(courses_table).where(courses_table.c.id == _as_uuid(course_id))
    if published_only:
        stmt = stmt.where(courses_table.c.is_published.is_(True))

    result = await conn.execute(stmt)
    return result.fetchone()


async def get_enrollment_by_id(
    conn: AsyncConnection,
    enrollment_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(enrollments_table).where(enrollments_table.c.id == _as_uuid(enrollment_id))
    )
    return result.fetchone()


async def get_enrollment_by_user_course(
    conn: AsyncConnection,
    *,
    user_id: str | uuid.UUID,
    course_id: str | uuid.UUID,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(enrollments_table)
        .where(enrollments_table.c.user_id == _as_uuid(user_id))
        .where(enrollments_table.c.course_id == _as_uuid(course_id))
    )
    return result.fetchone()


async def create_enrollment(
    conn: AsyncConnection,
    *,
    user_id: str | uuid.UUID,
    course_id: str | uuid.UUID,
) -> sa.engine.Row:
    now = _utc_now()
    result = await conn.execute(
        sa.insert(enrollments_table)
        .values(
            id=uuid.uuid4(),
            user_id=_as_uuid(user_id),
            course_id=_as_uuid(course_id),
            progress=_normalize_progress(0),
            enrolled_at=now,
            completed_at=None,
        )
        .returning(enrollments_table)
    )
    row = result.fetchone()
    await conn.commit()
    assert row is not None
    return row


async def update_enrollment_progress(
    conn: AsyncConnection,
    *,
    enrollment_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    progress: float | Decimal,
) -> sa.engine.Row | None:
    normalized_progress = _normalize_progress(progress)
    completed_at = _utc_now() if normalized_progress >= Decimal("100.00") else None
    result = await conn.execute(
        sa.update(enrollments_table)
        .where(enrollments_table.c.id == _as_uuid(enrollment_id))
        .where(enrollments_table.c.user_id == _as_uuid(user_id))
        .values(
            progress=normalized_progress,
            completed_at=completed_at,
        )
        .returning(enrollments_table)
    )
    row = result.fetchone()
    await conn.commit()
    return row
