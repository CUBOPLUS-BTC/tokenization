from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from .db.metadata import assets as assets_table
from .db.metadata import referral_rewards as referral_rewards_table
from .db.metadata import token_balances as token_balances_table
from .db.metadata import tokens as tokens_table
from .db.metadata import trades as trades_table
from .db.metadata import treasury as treasury_table
from .db.metadata import users as users_table
from .db.metadata import yield_accruals as yield_accruals_table


REFERRAL_SIGNUP_BONUS_SAT = 50_000
_DAYS_PER_YEAR = Decimal("365")
_ONE_HUNDRED = Decimal("100")


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _row_value(row: object, key: str, default: object | None = None):
    if isinstance(row, dict):
        return row.get(key, default)

    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, default)


def _normalize_referral_code(value: str) -> str:
    return value.strip().upper()


async def generate_referral_code(conn: AsyncConnection) -> str:
    for _ in range(10):
        candidate = uuid.uuid4().hex[:10].upper()
        existing = await conn.execute(
            sa.select(users_table.c.id).where(users_table.c.referral_code == candidate)
        )
        if existing.fetchone() is None:
            return candidate
    raise RuntimeError("unable_to_generate_unique_referral_code")


async def get_user_by_referral_code(
    conn: AsyncConnection,
    referral_code: str,
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(users_table).where(
            users_table.c.referral_code == _normalize_referral_code(referral_code)
        )
    )
    return result.fetchone()


async def get_referral_reward_by_referred_user(
    conn: AsyncConnection,
    referred_user_id: str | uuid.UUID,
    *,
    reward_type: str = "signup_bonus",
) -> sa.engine.Row | None:
    result = await conn.execute(
        sa.select(referral_rewards_table)
        .where(referral_rewards_table.c.referred_user_id == _as_uuid(referred_user_id))
        .where(referral_rewards_table.c.reward_type == reward_type)
        .limit(1)
    )
    return result.fetchone()


async def create_referral_signup_reward(
    conn: AsyncConnection,
    *,
    referred_user_id: str | uuid.UUID,
    amount_sat: int = REFERRAL_SIGNUP_BONUS_SAT,
) -> sa.engine.Row | None:
    user_result = await conn.execute(
        sa.select(users_table.c.referrer_id)
        .where(users_table.c.id == _as_uuid(referred_user_id))
        .limit(1)
    )
    user_row = user_result.fetchone()
    if user_row is None:
        return None

    referrer_id = _row_value(user_row, "referrer_id")
    if referrer_id is None:
        return None

    existing_reward = await get_referral_reward_by_referred_user(conn, referred_user_id)
    if existing_reward is not None:
        return existing_reward

    now = _utc_now()
    reward_result = await conn.execute(
        sa.insert(referral_rewards_table)
        .values(
            id=uuid.uuid4(),
            referrer_id=referrer_id,
            referred_user_id=_as_uuid(referred_user_id),
            reward_type="signup_bonus",
            amount_sat=amount_sat,
            status="credited",
            eligibility_event="kyc_verified",
            metadata={"rule": "verified_kyc_referral_bonus"},
            credited_at=now,
            created_at=now,
        )
        .returning(referral_rewards_table)
    )
    reward_row = reward_result.fetchone()
    assert reward_row is not None

    latest_treasury = await conn.execute(
        sa.select(treasury_table.c.balance_after_sat)
        .order_by(treasury_table.c.created_at.desc(), treasury_table.c.id.desc())
        .limit(1)
    )
    balance_after_sat = int(latest_treasury.scalar_one_or_none() or 0) - amount_sat
    await conn.execute(
        sa.insert(treasury_table).values(
            id=uuid.uuid4(),
            source_trade_id=None,
            source_referral_reward_id=_row_value(reward_row, "id"),
            type="referral_reward",
            amount_sat=amount_sat,
            balance_after_sat=balance_after_sat,
            description=f"Referral signup reward for user {_as_uuid(referred_user_id)}",
            created_at=now,
        )
    )
    return reward_row


async def list_referral_rewards_for_user(
    conn: AsyncConnection,
    referrer_id: str | uuid.UUID,
) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(
            referral_rewards_table,
            users_table.c.display_name.label("referred_display_name"),
            users_table.c.email.label("referred_email"),
            users_table.c.created_at.label("referred_created_at"),
        )
        .select_from(
            referral_rewards_table.join(
                users_table,
                users_table.c.id == referral_rewards_table.c.referred_user_id,
            )
        )
        .where(referral_rewards_table.c.referrer_id == _as_uuid(referrer_id))
        .order_by(referral_rewards_table.c.created_at.desc(), referral_rewards_table.c.id.desc())
    )
    return result.fetchall()


