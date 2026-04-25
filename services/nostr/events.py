from __future__ import annotations

import json
import time
from typing import Any
import hashlib
import re
import uuid


def _entity_tags(payload: dict[str, Any]) -> list[list[str]]:
    tags: list[list[str]] = []
    for key, value in sorted(payload.items()):
        if not key.endswith("_id") or value is None:
            continue
        tags.append(["entity", key, str(value)])
    return tags


def map_internal_event_to_nostr(
    topic: str,
    payload: dict[str, Any],
    *,
    source_service: str,
) -> dict[str, Any]:
    event_name = str(payload.get("event") or topic.replace(".", "_"))
    content = {
        "event_type": event_name,
        "topic": topic,
        "source_service": source_service,
        "occurred_at": payload.get("created_at") or payload.get("completed_at") or payload.get("minted_at"),
        "payload": payload,
    }
    tags: list[list[str]] = [
        ["topic", topic],
        ["event", event_name],
        ["source", source_service],
        *_entity_tags(payload),
    ]
    return {
        "kind": 1,
        "created_at": int(time.time()),
        "tags": tags,
        "content": json.dumps(content, separators=(",", ":"), sort_keys=True),
    }


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "announcement"


def _stringify_price(value: Any) -> str:
    if hasattr(value, "normalize"):
        normalized = value.normalize()
        price = format(normalized, "f")
    else:
        price = str(value)
    return price.rstrip("0").rstrip(".") if "." in price else price


def make_classified_listing_event(
    *,
    title: str,
    content: str,
    summary: str | None = None,
    identifier: str | None = None,
    hashtags: list[str] | None = None,
    location: str | None = None,
    price_amount: Any | None = None,
    price_currency: str | None = None,
    price_frequency: str | None = None,
    reference_url: str | None = None,
    image_urls: list[str] | None = None,
    status: str = "active",
) -> dict[str, Any]:
    created_at = int(time.time())
    listing_identifier = identifier or f"{_slugify(title)}-{uuid.uuid4().hex[:8]}"

    tags: list[list[str]] = [
        ["d", listing_identifier],
        ["title", title],
        ["published_at", str(created_at)],
        ["status", status],
        ["t", "announcement"],
        ["t", "classified"],
    ]
    if summary:
        tags.append(["summary", summary])
    if location:
        tags.append(["location", location])
    if price_amount is not None and price_currency is not None:
        price_tag = ["price", _stringify_price(price_amount), price_currency.upper()]
        if price_frequency:
            price_tag.append(price_frequency)
        tags.append(price_tag)
    if reference_url:
        tags.append(["r", reference_url])
    for image_url in image_urls or []:
        tags.append(["image", image_url])
    for hashtag in hashtags or []:
        if hashtag not in {"announcement", "classified"}:
            tags.append(["t", hashtag])

    return {
        "kind": 30402,
        "created_at": created_at,
        "tags": tags,
        "content": content,
    }


def _derive_xonly_pubkey(private_key_hex: str) -> str:
    from btclib.ecc import ssa

    _, x_only_pubkey = ssa.gen_keys(int(private_key_hex, 16))
    return f"{x_only_pubkey:064x}"


def _event_commitment(
    *,
    pubkey: str,
    created_at: int,
    kind: int,
    tags: list[list[str]],
    content: str,
) -> bytes:
    raw = json.dumps([0, pubkey, created_at, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    return raw.encode("utf-8")


def sign_nostr_event(unsigned_event: dict[str, Any], *, private_key_hex: str) -> dict[str, Any]:
    from btclib.ecc import ssa

    pubkey = _derive_xonly_pubkey(private_key_hex)
    created_at = int(unsigned_event["created_at"])
    kind = int(unsigned_event["kind"])
    tags = unsigned_event["tags"]
    content = str(unsigned_event["content"])

    commitment = _event_commitment(
        pubkey=pubkey,
        created_at=created_at,
        kind=kind,
        tags=tags,
        content=content,
    )
    event_id = hashlib.sha256(commitment).hexdigest()
    signature = ssa.sign_(bytes.fromhex(event_id), int(private_key_hex, 16)).serialize().hex()

    return {
        "id": event_id,
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": signature,
    }


def map_and_sign_internal_event(
    topic: str,
    payload: dict[str, Any],
    *,
    source_service: str,
    private_key_hex: str,
) -> dict[str, Any]:
    unsigned = map_internal_event_to_nostr(topic, payload, source_service=source_service)
    return sign_nostr_event(unsigned, private_key_hex=private_key_hex)


def map_and_sign_classified_listing(
    *,
    title: str,
    content: str,
    private_key_hex: str,
    summary: str | None = None,
    identifier: str | None = None,
    hashtags: list[str] | None = None,
    location: str | None = None,
    price_amount: Any | None = None,
    price_currency: str | None = None,
    price_frequency: str | None = None,
    reference_url: str | None = None,
    image_urls: list[str] | None = None,
    status: str = "active",
) -> dict[str, Any]:
    unsigned = make_classified_listing_event(
        title=title,
        content=content,
        summary=summary,
        identifier=identifier,
        hashtags=hashtags,
        location=location,
        price_amount=price_amount,
        price_currency=price_currency,
        price_frequency=price_frequency,
        reference_url=reference_url,
        image_urls=image_urls,
        status=status,
    )
    return sign_nostr_event(unsigned, private_key_hex=private_key_hex)
