from __future__ import annotations

import json
import time
from typing import Any


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
