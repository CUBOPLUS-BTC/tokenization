"""Structured JSON logging for all services.

Provides a consistent JSON log formatter that emits one JSON object per line,
making logs parseable by centralized log aggregators (ELK, Loki, Datadog, etc.).

Every log record includes:
  - timestamp (ISO-8601 UTC)
  - level
  - logger name
  - message
  - service name
  - request_id (when available)
  - correlation_id (when available)
  - extra fields passed via the ``extra`` kwarg
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

from .security import SensitiveDataFilter


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def __init__(self, service_name: str = "unknown") -> None:
        super().__init__()
        self.service_name = service_name

    # Keys that belong to the base LogRecord and should not leak into ``extra``.
    _BUILTIN_ATTRS: frozenset[str] = frozenset(
        {
            "args",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
            "service": self.service_name,
        }

        # Propagate well-known contextual fields.
        for ctx_key in ("request_id", "correlation_id", "user_id", "trace_id"):
            value = getattr(record, ctx_key, None)
            if value is not None:
                payload[ctx_key] = value

        # Propagate caller-supplied ``extra`` fields.
        for key, value in record.__dict__.items():
            if key in self._BUILTIN_ATTRS or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = value

        # Attach exception info if present.
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        if record.stack_info:
            payload["stack_info"] = record.stack_info

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_structured_logging(
    *,
    service_name: str,
    log_level: str,
    stream: Any | None = None,
) -> None:
    """Replace the root logger configuration with structured JSON output.

    This function is idempotent – calling it multiple times with the same
    arguments has no additional side-effects.
    """
    resolved_level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(resolved_level)

    # Avoid duplicating handlers on repeated calls.
    if any(
        isinstance(getattr(h, "formatter", None), JSONFormatter)
        for h in root.handlers
    ):
        return

    # Remove default handlers set by basicConfig so we don't get duplicates.
    root.handlers.clear()

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setLevel(resolved_level)
    handler.setFormatter(JSONFormatter(service_name=service_name))

    # Attach the sensitive-data filter to redact secrets from structured output.
    if not any(isinstance(f, SensitiveDataFilter) for f in handler.filters):
        handler.addFilter(SensitiveDataFilter())

    root.addHandler(handler)

    # Ensure the root logger also has the filter for any other handler added later.
    if not any(isinstance(f, SensitiveDataFilter) for f in root.filters):
        root.addFilter(SensitiveDataFilter())


__all__ = [
    "JSONFormatter",
    "configure_structured_logging",
]
