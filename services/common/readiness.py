from __future__ import annotations

import socket
from urllib.parse import urlparse

from .config import Settings


def _check_tcp_socket(host: str, port: int, timeout_seconds: float = 1.5) -> tuple[bool, str | None]:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True, None
    except OSError as exc:
        return False, str(exc)


def _redis_endpoint(redis_url: str) -> tuple[str, int]:
    parsed = urlparse(redis_url)
    if parsed.hostname:
        return parsed.hostname, parsed.port or 6379

    # Fallback for non-standard URL-like values.
    candidate = redis_url.replace("redis://", "", 1).split("/", 1)[0]
    if ":" in candidate:
        host, raw_port = candidate.rsplit(":", 1)
        return host, int(raw_port)
    return candidate, 6379


def _check_redis_ping(redis_url: str) -> tuple[bool, str | None, str]:
    host, port = _redis_endpoint(redis_url)
    try:
        with socket.create_connection((host, port), timeout=1.5) as conn:
            conn.settimeout(1.5)
            conn.sendall(b"*1\r\n$4\r\nPING\r\n")
            response = conn.recv(16)
            if response.startswith(b"+PONG"):
                return True, None, f"{host}:{port}"
            return False, f"Unexpected redis response: {response!r}", f"{host}:{port}"
    except OSError as exc:
        return False, str(exc), f"{host}:{port}"


def get_readiness_payload(settings: Settings) -> dict:
    postgres_ok, postgres_error = _check_tcp_socket(settings.postgres_host, settings.postgres_port)
    redis_ok, redis_error, redis_target = _check_redis_ping(settings.redis_url)

    bitcoin_ok, bitcoin_error = _check_tcp_socket(settings.bitcoin_rpc_host, settings.bitcoin_rpc_port)
    lnd_ok, lnd_error = _check_tcp_socket(settings.lnd_grpc_host, settings.lnd_grpc_port)
    tapd_ok, tapd_error = _check_tcp_socket(settings.tapd_grpc_host, settings.tapd_grpc_port)

    dependencies = {
        "postgres": {
            "ok": postgres_ok,
            "target": f"{settings.postgres_host}:{settings.postgres_port}",
            "error": postgres_error,
        },
        "redis": {
            "ok": redis_ok,
            "target": redis_target,
            "error": redis_error,
        },
        "bitcoin": {
            "ok": bitcoin_ok,
            "target": f"{settings.bitcoin_rpc_host}:{settings.bitcoin_rpc_port}",
            "error": bitcoin_error,
        },
        "lnd": {
            "ok": lnd_ok,
            "target": f"{settings.lnd_grpc_host}:{settings.lnd_grpc_port}",
            "error": lnd_error,
        },
        "tapd": {
            "ok": tapd_ok,
            "target": f"{settings.tapd_grpc_host}:{settings.tapd_grpc_port}",
            "error": tapd_error,
        },
    }

    all_ready = postgres_ok and redis_ok and bitcoin_ok and lnd_ok and tapd_ok
    return {
        "status": "ready" if all_ready else "not_ready",
        "service": settings.service_name,
        "env_profile": settings.env_profile,
        "dependencies": dependencies,
    }
