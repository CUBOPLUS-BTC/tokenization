"""Alerting hooks for critical failures and business-impacting events.

Provides a pluggable ``AlertDispatcher`` that fans out alert notifications to
one or more ``AlertSink`` implementations.  Out of the box:

* **LogAlertSink** – writes ``CRITICAL`` structured log entries (always active)
* **WebhookAlertSink** – POSTs JSON payloads to an external URL (PagerDuty,
  Opsgenie, Slack Incoming Webhook, generic webhook, etc.)
* **EventBusAlertSink** – publishes to the internal ``InternalEventBus`` so
  Redis-stream consumers can react.

Usage::

    from common.alerting import alert_dispatcher, AlertSeverity

    await alert_dispatcher.fire(
        severity=AlertSeverity.CRITICAL,
        title="Escrow funding timed out",
        detail="Trade abc-123 escrow was not funded within the 30-minute window.",
        source="marketplace",
        tags={"trade_id": "abc-123"},
    )
"""

from __future__ import annotations

import abc
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Sink interface
# ---------------------------------------------------------------------------


class AlertSink(abc.ABC):
    """Interface for alert delivery targets."""

    @abc.abstractmethod
    async def send(
        self,
        *,
        severity: AlertSeverity,
        title: str,
        detail: str,
        source: str,
        tags: dict[str, str],
        fired_at: str,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Built-in sinks
# ---------------------------------------------------------------------------


class LogAlertSink(AlertSink):
    """Emit alerts as structured log entries at CRITICAL / WARNING level."""

    async def send(
        self,
        *,
        severity: AlertSeverity,
        title: str,
        detail: str,
        source: str,
        tags: dict[str, str],
        fired_at: str,
    ) -> None:
        level = logging.CRITICAL if severity == AlertSeverity.CRITICAL else logging.WARNING
        logger.log(
            level,
            "ALERT [%s] %s – %s",
            severity.value.upper(),
            title,
            detail,
            extra={
                "alert_severity": severity.value,
                "alert_title": title,
                "alert_source": source,
                "alert_tags": tags,
                "alert_fired_at": fired_at,
            },
        )


class WebhookAlertSink(AlertSink):
    """POST JSON payloads to an external webhook URL.

    The payload format is deliberately generic so that it works with Slack
    Incoming Webhooks, PagerDuty Events API v2, Opsgenie, or any custom HTTP
    endpoint.  Callers can subclass and override ``_build_payload`` if needed.
    """

    def __init__(self, webhook_url: str, *, timeout_seconds: float = 5.0) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds

    async def send(
        self,
        *,
        severity: AlertSeverity,
        title: str,
        detail: str,
        source: str,
        tags: dict[str, str],
        fired_at: str,
    ) -> None:
        payload = self._build_payload(
            severity=severity,
            title=title,
            detail=detail,
            source=source,
            tags=tags,
            fired_at=fired_at,
        )

        try:
            import httpx

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(self.webhook_url, json=payload)
                if resp.status_code >= 400:
                    logger.error(
                        "Webhook alert delivery failed (HTTP %s): %s",
                        resp.status_code,
                        resp.text[:200],
                    )
        except ImportError:
            logger.warning("httpx not installed – webhook alert sink is disabled")
        except Exception:
            logger.exception("Webhook alert delivery failed for '%s'", title)

    @staticmethod
    def _build_payload(
        *,
        severity: AlertSeverity,
        title: str,
        detail: str,
        source: str,
        tags: dict[str, str],
        fired_at: str,
    ) -> dict[str, Any]:
        return {
            "severity": severity.value,
            "title": title,
            "detail": detail,
            "source": source,
            "tags": tags,
            "fired_at": fired_at,
        }


class EventBusAlertSink(AlertSink):
    """Publish alert events to the internal event bus for Redis stream consumers."""

    def __init__(self, event_bus: Any) -> None:
        self._bus = event_bus

    async def send(
        self,
        *,
        severity: AlertSeverity,
        title: str,
        detail: str,
        source: str,
        tags: dict[str, str],
        fired_at: str,
    ) -> None:
        await self._bus.publish(
            "alert.fired",
            {
                "event": "alert_fired",
                "severity": severity.value,
                "title": title,
                "detail": detail,
                "source": source,
                "tags": tags,
                "fired_at": fired_at,
            },
        )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class AlertDispatcher:
    """Fans out alert notifications to registered sinks."""

    def __init__(self) -> None:
        self._sinks: list[AlertSink] = [LogAlertSink()]

    def register(self, sink: AlertSink) -> None:
        self._sinks.append(sink)

    async def fire(
        self,
        *,
        severity: AlertSeverity,
        title: str,
        detail: str,
        source: str,
        tags: dict[str, str] | None = None,
    ) -> None:
        fired_at = datetime.now(tz=timezone.utc).isoformat()
        resolved_tags = tags or {}

        for sink in self._sinks:
            try:
                await sink.send(
                    severity=severity,
                    title=title,
                    detail=detail,
                    source=source,
                    tags=resolved_tags,
                    fired_at=fired_at,
                )
            except Exception:
                logger.exception(
                    "Alert sink %s failed for '%s'",
                    type(sink).__name__,
                    title,
                )


# Module-level singleton.
alert_dispatcher = AlertDispatcher()


__all__ = [
    "AlertSeverity",
    "AlertSink",
    "LogAlertSink",
    "WebhookAlertSink",
    "EventBusAlertSink",
    "AlertDispatcher",
    "alert_dispatcher",
]
