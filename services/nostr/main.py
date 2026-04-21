from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
import sys
import uuid

from fastapi import Depends, FastAPI, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth.jwt_utils import decode_token
from common import (
    configure_structured_logging,
    get_readiness_payload,
    get_settings,
    install_http_security,
)
from common.alerting import configure_alerting
from common.metrics import mount_metrics_endpoint, record_business_event
from nostr.events import map_and_sign_classified_listing, map_and_sign_internal_event
from nostr.relay_client import NostrRelayConnector
from nostr.schemas import AnnouncementPublishRequest, AnnouncementPublishResponse

settings = get_settings(service_name="nostr", default_port=8005)
configure_structured_logging(service_name=settings.service_name, log_level=settings.log_level)
logger = logging.getLogger(__name__)
TOPICS = ("asset.created", "ai.evaluation.complete", "trade.matched", "token.minted")
configure_alerting(settings)
_bearer_scheme = HTTPBearer(auto_error=False)
_connector: NostrRelayConnector | None = None


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    id: str
    role: str


class ContractError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _get_connector() -> NostrRelayConnector:
    global _connector
    if _connector is None:
        _connector = NostrRelayConnector(settings.nostr_relay_list)
    return _connector


def _decode_stream_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _jwt_secret() -> str:
    return settings.jwt_secret or "dev-secret-change-me"


def _normalize_uuid_claim(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


async def _get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> AuthenticatedPrincipal:
    if credentials is None:
        raise ContractError(
            code="authentication_required",
            message="Authentication is required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        claims = decode_token(credentials.credentials, _jwt_secret(), expected_type="access")
    except JWTError as exc:
        raise ContractError(
            code="invalid_token",
            message="Access token is invalid or expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    user_id = _normalize_uuid_claim(claims.get("sub"))
    role = claims.get("role")
    if user_id is None or not isinstance(role, str):
        raise ContractError(
            code="invalid_token",
            message="Access token is invalid or expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return AuthenticatedPrincipal(id=user_id, role=role)


async def _require_announcement_publisher(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
) -> AuthenticatedPrincipal:
    if principal.role not in {"seller", "admin"}:
        raise ContractError(
            code="forbidden",
            message="Seller or admin role is required.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return principal


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
                        relay_statuses = await connector.publish(nostr_event, topic=topic)
                        record_business_event("nostr_publish")
                        logger.info(
                            "Published internal event to Nostr relays",
                            extra={
                                "topic": topic,
                                "record_id": record_id,
                                "event_id": nostr_event["id"],
                                "accepted_relays": [relay for relay, ok in relay_statuses.items() if ok],
                                "failed_relays": [relay for relay, ok in relay_statuses.items() if not ok],
                            },
                        )
                    except Exception:
                        record_business_event("nostr_publish", outcome="failure")
                        logger.exception(
                            "Failed to publish mapped event to Nostr relay connector",
                            extra={"topic": topic, "record_id": record_id, "event_id": nostr_event["id"]},
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
    connector = _get_connector()
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


def _announcement_identifier(tags: list[list[str]]) -> str:
    for tag in tags:
        if len(tag) >= 2 and tag[0] == "d":
            return tag[1]
    return "announcement"


app = FastAPI(title="Nostr Service", lifespan=_lifespan)
install_http_security(app, settings, sensitive_paths=("/announcements",))
mount_metrics_endpoint(app, settings)


@app.exception_handler(ContractError)
async def contract_exception_handler(request: Request, exc: ContractError):
    return _error(exc.code, exc.message, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error(
        "validation_error",
        "Request payload failed validation.",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


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


@app.post(
    "/announcements",
    response_model=AnnouncementPublishResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a classified announcement to Nostr relays",
)
async def publish_announcement(
    body: AnnouncementPublishRequest,
    principal: AuthenticatedPrincipal = Depends(_require_announcement_publisher),
):
    nostr_event = map_and_sign_classified_listing(
        title=body.title,
        content=body.content,
        summary=body.summary,
        identifier=body.identifier,
        hashtags=body.hashtags,
        location=body.location,
        price_amount=body.price_amount,
        price_currency=body.price_currency,
        price_frequency=body.price_frequency,
        reference_url=str(body.reference_url) if body.reference_url is not None else None,
        image_urls=[str(url) for url in body.image_urls],
        status=body.status,
        private_key_hex=_nostr_private_key(),
    )

    try:
        relay_statuses = await _get_connector().publish(nostr_event, topic="announcement.publish")
    except Exception:
        record_business_event("nostr_announcement_publish", outcome="failure")
        logger.exception(
            "Failed to publish announcement to Nostr relays",
            extra={"actor_id": principal.id, "actor_role": principal.role, "event_id": nostr_event["id"]},
        )
        return _error(
            "relay_unavailable",
            "No configured Nostr relay accepted the announcement.",
            status.HTTP_502_BAD_GATEWAY,
        )

    accepted_relays = [relay for relay, ok in relay_statuses.items() if ok]
    failed_relays = [relay for relay, ok in relay_statuses.items() if not ok]
    record_business_event("nostr_announcement_publish")
    logger.info(
        "Published Nostr announcement",
        extra={
            "actor_id": principal.id,
            "actor_role": principal.role,
            "event_id": nostr_event["id"],
            "identifier": _announcement_identifier(nostr_event["tags"]),
            "accepted_relays": accepted_relays,
            "failed_relays": failed_relays,
        },
    )
    return AnnouncementPublishResponse(
        id=nostr_event["id"],
        kind=30402,
        pubkey=nostr_event["pubkey"],
        identifier=_announcement_identifier(nostr_event["tags"]),
        accepted_relays=accepted_relays,
        failed_relays=failed_relays,
    ).model_dump(mode="json")


if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
