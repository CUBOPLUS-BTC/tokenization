from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
import secrets
import re
import sys
import uuid

from fastapi import Depends, FastAPI, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncConnection

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
from common.db.schema_check import ensure_schema_ready
from nostr.db import (
    create_campaign_match,
    create_campaign,
    get_db_conn,
    get_engine,
    get_campaign_by_id as get_campaign_row,
    list_active_campaigns,
    list_campaign_fundings,
    list_campaign_matches,
    list_campaign_payouts,
    list_campaign_triggers,
    list_campaigns_for_user,
    set_campaign_status,
)
from nostr.events import map_and_sign_classified_listing, map_and_sign_internal_event
from nostr.relay_client import NostrRelayConnector
from nostr.schemas import (
    AnnouncementPublishRequest,
    AnnouncementPublishResponse,
    CampaignCreateRequest,
    CampaignFundingOut,
    CampaignFundingRequest,
    CampaignMatchOut,
    CampaignOut,
    CampaignPayoutOut,
    CampaignTriggerOut,
)
from nostr.wallet_client import WalletClientError, WalletInternalClient

settings = get_settings(service_name="nostr", default_port=8005)
configure_structured_logging(service_name=settings.service_name, log_level=settings.log_level)
logger = logging.getLogger(__name__)
TOPICS = ("asset.created", "ai.evaluation.complete", "trade.matched", "token.minted")
RELAY_POLL_INTERVAL_SECONDS = 15
RELAY_READ_TIMEOUT_SECONDS = 2
_HASHTAG_SLUG = re.compile(r"[^a-z0-9]+")
configure_alerting(settings)
_bearer_scheme = HTTPBearer(auto_error=False)
_connector: NostrRelayConnector | None = None
_wallet_client: WalletInternalClient | None = None


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


