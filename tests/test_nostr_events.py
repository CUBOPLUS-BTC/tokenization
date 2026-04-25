from __future__ import annotations

import json

from services.nostr.events import map_and_sign_classified_listing, map_and_sign_internal_event


def test_map_and_sign_internal_event_includes_structured_payload_tags_and_signature():
    payload = {
        "event": "trade_matched",
        "trade_id": "trade-123",
        "token_id": "token-9",
        "buyer_id": "buyer-1",
        "seller_id": "seller-2",
        "created_at": "2026-04-15T12:00:00Z",
    }

    mapped = map_and_sign_internal_event(
        "trade.matched",
        payload,
        source_service="nostr",
        private_key_hex="1" * 64,
    )

    assert mapped["kind"] == 1
    assert isinstance(mapped["created_at"], int)
    assert len(mapped["id"]) == 64
    assert len(mapped["pubkey"]) == 64
    assert len(mapped["sig"]) == 128
    assert ["topic", "trade.matched"] in mapped["tags"]
    assert ["event", "trade_matched"] in mapped["tags"]
    assert ["entity", "trade_id", "trade-123"] in mapped["tags"]
    assert ["entity", "token_id", "token-9"] in mapped["tags"]

    content = json.loads(mapped["content"])
    assert content["event_type"] == "trade_matched"
    assert content["source_service"] == "nostr"
    assert content["topic"] == "trade.matched"
    assert content["occurred_at"] == "2026-04-15T12:00:00Z"
    assert content["payload"] == payload


def test_map_and_sign_classified_listing_builds_nip99_announcement():
    mapped = map_and_sign_classified_listing(
        title="Lake House Shares",
        summary="Tokenized vacation property exposure.",
        content="Own a fractional stake in a premium lake house.",
        identifier="lake-house-shares",
        hashtags=["real_estate", "tokenization"],
        location="Panama",
        price_amount="125000",
        price_currency="usd",
        reference_url="https://platform.example/assets/lake-house",
        image_urls=["https://platform.example/assets/lake-house.png"],
        private_key_hex="2" * 64,
    )

    assert mapped["kind"] == 30402
    assert isinstance(mapped["created_at"], int)
    assert len(mapped["id"]) == 64
    assert len(mapped["pubkey"]) == 64
    assert len(mapped["sig"]) == 128
    assert ["d", "lake-house-shares"] in mapped["tags"]
    assert ["title", "Lake House Shares"] in mapped["tags"]
    assert ["summary", "Tokenized vacation property exposure."] in mapped["tags"]
    assert ["location", "Panama"] in mapped["tags"]
    assert ["price", "125000", "USD"] in mapped["tags"]
    assert ["r", "https://platform.example/assets/lake-house"] in mapped["tags"]
    assert ["image", "https://platform.example/assets/lake-house.png"] in mapped["tags"]
    assert ["t", "announcement"] in mapped["tags"]
    assert ["t", "classified"] in mapped["tags"]
    assert ["t", "real_estate"] in mapped["tags"]
    assert mapped["content"] == "Own a fractional stake in a premium lake house."

