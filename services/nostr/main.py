import asyncio
import contextlib
import json
import logging
from pathlib import Path
import hashlib
import sys

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

sys.path.append(str(Path(__file__).resolve().parents[1]))

from common import get_readiness_payload, get_settings
from common.metrics import mount_metrics_endpoint, record_business_event
from common.alerting import configure_alerting
from nostr.events import map_and_sign_internal_event
from nostr.relay_client import NostrRelayConnector

settings = get_settings(service_name="nostr", default_port=8005)
logger = logging.getLogger(__name__)
TOPICS = ("asset.created", "ai.evaluation.complete", "trade.matched")
configure_alerting(settings)


def _decode_stream_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


async def _pump_events_to_relays(stop_event: asyncio.Event, connector: NostrRelayConnector) -> None:
    try:
        from redis.asyncio import Redis
    except ImportError:
        logger.warning("redis package not installed; Nostr stream subscriber disabled")
        return

    redis = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    stream_ids = {topic: "$" for topic in TOPICS}
    try:
        while not stop_event.is_set():
            entries = await redis.xread(stream_ids, count=50, block=1000)
            if not entries:
                continue

            for stream_name, records in entries:
                topic = _decode_stream_value(stream_name)
                for record_id, fields in records:
                    stream_ids[topic] = _decode_stream_value(record_id)
                    payload_raw = fields.get("payload")
                    if not isinstance(payload_raw, str):
                        logger.warning("Skipping malformed stream payload", extra={"topic": topic, "record_id": record_id})
                        continue

                    try:
                        payload = json.loads(payload_raw)
                    except json.JSONDecodeError:
                        logger.exception("Failed to parse stream payload JSON", extra={"topic": topic, "record_id": record_id})
                        continue

                    nostr_event = map_and_sign_internal_event(
                        topic,
                        payload,
                        source_service=settings.service_name,
                        private_key_hex=_nostr_private_key(),
                    )
                    try:
                        await connector.publish(nostr_event, topic=topic)
                        record_business_event("nostr_publish")
                    except Exception:
                        record_business_event("nostr_publish", outcome="failure")
                        logger.exception(
                            "Failed to publish mapped event to Nostr relay connector",
                            extra={"topic": topic, "record_id": record_id},
                        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Nostr stream publisher loop failed unexpectedly")
    finally:
        close = getattr(redis, "aclose", None) or getattr(redis, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                with contextlib.suppress(Exception):
                    await result


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    connector = NostrRelayConnector(settings.nostr_relay_list)
    relay_statuses = await connector.probe_relays()
    logger.info("Nostr relay connectivity probe completed", extra={"relays": relay_statuses})

    stop_event = asyncio.Event()
    worker = asyncio.create_task(_pump_events_to_relays(stop_event, connector))
    try:
        yield
    finally:
        stop_event.set()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


def _nostr_private_key() -> str:
    key = (settings.nostr_private_key or "").strip().lower()
    if key:
        return key
    # Deterministic local fallback to keep publishing operational in dev/test.
    seed = f"{settings.service_name}:{settings.jwt_secret or 'dev-secret-change-me'}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()

app = FastAPI(title="Nostr Service", lifespan=_lifespan)
mount_metrics_endpoint(app, settings)

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.service_name,
        "env_profile": settings.env_profile,
        "configured_relays": len(settings.nostr_relay_list),
    }


@app.get("/ready")
async def ready():
    payload = get_readiness_payload(settings)
    status_code = 200 if payload["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=payload)

if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