def _get_wallet_client() -> WalletInternalClient:
    global _wallet_client
    if _wallet_client is None:
        _wallet_client = WalletInternalClient(
            base_url=settings.wallet_service_url,
            internal_token=_jwt_secret(),
        )
    return _wallet_client


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

                    logger.info(
                        "Received internal event from Redis stream",
                        extra={
                            "topic": topic,
                            "record_id": record_id,
                            "payload": payload,
                        },
                    )

                    nostr_event = map_and_sign_internal_event(
                        topic,
                        payload,
                        source_service=settings.service_name,
                        private_key_hex=_nostr_private_key(),
                    )
                    logger.info(
                        "Mapped internal event to Nostr event",
                        extra={
                            "topic": topic,
                            "record_id": record_id,
                            "event_id": nostr_event["id"],
                            "kind": nostr_event["kind"],
                            "pubkey": nostr_event["pubkey"],
                            "tags": nostr_event["tags"],
                            "content": nostr_event["content"],
                        },
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
                                "payload": payload,
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
    engine = get_engine()
    await ensure_schema_ready(
        engine,
        (
            "nostr_identities",
            "nostr_campaigns",
            "nostr_campaign_triggers",
            "nostr_campaign_fundings",
            "nostr_campaign_matches",
            "nostr_campaign_payouts",
        ),
    )

    stop_event = asyncio.Event()
    worker = asyncio.create_task(_pump_events_to_relays(stop_event, connector))
    since_state = {relay: int(datetime.now(tz=timezone.utc).timestamp()) - 3600 for relay in settings.nostr_relay_list}
    relay_workers = [
        asyncio.create_task(_poll_relay_for_campaign_matches(stop_event, relay_url=relay, since_state=since_state))
        for relay in settings.nostr_relay_list
    ]
    try:
        yield
    finally:
        stop_event.set()
        worker.cancel()
        for relay_worker in relay_workers:
            relay_worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        for relay_worker in relay_workers:
            with contextlib.suppress(asyncio.CancelledError):
                await relay_worker
        await engine.dispose()


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


def _row_value(row: object, key: str, default: object = None) -> object:
    if row is None:
        return default
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _normalize_hashtag(value: str) -> str:
    normalized = _HASHTAG_SLUG.sub("-", value.strip().lower()).strip("-")
    return normalized


def _parse_csv_values(value: str, *, case_sensitive: bool) -> list[str]:
    items = [item.strip() for item in value.split(",")]
    normalized = [item for item in items if item]
    if case_sensitive:
        return normalized
    return [item.lower() for item in normalized]


def _event_hashtags(event: dict[str, object]) -> list[str]:
    tags = event.get("tags")
    if not isinstance(tags, list):
        return []
    values: list[str] = []
    for tag in tags:
        if isinstance(tag, list) and len(tag) >= 2 and str(tag[0]) == "t":
            normalized = _normalize_hashtag(str(tag[1]))
            if normalized:
                values.append(normalized)
    return values


def _event_tag_strings(event: dict[str, object]) -> list[str]:
    tags = event.get("tags")
    if not isinstance(tags, list):
        return []
    rendered: list[str] = []
    for tag in tags:
        if not isinstance(tag, list) or len(tag) < 2:
            continue
        key = str(tag[0])
        values = [str(item) for item in tag[1:]]
        rendered.append(":".join([key, *values]))
        rendered.extend(values)
    return rendered


def _string_matches(*, haystacks: list[str], needle: str, operator: str, case_sensitive: bool) -> bool:
    if case_sensitive:
        normalized_haystacks = haystacks
        normalized_needle = needle
    else:
        normalized_haystacks = [item.lower() for item in haystacks]
        normalized_needle = needle.lower()

    if operator == "equals":
        return normalized_needle in normalized_haystacks
    if operator == "contains":
        return any(normalized_needle in item for item in normalized_haystacks)
    if operator == "in":
        values = _parse_csv_values(needle, case_sensitive=case_sensitive)
        return any(item in values for item in normalized_haystacks)
    return False


def _event_matches_trigger(event: dict[str, object], trigger_row: object) -> bool:
    trigger_type = str(_row_value(trigger_row, "trigger_type"))
    operator = str(_row_value(trigger_row, "operator"))
    value = str(_row_value(trigger_row, "value"))
    case_sensitive = bool(_row_value(trigger_row, "case_sensitive"))

    if trigger_type == "hashtag":
        return _string_matches(
            haystacks=_event_hashtags(event),
            needle=_normalize_hashtag(value),
            operator=operator,
            case_sensitive=False,
        )

    if trigger_type == "tag":
        return _string_matches(
            haystacks=_event_tag_strings(event),
            needle=value,
            operator=operator,
            case_sensitive=case_sensitive,
        )

    if trigger_type == "content_substring":
        return _string_matches(
            haystacks=[str(event.get("content") or "")],
            needle=value,
            operator=operator,
            case_sensitive=case_sensitive,
        )

    if trigger_type == "author_pubkey":
        return _string_matches(
            haystacks=[str(event.get("pubkey") or "")],
            needle=value,
            operator=operator,
            case_sensitive=case_sensitive,
        )

    if trigger_type == "event_kind":
        return _string_matches(
            haystacks=[str(event.get("kind") or "")],
            needle=value,
            operator=operator,
            case_sensitive=True,
        )

    return False


def _event_matches_campaign(event: dict[str, object], trigger_rows: list[object]) -> bool:
    return any(_event_matches_trigger(event, trigger_row) for trigger_row in trigger_rows)


def _match_fingerprint(campaign_id: str, event: dict[str, object]) -> str:
    raw = f"{campaign_id}:{event.get('id')}:{event.get('pubkey')}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _event_created_at(event: dict[str, object]) -> datetime | None:
    created_at = event.get("created_at")
    if not isinstance(created_at, int):
        return None
    return datetime.fromtimestamp(created_at, tz=timezone.utc)


async def _process_relay_event(conn: AsyncConnection, *, relay_url: str, event: dict[str, object]) -> None:
    if not isinstance(event.get("id"), str) or not isinstance(event.get("pubkey"), str):
        return
    if not isinstance(event.get("kind"), int):
        return

    event_time = _event_created_at(event)
    active_campaigns = await list_active_campaigns(conn)
    for campaign_row in active_campaigns:
        if event_time is not None:
            start_at = _row_value(campaign_row, "start_at")
            end_at = _row_value(campaign_row, "end_at")
            if start_at is not None and event_time < start_at:
                continue
            if end_at is not None and event_time >= end_at:
                continue

        campaign_id = str(_row_value(campaign_row, "id"))
        trigger_rows = await list_campaign_triggers(conn, campaign_id)
        if not trigger_rows or not _event_matches_campaign(event, trigger_rows):
            continue

        row = await create_campaign_match(
            conn,
            campaign_id=campaign_id,
            relay_url=relay_url,
            event_id=str(event["id"]),
            event_pubkey=str(event["pubkey"]),
            event_kind=int(event["kind"]),
            match_fingerprint=_match_fingerprint(campaign_id, event),
        )
        if row is None:
            continue

        logger.info(
            "Detected Nostr campaign match",
            extra={
                "campaign_id": campaign_id,
                "campaign_name": _row_value(campaign_row, "name"),
                "relay_url": relay_url,
                "event_id": event["id"],
                "event_pubkey": event["pubkey"],
                "event_kind": event["kind"],
                "content": event.get("content"),
                "tags": event.get("tags"),
            },
        )


async def _poll_relay_for_campaign_matches(
    stop_event: asyncio.Event,
    *,
    relay_url: str,
    since_state: dict[str, int],
) -> None:
    try:
        from websockets import connect
    except ImportError:
        logger.warning("websockets package not installed; relay match subscriber disabled")
        return

    while not stop_event.is_set():
        try:
            subscription_id = f"campaigns-{secrets.token_hex(4)}"
            since = since_state.get(relay_url, int(datetime.now(tz=timezone.utc).timestamp()) - 3600)
            async with connect(relay_url, open_timeout=5, close_timeout=3) as websocket:
                await websocket.send(json.dumps(["REQ", subscription_id, {"kinds": [1], "since": since, "limit": 200}]))
                while not stop_event.is_set():
                    try:
                        raw_message = await asyncio.wait_for(websocket.recv(), timeout=RELAY_READ_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        break

                    message = json.loads(raw_message)
                    if not isinstance(message, list) or not message:
                        continue
                    if message[0] == "EOSE":
                        break
                    if message[0] != "EVENT" or len(message) < 3 or not isinstance(message[2], dict):
                        continue

                    event = message[2]
                    created_at = event.get("created_at")
                    if isinstance(created_at, int):
                        since_state[relay_url] = max(since_state.get(relay_url, since), created_at + 1)

                    async with get_engine().connect() as conn:
                        await _process_relay_event(conn, relay_url=relay_url, event=event)

                await websocket.send(json.dumps(["CLOSE", subscription_id]))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed while polling relay for campaign matches", extra={"relay_url": relay_url})

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RELAY_POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


async def _campaign_response(conn: AsyncConnection, campaign_row: object) -> dict[str, object]:
    campaign_id = str(_row_value(campaign_row, "id"))
    trigger_rows = await list_campaign_triggers(conn, campaign_id)
    funding_rows = await list_campaign_fundings(conn, campaign_id)
    return CampaignOut(
        id=campaign_id,
        name=str(_row_value(campaign_row, "name")),
        status=str(_row_value(campaign_row, "status")),
        funding_mode=str(_row_value(campaign_row, "funding_mode")),
        reward_amount_sat=int(_row_value(campaign_row, "reward_amount_sat")),
        budget_total_sat=int(_row_value(campaign_row, "budget_total_sat")),
        budget_reserved_sat=int(_row_value(campaign_row, "budget_reserved_sat")),
        budget_spent_sat=int(_row_value(campaign_row, "budget_spent_sat")),
        budget_refunded_sat=int(_row_value(campaign_row, "budget_refunded_sat")),
        max_rewards_per_user=int(_row_value(campaign_row, "max_rewards_per_user")),
        start_at=_row_value(campaign_row, "start_at"),
        end_at=_row_value(campaign_row, "end_at"),
        created_at=_row_value(campaign_row, "created_at"),
        updated_at=_row_value(campaign_row, "updated_at"),
        triggers=[
            CampaignTriggerOut(
                id=str(_row_value(row, "id")),
                trigger_type=str(_row_value(row, "trigger_type")),
                operator=str(_row_value(row, "operator")),
                value=str(_row_value(row, "value")),
                case_sensitive=bool(_row_value(row, "case_sensitive")),
                created_at=_row_value(row, "created_at"),
            )
            for row in trigger_rows
        ],
        fundings=[
            CampaignFundingOut(
                id=str(_row_value(row, "id")),
                funding_mode=str(_row_value(row, "funding_mode")),
                amount_sat=int(_row_value(row, "amount_sat")),
                status=str(_row_value(row, "status")),
                payment_hash=_row_value(row, "ln_payment_hash"),
                confirmed_at=_row_value(row, "confirmed_at"),
                created_at=_row_value(row, "created_at"),
            )
            for row in funding_rows
        ],
    ).model_dump(mode="json")


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
    "/campaigns",
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Nostr zap campaign",
)
async def create_nostr_campaign(
    body: CampaignCreateRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    row = await create_campaign(
        conn,
        user_id=principal.id,
        payload=body.model_dump(),
        triggers=[trigger.model_dump() for trigger in body.triggers],
    )
    record_business_event("nostr_campaign_create")
    return await _campaign_response(conn, row)


@app.get(
    "/campaigns",
    response_model=list[CampaignOut],
    status_code=status.HTTP_200_OK,
    summary="List the authenticated user's Nostr zap campaigns",
)
async def list_nostr_campaigns(
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    rows = await list_campaigns_for_user(conn, principal.id)
    return [await _campaign_response(conn, row) for row in rows]


@app.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Get one Nostr zap campaign",
)
async def get_nostr_campaign(
    campaign_id: str,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    row = await get_campaign_row(conn, campaign_id=campaign_id, user_id=principal.id)
    if row is None:
        raise ContractError(
            code="campaign_not_found",
            message="Campaign does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    for funding in await list_campaign_fundings(conn, campaign_id):
        payment_hash = _row_value(funding, "ln_payment_hash")
        funding_status = str(_row_value(funding, "status"))
        if isinstance(payment_hash, str) and payment_hash and funding_status == "pending":
            with contextlib.suppress(WalletClientError):
                await _get_wallet_client().sync_campaign_funding(campaign_id=campaign_id, payment_hash=payment_hash)
    refreshed = await get_campaign_row(conn, campaign_id=campaign_id, user_id=principal.id)
    assert refreshed is not None
    return await _campaign_response(conn, refreshed)


@app.post(
    "/campaigns/{campaign_id}/fund/intraledger",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Reserve internal Lightning balance for a campaign",
)
async def fund_campaign_intraledger(
    campaign_id: str,
    body: CampaignFundingRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    row = await get_campaign_row(conn, campaign_id=campaign_id, user_id=principal.id)
    if row is None:
        raise ContractError(
            code="campaign_not_found",
            message="Campaign does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if str(_row_value(row, "funding_mode")) != "intraledger":
        raise ContractError(
            code="invalid_funding_mode",
            message="This campaign must be funded through an external Lightning invoice.",
            status_code=status.HTTP_409_CONFLICT,
        )
    try:
        await _get_wallet_client().reserve_campaign_funds(
            campaign_id=campaign_id,
            user_id=principal.id,
            amount_sat=body.amount_sat,
        )
    except WalletClientError as exc:
        raise ContractError(code=exc.code, message=exc.message, status_code=exc.status_code) from exc

    refreshed = await get_campaign_row(conn, campaign_id=campaign_id, user_id=principal.id)
    assert refreshed is not None
    record_business_event("nostr_campaign_fund_intraledger")
    return await _campaign_response(conn, refreshed)


@app.post(
    "/campaigns/{campaign_id}/fund/external",
    response_model=CampaignFundingOut,
    status_code=status.HTTP_200_OK,
    summary="Create an external Lightning funding invoice for a campaign",
)
async def fund_campaign_external(
    campaign_id: str,
    body: CampaignFundingRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    row = await get_campaign_row(conn, campaign_id=campaign_id, user_id=principal.id)
    if row is None:
        raise ContractError(
            code="campaign_not_found",
            message="Campaign does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if str(_row_value(row, "funding_mode")) != "external":
        raise ContractError(
            code="invalid_funding_mode",
            message="This campaign must be funded from the platform wallet balance.",
            status_code=status.HTTP_409_CONFLICT,
        )
    try:
        funding = await _get_wallet_client().create_campaign_funding_invoice(
            campaign_id=campaign_id,
            amount_sat=body.amount_sat,
            memo=f"Nostr campaign {campaign_id}",
        )
    except WalletClientError as exc:
        raise ContractError(code=exc.code, message=exc.message, status_code=exc.status_code) from exc

    record_business_event("nostr_campaign_fund_external")
    return CampaignFundingOut(
        id=str(funding["funding_id"]),
        funding_mode="external",
        amount_sat=int(funding["amount_sat"]),
        status=str(funding["status"]),
        payment_hash=funding.get("payment_hash"),
        payment_request=funding.get("payment_request"),
        confirmed_at=funding.get("confirmed_at"),
    ).model_dump(mode="json")


@app.post(
    "/campaigns/{campaign_id}/activate",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Activate a campaign after it has been funded",
)
async def activate_campaign(
    campaign_id: str,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    row = await get_campaign_row(conn, campaign_id=campaign_id, user_id=principal.id)
    if row is None:
        raise ContractError(
            code="campaign_not_found",
            message="Campaign does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if int(_row_value(row, "budget_reserved_sat")) < int(_row_value(row, "reward_amount_sat")):
        raise ContractError(
            code="campaign_not_funded",
            message="Campaign must have at least one reward available before activation.",
            status_code=status.HTTP_409_CONFLICT,
        )
    updated = await set_campaign_status(conn, campaign_id=campaign_id, user_id=principal.id, status="active")
    assert updated is not None
    record_business_event("nostr_campaign_activate")
    return await _campaign_response(conn, updated)


@app.post(
    "/campaigns/{campaign_id}/pause",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Pause a campaign",
)
async def pause_campaign(
    campaign_id: str,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    updated = await set_campaign_status(conn, campaign_id=campaign_id, user_id=principal.id, status="paused")
    if updated is None:
        raise ContractError(
            code="campaign_not_found",
            message="Campaign does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    record_business_event("nostr_campaign_pause")
    return await _campaign_response(conn, updated)


@app.post(
    "/campaigns/{campaign_id}/cancel",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Cancel a campaign",
)
async def cancel_campaign(
    campaign_id: str,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    updated = await set_campaign_status(conn, campaign_id=campaign_id, user_id=principal.id, status="cancelled")
    if updated is None:
        raise ContractError(
            code="campaign_not_found",
            message="Campaign does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    record_business_event("nostr_campaign_cancel")
    return await _campaign_response(conn, updated)


@app.get(
    "/campaigns/{campaign_id}/matches",
    response_model=list[CampaignMatchOut],
    status_code=status.HTTP_200_OK,
    summary="List detected campaign matches",
)
async def get_campaign_matches(
    campaign_id: str,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    row = await get_campaign_row(conn, campaign_id=campaign_id, user_id=principal.id)
    if row is None:
        raise ContractError(
            code="campaign_not_found",
            message="Campaign does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return [
        CampaignMatchOut(
            id=str(_row_value(match, "id")),
            relay_url=str(_row_value(match, "relay_url")),
            event_id=str(_row_value(match, "event_id")),
            event_pubkey=str(_row_value(match, "event_pubkey")),
            event_kind=int(_row_value(match, "event_kind")),
            match_fingerprint=str(_row_value(match, "match_fingerprint")),
            status=str(_row_value(match, "status")),
            ignore_reason=_row_value(match, "ignore_reason"),
            created_at=_row_value(match, "created_at"),
        ).model_dump(mode="json")
        for match in await list_campaign_matches(conn, campaign_id)
    ]


@app.get(
    "/campaigns/{campaign_id}/payouts",
    response_model=list[CampaignPayoutOut],
    status_code=status.HTTP_200_OK,
    summary="List campaign payout attempts",
)
async def get_campaign_payouts(
    campaign_id: str,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    conn: AsyncConnection = Depends(get_db_conn),
):
    row = await get_campaign_row(conn, campaign_id=campaign_id, user_id=principal.id)
    if row is None:
        raise ContractError(
            code="campaign_not_found",
            message="Campaign does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return [
        CampaignPayoutOut(
            id=str(_row_value(payout, "id")),
            match_id=str(_row_value(payout, "match_id")),
            recipient_pubkey=str(_row_value(payout, "recipient_pubkey")),
            amount_sat=int(_row_value(payout, "amount_sat")),
            fee_sat=_row_value(payout, "fee_sat"),
            payment_hash=_row_value(payout, "payment_hash"),
            status=str(_row_value(payout, "status")),
            failure_reason=_row_value(payout, "failure_reason"),
            created_at=_row_value(payout, "created_at"),
            settled_at=_row_value(payout, "settled_at"),
        ).model_dump(mode="json")
        for payout in await list_campaign_payouts(conn, campaign_id)
    ]


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
    import uvicorn

    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
