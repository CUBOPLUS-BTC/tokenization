from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_settings
from common.db.metadata import (
    nostr_campaign_fundings as nostr_campaign_fundings_table,
    nostr_campaign_matches as nostr_campaign_matches_table,
    nostr_campaign_payouts as nostr_campaign_payouts_table,
    nostr_campaign_triggers as nostr_campaign_triggers_table,
    nostr_campaigns as nostr_campaigns_table,
)


settings = get_settings(service_name="nostr", default_port=8005)
_engine: AsyncEngine | None = None


def _make_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+asyncpg://"):
        return sync_url
    for prefix in ("postgresql+", "postgres+"):
        if sync_url.startswith(prefix):
            return "postgresql+asyncpg://" + sync_url.split("://", 1)[1]
    for prefix in ("postgresql://", "postgres://"):
        if sync_url.startswith(prefix):
            return "postgresql+asyncpg://" + sync_url[len(prefix):]
    return sync_url


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(_make_async_url(settings.database_url), pool_pre_ping=True)
    return _engine


async def get_db_conn() -> AsyncIterator[AsyncConnection]:
    async with get_engine().connect() as conn:
        yield conn


async def create_campaign(
    conn: AsyncConnection,
    *,
    user_id: str,
    payload: dict[str, Any],
    triggers: list[dict[str, Any]],
) -> sa.engine.Row:
    now = _utc_now()
    campaign_id = uuid.uuid4()
    result = await conn.execute(
        sa.insert(nostr_campaigns_table)
        .values(
            id=campaign_id,
            user_id=_as_uuid(user_id),
            name=payload["name"],
            status="draft",
            funding_mode=payload["funding_mode"],
            reward_amount_sat=payload["reward_amount_sat"],
            budget_total_sat=payload["budget_total_sat"],
            max_rewards_per_user=payload["max_rewards_per_user"],
            start_at=payload.get("start_at"),
            end_at=payload.get("end_at"),
            created_at=now,
            updated_at=now,
        )
        .returning(nostr_campaigns_table)
    )
    row = result.fetchone()
    assert row is not None
    for trigger in triggers:
        await conn.execute(
            sa.insert(nostr_campaign_triggers_table).values(
                id=uuid.uuid4(),
                campaign_id=campaign_id,
                trigger_type=trigger["trigger_type"],
                operator=trigger["operator"],
                value=trigger["value"],
                case_sensitive=trigger["case_sensitive"],
                created_at=now,
            )
        )
    await conn.commit()
    return row


async def list_campaigns_for_user(conn: AsyncConnection, user_id: str) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(nostr_campaigns_table)
        .where(nostr_campaigns_table.c.user_id == _as_uuid(user_id))
        .order_by(nostr_campaigns_table.c.created_at.desc(), nostr_campaigns_table.c.id.desc())
    )
    return list(result.fetchall())


async def get_campaign_by_id(
    conn: AsyncConnection,
    *,
    campaign_id: str,
    user_id: str | None = None,
) -> sa.engine.Row | None:
    stmt = sa.select(nostr_campaigns_table).where(nostr_campaigns_table.c.id == _as_uuid(campaign_id))
    if user_id is not None:
        stmt = stmt.where(nostr_campaigns_table.c.user_id == _as_uuid(user_id))
    result = await conn.execute(stmt)
    return result.fetchone()


async def list_campaign_triggers(conn: AsyncConnection, campaign_id: str) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(nostr_campaign_triggers_table)
        .where(nostr_campaign_triggers_table.c.campaign_id == _as_uuid(campaign_id))
        .order_by(nostr_campaign_triggers_table.c.created_at.asc(), nostr_campaign_triggers_table.c.id.asc())
    )
    return list(result.fetchall())


async def list_campaign_fundings(conn: AsyncConnection, campaign_id: str) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(nostr_campaign_fundings_table)
        .where(nostr_campaign_fundings_table.c.campaign_id == _as_uuid(campaign_id))
        .order_by(nostr_campaign_fundings_table.c.created_at.desc(), nostr_campaign_fundings_table.c.id.desc())
    )
    return list(result.fetchall())


async def set_campaign_status(
    conn: AsyncConnection,
    *,
    campaign_id: str,
    user_id: str,
    status: str,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.update(nostr_campaigns_table)
        .where(nostr_campaigns_table.c.id == _as_uuid(campaign_id))
        .where(nostr_campaigns_table.c.user_id == _as_uuid(user_id))
        .values(status=status, updated_at=_utc_now())
        .returning(nostr_campaigns_table)
    )
    row = result.fetchone()
    if row is None:
        await conn.rollback()
        return None
    await conn.commit()
    return row


async def list_campaign_matches(conn: AsyncConnection, campaign_id: str) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(nostr_campaign_matches_table)
        .where(nostr_campaign_matches_table.c.campaign_id == _as_uuid(campaign_id))
        .order_by(nostr_campaign_matches_table.c.created_at.desc(), nostr_campaign_matches_table.c.id.desc())
    )
    return list(result.fetchall())


async def list_campaign_payouts(conn: AsyncConnection, campaign_id: str) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(nostr_campaign_payouts_table)
        .where(nostr_campaign_payouts_table.c.campaign_id == _as_uuid(campaign_id))
        .order_by(nostr_campaign_payouts_table.c.created_at.desc(), nostr_campaign_payouts_table.c.id.desc())
    )
    return list(result.fetchall())
