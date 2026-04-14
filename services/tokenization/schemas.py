from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator


AssetCategory = Literal["real_estate", "commodity", "invoice", "art", "other"]
AssetStatus = Literal["pending", "evaluating", "approved", "rejected", "tokenized"]


def _strip_and_require_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value must not be blank.")
    return normalized


class AssetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    category: AssetCategory
    valuation_sat: int = Field(gt=0)
    documents_url: AnyHttpUrl

    @field_validator("name", "description")
    @classmethod
    def _validate_text_fields(cls, value: str) -> str:
        return _strip_and_require_text(value)


class AssetOut(BaseModel):
    id: str
    owner_id: str
    name: str
    description: str
    category: AssetCategory
    valuation_sat: int
    documents_url: str
    status: AssetStatus
    created_at: datetime
    updated_at: datetime


class AssetResponse(BaseModel):
    asset: AssetOut

