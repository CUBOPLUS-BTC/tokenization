from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
import uuid

import sqlalchemy as sa
from fastapi import Request

from .config import Settings
from .db.metadata import audit_logs as audit_logs_table
from .security import sanitize_for_logging


logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    state_request_id = getattr(request.state, "request_id", None)
    if isinstance(state_request_id, str) and state_request_id:
        return state_request_id
    header_request_id = request.headers.get("X-Request-ID")
    if header_request_id:
        return header_request_id
    return str(uuid.uuid4())


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client is not None and request.client.host:
        return request.client.host
    return None


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return request.url.path


def _as_uuid_or_none(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


async def record_audit_event(
    conn: object,
    *,
    settings: Settings,
    request: Request,
    action: str,
    actor_id: str | uuid.UUID | None = None,
    actor_role: str | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    outcome: str = "succeeded",
    metadata: Mapping[str, Any] | None = None,
) -> None:
    audit_metadata = sanitize_for_logging(dict(metadata or {}))
    stmt = sa.insert(audit_logs_table).values(
        id=uuid.uuid4(),
        service_name=settings.service_name,
        action=action,
        actor_id=_as_uuid_or_none(actor_id),
        actor_role=actor_role,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        outcome=outcome,
        request_id=_request_id(request),
        correlation_id=request.headers.get("X-Correlation-ID"),
        client_ip=_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        request_method=request.method.upper(),
        request_path=_route_path(request),
        metadata=audit_metadata,
    )

    try:
        await conn.execute(stmt)
        commit = getattr(conn, "commit", None)
        if callable(commit):
            await commit()
    except Exception:
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            await rollback()
        logger.exception(
            "Audit log write failed for %s",
            action,
            extra={"audit_action": action, "audit_metadata": audit_metadata},
        )


__all__ = ["record_audit_event"]
