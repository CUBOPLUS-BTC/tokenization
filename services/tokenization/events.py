from __future__ import annotations

from collections import defaultdict
import inspect
import json
import logging
from typing import Any, Awaitable, Callable


logger = logging.getLogger(__name__)

EventPayload = dict[str, Any]
EventHandler = Callable[[str, EventPayload], Awaitable[None] | None]


class InternalEventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        self._handlers[topic].append(handler)

    async def publish(self, topic: str, payload: EventPayload) -> None:
        logger.info("Internal event emitted", extra={"topic": topic, "payload": payload})

        for handler in list(self._handlers.get(topic, [])):
            result = handler(topic, payload)
            if inspect.isawaitable(result):
                await result


class RedisStreamMirror:
    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url

    async def __call__(self, topic: str, payload: EventPayload) -> None:
        try:
            from redis.asyncio import Redis
        except ImportError:
            logger.debug("redis package not installed; skipping Redis stream publish for %s", topic)
            return

        client = Redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
        try:
            await client.xadd(topic, self._stream_fields(topic, payload))
        except Exception:
            logger.exception("Failed to mirror internal event to Redis stream %s", topic)
        finally:
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result

    @staticmethod
    def _stream_fields(topic: str, payload: EventPayload) -> dict[str, str]:
        fields = {
            "topic": topic,
            "event": payload.get("event", topic),
            "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
        }
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                fields[key] = json.dumps(value, sort_keys=True, separators=(",", ":"))
            elif value is None:
                fields[key] = ""
            else:
                fields[key] = str(value)
        return fields