async def list_referred_users(
    conn: AsyncConnection,
    referrer_id: str | uuid.UUID,
) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(users_table)
        .where(users_table.c.referrer_id == _as_uuid(referrer_id))
        .order_by(users_table.c.created_at.desc(), users_table.c.id.desc())
    )
    return result.fetchall()


async def summarize_referrals_for_user(
    conn: AsyncConnection,
    referrer_id: str | uuid.UUID,
) -> dict[str, int]:
    result = await conn.execute(
        sa.select(
            sa.func.count(sa.distinct(users_table.c.id)).label("referrals_count"),
            sa.func.coalesce(sa.func.sum(referral_rewards_table.c.amount_sat), 0).label("total_reward_sat"),
        )
        .select_from(
            users_table.outerjoin(
                referral_rewards_table,
                sa.and_(
                    referral_rewards_table.c.referred_user_id == users_table.c.id,
                    referral_rewards_table.c.referrer_id == _as_uuid(referrer_id),
                ),
            )
        )
        .where(users_table.c.referrer_id == _as_uuid(referrer_id))
    )
    row = result.fetchone()
    return {
        "referrals_count": int(_row_value(row, "referrals_count", 0)),
        "total_reward_sat": int(_row_value(row, "total_reward_sat", 0)),
    }


async def summarize_referrals_platform(conn: AsyncConnection) -> dict[str, int]:
    result = await conn.execute(
        sa.select(
            sa.func.count(sa.distinct(users_table.c.id))
            .filter(users_table.c.referrer_id.is_not(None))
            .label("referred_users"),
            sa.func.count(sa.distinct(referral_rewards_table.c.referrer_id)).label("active_referrers"),
            sa.func.coalesce(sa.func.sum(referral_rewards_table.c.amount_sat), 0).label("total_reward_sat"),
        )
        .select_from(
            users_table.outerjoin(
                referral_rewards_table,
                referral_rewards_table.c.referred_user_id == users_table.c.id,
            )
        )
    )
    row = result.fetchone()
    return {
        "referred_users": int(_row_value(row, "referred_users", 0)),
        "active_referrers": int(_row_value(row, "active_referrers", 0)),
        "total_reward_sat": int(_row_value(row, "total_reward_sat", 0)),
    }


