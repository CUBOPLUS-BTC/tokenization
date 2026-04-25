from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from datetime import datetime
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator


_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _strip_and_require_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value must not be blank.")
    return normalized


def _normalize_slug(value: str) -> str:
    normalized = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
    return normalized or "announcement"


def _normalize_hashtag(value: str) -> str:
    normalized = _normalize_slug(value)
    if not normalized:
        raise ValueError("Hashtag must not be blank.")
    return normalized


class AnnouncementPublishRequest(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    content: str = Field(min_length=1, max_length=10000)
    summary: str | None = Field(default=None, max_length=280)
    identifier: str | None = Field(default=None, max_length=120)
    hashtags: list[str] = Field(default_factory=list, max_length=12)
    location: str | None = Field(default=None, max_length=120)
    price_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=8)
    price_currency: str | None = Field(default=None, min_length=3, max_length=8)
    price_frequency: str | None = Field(default=None, max_length=32)
    reference_url: AnyHttpUrl | None = None
    image_urls: list[AnyHttpUrl] = Field(default_factory=list, max_length=6)
    status: Literal["active", "sold"] = "active"

    @field_validator("title", "content", "summary", "location", "price_frequency", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip_and_require_text(value)

    @field_validator("identifier", mode="before")
    @classmethod
    def _validate_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_slug(value)

    @field_validator("price_currency", mode="before")
    @classmethod
    def _validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip_and_require_text(value).upper()

    @field_validator("hashtags", mode="before")
    @classmethod
    def _validate_hashtags(cls, value: list[str] | None) -> list[str]:
        if value is None:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            tag = _normalize_hashtag(item)
            if tag not in seen:
                normalized.append(tag)
                seen.add(tag)
        return normalized

    @model_validator(mode="after")
    def _validate_price_fields(self) -> AnnouncementPublishRequest:
        if self.price_amount is None and self.price_currency is None and self.price_frequency is None:
            return self
        if self.price_amount is None or self.price_currency is None:
            raise ValueError("price_amount and price_currency must be provided together.")
        return self


class AnnouncementPublishResponse(BaseModel):
    id: str
    kind: Literal[30402]
    pubkey: str
    identifier: str
    accepted_relays: list[str]
    failed_relays: list[str]


CampaignStatus = Literal["draft", "funding_pending", "active", "paused", "completed", "exhausted", "cancelled", "failed"]
CampaignFundingMode = Literal["intraledger", "external"]
CampaignTriggerType = Literal["hashtag", "tag", "content_substring", "author_pubkey", "event_kind"]
CampaignTriggerOperator = Literal["equals", "contains", "in"]


class CampaignTriggerIn(BaseModel):
    trigger_type: CampaignTriggerType
    operator: CampaignTriggerOperator = "equals"
    value: str = Field(min_length=1, max_length=255)
    case_sensitive: bool = False

    @field_validator("value", mode="before")
    @classmethod
    def _validate_value(cls, value: str) -> str:
        return _strip_and_require_text(value)


class CampaignCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    funding_mode: CampaignFundingMode
    reward_amount_sat: int = Field(ge=1)
    budget_total_sat: int = Field(ge=1)
    max_rewards_per_user: int = Field(default=1, ge=1, le=1000)
    start_at: datetime | None = None
    end_at: datetime | None = None
    triggers: list[CampaignTriggerIn] = Field(min_length=1, max_length=20)

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _strip_and_require_text(value)

    @model_validator(mode="after")
    def _validate_budget_and_window(self) -> "CampaignCreateRequest":
        if self.budget_total_sat < self.reward_amount_sat:
            raise ValueError("budget_total_sat must be greater than or equal to reward_amount_sat.")
        if self.end_at is not None and self.start_at is not None and self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at.")
        return self


class CampaignFundingRequest(BaseModel):
    amount_sat: int = Field(ge=1)


class CampaignTriggerOut(BaseModel):
    id: str
    trigger_type: CampaignTriggerType
    operator: CampaignTriggerOperator
    value: str
    case_sensitive: bool
    created_at: datetime


class CampaignFundingOut(BaseModel):
    id: str
    funding_mode: CampaignFundingMode
    amount_sat: int
    status: Literal["pending", "confirmed", "cancelled", "refunded"]
    payment_hash: str | None = None
    payment_request: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime | None = None


class CampaignOut(BaseModel):
    id: str
    name: str
    status: CampaignStatus
    funding_mode: CampaignFundingMode
    reward_amount_sat: int
    budget_total_sat: int
    budget_reserved_sat: int
    budget_spent_sat: int
    budget_refunded_sat: int
    max_rewards_per_user: int
    start_at: datetime | None = None
    end_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    triggers: list[CampaignTriggerOut] = Field(default_factory=list)
    fundings: list[CampaignFundingOut] = Field(default_factory=list)


class CampaignMatchOut(BaseModel):
    id: str
    relay_url: str
    event_id: str
    event_pubkey: str
    event_kind: int
    match_fingerprint: str
    status: Literal["matched", "ignored", "reserved", "paid", "failed"]
    ignore_reason: str | None = None
    created_at: datetime


class CampaignPayoutOut(BaseModel):
    id: str
    match_id: str
    recipient_pubkey: str
    amount_sat: int
    fee_sat: int | None = None
    payment_hash: str | None = None
    status: Literal["pending", "succeeded", "failed"]
    failure_reason: str | None = None
    created_at: datetime
    settled_at: datetime | None = None
