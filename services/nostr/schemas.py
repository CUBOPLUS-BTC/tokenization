from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

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
