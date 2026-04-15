from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
import logging
import re
import time
import uuid

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Settings


_LOG_RECORD_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())


def _is_sensitive_key(key: str) -> bool:
    normalized = key.replace("-", "_").lower()
    return normalized.endswith("_key") or any(
        marker in normalized
        for marker in (
            "secret",
            "password",
            "token",
            "api_key",
            "private_key",
            "seed",
            "macaroon",
            "authorization",
            "credential",
        )
    )


class SensitiveDataFilter(logging.Filter):
    _bearer_pattern = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b")
    _hex_pattern = re.compile(r"\b[a-fA-F0-9]{64,}\b")
    _jwt_pattern = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
    _assignment_pattern = re.compile(
        r"(?i)\b(secret|password|token|api[_-]?key|private[_-]?key|seed|macaroon)\b\s*[:=]\s*([^\s,;]+)"
    )

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_for_logging(record.msg)
        if record.args:
            record.args = tuple(sanitize_for_logging(arg) for arg in record.args)

        for key, value in list(record.__dict__.items()):
            if key in _LOG_RECORD_RESERVED:
                continue
            if _is_sensitive_key(key):
                record.__dict__[key] = "[REDACTED]"
                continue
            record.__dict__[key] = sanitize_for_logging(value)
        return True

    @classmethod
    def redact_text(cls, value: str) -> str:
        redacted = cls._bearer_pattern.sub("Bearer [REDACTED]", value)
        redacted = cls._jwt_pattern.sub("[REDACTED]", redacted)
        redacted = cls._hex_pattern.sub("[REDACTED]", redacted)
        return cls._assignment_pattern.sub(r"\1=[REDACTED]", redacted)


def sanitize_for_logging(value: object) -> object:
    if isinstance(value, str):
        return SensitiveDataFilter.redact_text(value)
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if _is_sensitive_key(str(key)) else sanitize_for_logging(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_for_logging(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_for_logging(item) for item in value)
    if isinstance(value, set):
        return {sanitize_for_logging(item) for item in value}
    return value


def configure_logging(log_level: str) -> None:
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))

    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if not any(isinstance(log_filter, SensitiveDataFilter) for log_filter in root_logger.filters):
        root_logger.addFilter(SensitiveDataFilter())

    for handler in root_logger.handlers:
        if not any(isinstance(log_filter, SensitiveDataFilter) for log_filter in handler.filters):
            handler.addFilter(SensitiveDataFilter())


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    path_prefixes: tuple[str, ...]
    limit: int
    window_seconds: int
    methods: frozenset[str]
    scope: str = "client"

    def matches(self, request: Request) -> bool:
        if request.method.upper() not in self.methods:
            return False
        path = request.url.path
        return any(path.startswith(prefix) for prefix in self.path_prefixes)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request_id)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, rules: Iterable[RateLimitRule]):
        super().__init__(app)
        self._rules = tuple(rule for rule in rules if rule.limit > 0)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        rule = next((item for item in self._rules if item.matches(request)), None)
        if rule is None:
            return await call_next(request)

        request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or str(uuid.uuid4())
        client_identifier = _client_ip(request)
        scope_identifier = request.url.path if rule.scope == "client_path" else "all"
        bucket = f"{rule.name}:{client_identifier}:{scope_identifier}"
        now = time.monotonic()

        async with self._lock:
            hits = self._hits[bucket]
            self._trim_expired(hits, now=now, window_seconds=rule.window_seconds)

            if len(hits) >= rule.limit:
                retry_after = max(1, int(rule.window_seconds - (now - hits[0])))
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "error": {
                            "code": "rate_limit_exceeded",
                            "message": "Too many requests for this operation. Retry later.",
                        }
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(rule.limit),
                        "X-RateLimit-Remaining": "0",
                        "X-Request-ID": request_id,
                    },
                )

            hits.append(now)
            remaining = max(rule.limit - len(hits), 0)

        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Limit", str(rule.limit))
        response.headers.setdefault("X-RateLimit-Remaining", str(remaining))
        response.headers.setdefault("X-Request-ID", request_id)
        return response

    @staticmethod
    def _trim_expired(hits: deque[float], *, now: float, window_seconds: int) -> None:
        cutoff = now - window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()


def build_write_rate_limit_rules(
    settings: Settings,
    *,
    sensitive_paths: Iterable[str],
) -> tuple[RateLimitRule, ...]:
    write_methods = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    rules: list[RateLimitRule] = []

    sensitive_prefixes = tuple(dict.fromkeys(sensitive_paths))
    if sensitive_prefixes:
        rules.append(
            RateLimitRule(
                name=f"{settings.service_name}-sensitive",
                path_prefixes=sensitive_prefixes,
                limit=settings.rate_limit_sensitive_requests,
                window_seconds=settings.rate_limit_window_seconds,
                methods=write_methods,
                scope="client_path",
            )
        )

    rules.append(
        RateLimitRule(
            name=f"{settings.service_name}-writes",
            path_prefixes=("/",),
            limit=settings.rate_limit_write_requests,
            window_seconds=settings.rate_limit_window_seconds,
            methods=write_methods,
            scope="client",
        )
    )
    return tuple(rules)


def install_http_security(
    app: FastAPI,
    settings: Settings,
    *,
    sensitive_paths: Iterable[str],
) -> None:
    app.add_middleware(RateLimitMiddleware, rules=build_write_rate_limit_rules(settings, sensitive_paths=sensitive_paths))
    app.add_middleware(RequestContextMiddleware)


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


__all__ = [
    "SensitiveDataFilter",
    "sanitize_for_logging",
    "configure_logging",
    "RateLimitRule",
    "build_write_rate_limit_rules",
    "install_http_security",
]
