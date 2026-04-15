from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

RelayTransport = Callable[[str, str], Awaitable[None]]


class NostrRelayConnector:
    def __init__(
        self,
        relays: list[str],
        *,
        transport: RelayTransport | None = None,
    ) -> None:
        self.relays = relays
        self._transport = transport or self._send_over_websocket

    async def probe_relays(self) -> dict[str, bool]:
        statuses: dict[str, bool] = {}
        for relay in self.relays:
            try:
                await self._transport(relay, json.dumps(["REQ", f"probe-{relay}", {"limit": 0}]))
                statuses[relay] = True
            except Exception:
                logger.exception("Nostr relay probe failed for %s", relay)
                statuses[relay] = False
        return statuses

    async def publish(self, event: dict[str, Any], *, topic: str) -> None:
        message = json.dumps(["EVENT", event], separators=(",", ":"), sort_keys=True)
        for relay in self.relays:
            try:
                await self._transport(relay, message)
            except Exception:
                logger.exception(
                    "Failed to publish event to relay",
                    extra={"relay": relay, "topic": topic, "event": event.get("content")},
                )

    @staticmethod
    async def _send_over_websocket(relay_url: str, message: str) -> None:
        try:
            from websockets import connect
        except ImportError as exc:
            raise RuntimeError("websockets package is required for Nostr relay publishing") from exc

        async with connect(relay_url, open_timeout=5, close_timeout=3) as websocket:
            await websocket.send(message)
