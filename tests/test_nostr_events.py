from __future__ import annotations

import json

from services.nostr.events import map_internal_event_to_nostr


def test_map_internal_event_to_nostr_includes_structured_payload_and_tags():
    payload = {
        "event": "trade_matched",
        "trade_id": "trade-123",
        "token_id": "token-9",
        "buyer_id": "buyer-1",
        "seller_id": "seller-2",
        "created_at": "2026-04-15T12:00:00Z",
    }

    mapped = map_internal_event_to_nostr(
        "trade.matched",
        payload,
        source_service="nostr",
    )

    assert mapped["kind"] == 1
    assert isinstance(mapped["created_at"], int)
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