async def get_user_yield_accruals(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> list[sa.engine.Row]:
    result = await conn.execute(
        sa.select(
            yield_accruals_table,
            assets_table.c.name.label("asset_name"),
        )
        .select_from(
            yield_accruals_table
            .join(tokens_table, tokens_table.c.id == yield_accruals_table.c.token_id)
            .join(assets_table, assets_table.c.id == tokens_table.c.asset_id)
        )
        .where(yield_accruals_table.c.user_id == _as_uuid(user_id))
        .order_by(yield_accruals_table.c.created_at.desc(), yield_accruals_table.c.id.desc())
    )
    return result.fetchall()


async def summarize_yield_for_user(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
) -> tuple[int, list[sa.engine.Row]]:
    per_token = await conn.execute(
        sa.select(
            yield_accruals_table.c.token_id,
            assets_table.c.name.label("asset_name"),
            sa.func.coalesce(sa.func.sum(yield_accruals_table.c.amount_sat), 0).label("total_yield_sat"),
        )
        .select_from(
            yield_accruals_table
            .join(tokens_table, tokens_table.c.id == yield_accruals_table.c.token_id)
            .join(assets_table, assets_table.c.id == tokens_table.c.asset_id)
        )
        .where(yield_accruals_table.c.user_id == _as_uuid(user_id))
        .group_by(yield_accruals_table.c.token_id, assets_table.c.name)
        .order_by(sa.text("total_yield_sat DESC"), yield_accruals_table.c.token_id.asc())
    )
    rows = per_token.fetchall()
    total = sum(int(_row_value(row, "total_yield_sat", 0)) for row in rows)
    return total, rows


async def summarize_yield_platform(conn: AsyncConnection) -> dict[str, int]:
    result = await conn.execute(
        sa.select(
            sa.func.count(sa.distinct(yield_accruals_table.c.user_id)).label("users_with_yield"),
            sa.func.count(sa.distinct(yield_accruals_table.c.token_id)).label("yield_tokens"),
            sa.func.coalesce(sa.func.sum(yield_accruals_table.c.amount_sat), 0).label("total_yield_sat"),
        )
    )
    row = result.fetchone()
    return {
        "users_with_yield": int(_row_value(row, "users_with_yield", 0)),
        "yield_tokens": int(_row_value(row, "yield_tokens", 0)),
        "total_yield_sat": int(_row_value(row, "total_yield_sat", 0)),
    }


async def accrue_pending_yield_for_user(
    conn: AsyncConnection,
    user_id: str | uuid.UUID,
    *,
    as_of: datetime | None = None,
) -> list[sa.engine.Row]:
    accrual_time = as_of or _utc_now()
    latest_trade_prices = (
        sa.select(
            trades_table.c.token_id.label("token_id"),
            trades_table.c.price_sat.label("market_price_sat"),
            sa.func.row_number()
            .over(
                partition_by=trades_table.c.token_id,
                order_by=(
                    sa.func.coalesce(trades_table.c.settled_at, trades_table.c.created_at).desc(),
                    trades_table.c.id.desc(),
                ),
            )
            .label("price_rank"),
        )
        .subquery()
    )
    latest_accruals = (
        sa.select(
            yield_accruals_table.c.user_id,
            yield_accruals_table.c.token_id,
            sa.func.max(yield_accruals_table.c.accrued_to).label("last_accrued_to"),
        )
        .group_by(yield_accruals_table.c.user_id, yield_accruals_table.c.token_id)
        .subquery()
    )
    result = await conn.execute(
        sa.select(
            token_balances_table.c.user_id,
            token_balances_table.c.token_id,
            token_balances_table.c.balance,
            token_balances_table.c.updated_at,
            tokens_table.c.minted_at,
            assets_table.c.projected_roi,
            sa.func.coalesce(
                latest_trade_prices.c.market_price_sat,
                tokens_table.c.unit_price_sat,
            ).label("reference_price_sat"),
            latest_accruals.c.last_accrued_to,
        )
        .select_from(
            token_balances_table
            .join(tokens_table, tokens_table.c.id == token_balances_table.c.token_id)
            .join(assets_table, assets_table.c.id == tokens_table.c.asset_id)
            .outerjoin(
                latest_trade_prices,
                sa.and_(
                    latest_trade_prices.c.token_id == token_balances_table.c.token_id,
                    latest_trade_prices.c.price_rank == 1,
                ),
            )
            .outerjoin(
                latest_accruals,
                sa.and_(
                    latest_accruals.c.user_id == token_balances_table.c.user_id,
                    latest_accruals.c.token_id == token_balances_table.c.token_id,
                ),
            )
        )
        .where(token_balances_table.c.user_id == _as_uuid(user_id))
        .where(token_balances_table.c.balance > 0)
        .where(assets_table.c.projected_roi.is_not(None))
        .where(assets_table.c.projected_roi > 0)
    )
    rows = result.fetchall()

    inserted_rows: list[sa.engine.Row] = []
    for row in rows:
        start_at = _row_value(row, "last_accrued_to") or _row_value(row, "updated_at") or _row_value(row, "minted_at")
        if start_at is None:
            continue
        days_elapsed = (accrual_time.date() - start_at.date()).days
        if days_elapsed <= 0:
            continue

        annual_rate = Decimal(str(_row_value(row, "projected_roi", "0")))
        quantity = Decimal(int(_row_value(row, "balance", 0)))
        reference_price = Decimal(int(_row_value(row, "reference_price_sat", 0)))
        gross_yield = (quantity * reference_price * annual_rate * Decimal(days_elapsed)) / (_ONE_HUNDRED * _DAYS_PER_YEAR)
        amount_sat = int(gross_yield.to_integral_value(rounding=ROUND_DOWN))
        if amount_sat <= 0:
            continue

        accrual_from = start_at
        accrual_to = start_at + timedelta(days=days_elapsed)
        insert_result = await conn.execute(
            sa.insert(yield_accruals_table)
            .values(
                id=uuid.uuid4(),
                user_id=_row_value(row, "user_id"),
                token_id=_row_value(row, "token_id"),
                annual_rate_pct=annual_rate.quantize(Decimal("0.01"), rounding=ROUND_DOWN),
                quantity_held=int(quantity),
                reference_price_sat=int(reference_price),
                amount_sat=amount_sat,
                accrued_from=accrual_from,
                accrued_to=accrual_to,
                created_at=accrual_time,
            )
            .returning(yield_accruals_table)
        )
        inserted_row = insert_result.fetchone()
        if inserted_row is not None:
            inserted_rows.append(inserted_row)

    return inserted_rows


__all__ = [
    "REFERRAL_SIGNUP_BONUS_SAT",
    "accrue_pending_yield_for_user",
    "create_referral_signup_reward",
    "generate_referral_code",
    "get_referral_reward_by_referred_user",
    "get_user_by_referral_code",
    "get_user_yield_accruals",
    "list_referral_rewards_for_user",
    "list_referred_users",
    "summarize_referrals_for_user",
    "summarize_referrals_platform",
    "summarize_yield_for_user",
    "summarize_yield_platform",
]