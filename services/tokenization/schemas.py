from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator


AssetCategory = Literal["real_estate", "commodity", "invoice", "art", "other"]
AssetStatus = Literal["pending", "evaluating", "approved", "rejected", "tokenized"]
AssetTokenVisibility = Literal["public", "private"]


def _strip_and_require_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value must not be blank.")
    return normalized


def _normalize_hex_string(value: str) -> str:
    normalized = _strip_and_require_text(value).lower()
    try:
        bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError("Value must be valid hexadecimal.") from exc
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


class AssetTokenizationRequest(BaseModel):
    liquid_asset_id: str | None = None
    taproot_asset_id: str | None = None
    total_supply: int = Field(gt=0)
    unit_price_sat: int = Field(gt=0)
    visibility: AssetTokenVisibility = "public"

    @field_validator("liquid_asset_id", "taproot_asset_id")
    @classmethod
    def _validate_optional_asset_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalize_hex_string(value)
        if len(normalized) != 64:
            raise ValueError("Asset id must be 64 hexadecimal characters.")
        return normalized


class AssetDocumentOut(BaseModel):
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int


class AssetOut(BaseModel):
    id: str
    owner_id: str
    name: str
    description: str
    category: AssetCategory
    valuation_sat: int
    documents_url: str | None
    document: AssetDocumentOut | None = None
    status: AssetStatus
    created_at: datetime
    updated_at: datetime


class AssetResponse(BaseModel):
    asset: AssetOut


class AssetTokenOut(BaseModel):
    id: str
    liquid_asset_id: str
    total_supply: int
    circulating_supply: int
    unit_price_sat: int
    visibility: AssetTokenVisibility
    issuance_metadata: dict[str, Any] | None = None
    minted_at: datetime


class AssetDetailOut(AssetOut):
    ai_score: float | None = None
    ai_analysis: dict[str, Any] | None = None
    projected_roi: float | None = None
    token: AssetTokenOut | None = None


class AssetDetailResponse(BaseModel):
    asset: AssetDetailOut


class AssetListResponse(BaseModel):
    assets: list[AssetDetailOut]
    next_cursor: str | None


class AssetEvaluationRequestResponse(BaseModel):
    message: str
    estimated_completion: datetime
