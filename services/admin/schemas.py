from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# User Management Schemas
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    id: str
    email: str | None = None
    display_name: str
    role: str
    created_at: datetime


class UserListResponse(BaseModel):
    users: list[UserOut]
    next_cursor: str | None


class UpdateUserRoleRequest(BaseModel):
    role: Literal["user", "seller", "admin", "auditor"]


# ---------------------------------------------------------------------------
# Course Schemas
# ---------------------------------------------------------------------------

CourseCategory = Literal["bitcoin", "finance", "programming", "entrepreneurship"]
CourseDifficulty = Literal["beginner", "intermediate", "advanced"]


class CreateCourseRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    content_url: str = Field(pattern=r"^https?://")
    category: CourseCategory
    difficulty: CourseDifficulty


class CourseOut(BaseModel):
    id: str
    title: str
    description: str
    category: CourseCategory
    difficulty: CourseDifficulty
    content_url: str


class CourseResponse(BaseModel):
    course: CourseOut


# ---------------------------------------------------------------------------
# Treasury Schemas
# ---------------------------------------------------------------------------

class TreasuryDisburseRequest(BaseModel):
    amount_sat: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=255)


class TreasuryEntryOut(BaseModel):
    id: str
    type: str
    amount_sat: int
    balance_after_sat: int
    reference_id: str | None = None
    description: str | None = None
    created_at: datetime


class TreasuryDisburseResponse(BaseModel):
    entry: TreasuryEntryOut


# ---------------------------------------------------------------------------
# Dispute Schemas
# ---------------------------------------------------------------------------

class AdminDisputeResolveRequest(BaseModel):
    resolution: Literal["refund_buyer", "release_to_seller"]
    notes: str = Field(min_length=1)


class DisputeOut(BaseModel):
    id: str
    trade_id: str
    opened_by: str
    reason: str
    status: str
    resolution: str | None = None
    resolved_by: str | None = None
    notes: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DisputeResponse(BaseModel):
    dispute: DisputeOut


class ReferralRewardOut(BaseModel):
    id: str
    referred_user_id: str
    referred_display_name: str
    referred_email: str | None = None
    reward_type: str
    amount_sat: int
    status: str
    eligibility_event: str
    credited_at: datetime
    created_at: datetime


class ReferralUserSummaryResponse(BaseModel):
    user_id: str
    referral_code: str
    referrals_count: int
    total_reward_sat: int
    rewards: list[ReferralRewardOut]


class ReferralPlatformSummaryResponse(BaseModel):
    referred_users: int
    active_referrers: int
    total_reward_sat: int


class YieldTokenSummaryOut(BaseModel):
    token_id: str
    asset_name: str
    total_yield_sat: int


class YieldAccrualOut(BaseModel):
    id: str
    token_id: str
    asset_name: str
    amount_sat: int
    quantity_held: int
    reference_price_sat: int
    annual_rate_pct: float
    accrued_from: datetime
    accrued_to: datetime
    created_at: datetime


class YieldUserSummaryResponse(BaseModel):
    user_id: str
    total_yield_sat: int
    by_token: list[YieldTokenSummaryOut]
    accruals: list[YieldAccrualOut]


class YieldPlatformSummaryResponse(BaseModel):
    users_with_yield: int
    yield_tokens: int
    total_yield_sat: int
