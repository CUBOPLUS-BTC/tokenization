from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


CourseCategory = Literal["bitcoin", "finance", "programming", "entrepreneurship"]
CourseDifficulty = Literal["beginner", "intermediate", "advanced"]


def _strip_and_require_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value must not be blank.")
    return normalized


class CourseOut(BaseModel):
    id: str
    title: str
    description: str
    category: CourseCategory
    difficulty: CourseDifficulty


class CourseDetailOut(CourseOut):
    content_url: str


class CourseListResponse(BaseModel):
    courses: list[CourseOut]
    next_cursor: str | None


class CourseDetailResponse(BaseModel):
    course: CourseDetailOut


class EnrollmentOut(BaseModel):
    id: str
    course_id: str
    progress: float
    enrolled_at: datetime
    completed_at: datetime | None = None


class EnrollmentResponse(BaseModel):
    enrollment: EnrollmentOut


class EnrollmentProgressUpdateRequest(BaseModel):
    progress: float = Field(ge=0, le=100)

    @field_validator("progress")
    @classmethod
    def _validate_progress(cls, value: float) -> float:
        return float(value)
