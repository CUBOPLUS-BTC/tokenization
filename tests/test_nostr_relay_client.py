from __future__ import annotations

import asyncio
import json

import pytest

from services.nostr.relay_client import NostrRelayConnector


def test_probe_relays_marks_success_and_failures():
    calls: list[str] = []

    async def transport(relay: str, message: str) -> None:
        calls.append(relay)
        if relay.endswith("bad.example.com"):
            raise RuntimeError("relay down")
        assert json.loads(message)[0] == "REQ"

    connector = NostrRelayConnector(
        ["wss://relay.good.example.com", "wss://relay.bad.example.com"],
        transport=transport,
    )

    statuses = asyncio.run(connector.probe_relays())

    assert calls == ["wss://relay.good.example.com", "wss://relay.bad.example.com"]
    assert statuses == {
        "wss://relay.good.example.com": True,
        "wss://relay.bad.example.com": False,
    }


def test_publish_continues_on_relay_failure():
    sent: list[str] = []

    async def transport(relay: str, message: str) -> None:
        sent.append(relay)
        if relay.endswith("bad.example.com"):
            raise RuntimeError("network issue")
        payload = json.loads(message)
        assert payload[0] == "EVENT"
        assert payload[1]["kind"] == 1

    connector = NostrRelayConnector(
        ["wss://relay.bad.example.com", "wss://relay.good.example.com"],
        transport=transport,
    )

    statuses = asyncio.run(connector.publish({"id": "abc123", "kind": 1, "content": "{}"}, topic="trade.matched"))

    assert sent == ["wss://relay.bad.example.com", "wss://relay.good.example.com"]
    assert statuses == {
        "wss://relay.bad.example.com": False,
        "wss://relay.good.example.com": True,
    }


def test_publish_raises_when_all_relays_fail():
    async def transport(relay: str, message: str) -> None:
        raise RuntimeError("network issue")

    connector = NostrRelayConnector(
        ["wss://relay.bad.example.com", "wss://relay.down.example.com"],
        transport=transport,
    )

    with pytest.raises(RuntimeError, match="No configured Nostr relay accepted the event."):
        asyncio.run(connector.publish({"id": "abc123", "kind": 1, "content": "{}"}, topic="trade.matched"))

