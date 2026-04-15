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
