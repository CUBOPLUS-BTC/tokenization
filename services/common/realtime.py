from __future__ import annotations

import base64
import inspect
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class StreamEvent:
    topic: str
    event_id: str
    payload: JsonDict
    positions: dict[str, str]


def encode_resume_token(positions: dict[str, str]) -> str:
    payload = {
        topic: position
        for topic, position in sorted(positions.items())
        if isinstance(topic, str) and isinstance(position, str) and position
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_resume_token(
    token: str | None,
    *,
    allowed_topics: set[str] | None = None,
) -> dict[str, str]:
    if not token:
        return {}

    padding = "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{token}{padding}").decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Resume token is invalid.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Resume token is invalid.")

    positions: dict[str, str] = {}
    for topic, position in payload.items():
        if not isinstance(topic, str) or not isinstance(position, str):
            raise ValueError("Resume token is invalid.")
        if allowed_topics is not None and topic not in allowed_topics:
            continue
        positions[topic] = position

    return positions


class RedisStreamFeed:
    def __init__(
        self,
        redis_url: str,
        *,
        block_ms: int = 15_000,
        count: int = 100,
    ) -> None:
        self.redis_url = redis_url
        self.block_ms = block_ms
        self.count = count

    async def listen(
        self,
        topics: list[str],
        *,
        resume_from: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError("redis package is required for realtime streams.") from exc

        positions = {
            topic: (resume_from or {}).get(topic, "$")
            for topic in topics
        }
        client = Redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)

        try:
            while True:
                entries = await client.xread(positions, block=self.block_ms, count=self.count)
                if not entries:
                    continue

                for topic, events in entries:
                    for event_id, fields in events:
                        payload = self._payload_from_fields(topic, fields)
                        positions[str(topic)] = str(event_id)
                        yield StreamEvent(
                            topic=str(topic),
                            event_id=str(event_id),
                            payload=payload,
                            positions=dict(positions),
                        )
        finally:
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result

    @staticmethod
    def _payload_from_fields(topic: str, fields: dict[str, str]) -> JsonDict:
        payload_raw = fields.get("payload")
        if payload_raw:
            try:
                parsed_payload = json.loads(payload_raw)
                if isinstance(parsed_payload, dict):
                    return parsed_payload
            except json.JSONDecodeError:
                pass

        payload: JsonDict = {}
        for key, value in fields.items():
            if key == "payload":
                continue

            try:
                parsed_value = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                parsed_value = value
            payload[key] = parsed_value

        payload.setdefault("event", fields.get("event", topic))
        return payload
