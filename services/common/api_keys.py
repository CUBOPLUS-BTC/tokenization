from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .config import Settings


ALL_API_KEY_SCOPES: tuple[str, ...] = (
    "wallet:read",
    "wallet:write",
    "marketplace:orders:read",
    "marketplace:orders:create",
    "marketplace:orders:cancel",
    "marketplace:trades:read",
    "marketplace:private:read",
    "marketplace:private:trade",
    "marketplace:escrows:sign",
    "tokenization:assets:read",
    "tokenization:assets:create",
    "referrals:read",
    "yield:read",
)

READ_ONLY_API_KEY_SCOPES: tuple[str, ...] = tuple(scope for scope in ALL_API_KEY_SCOPES if scope.endswith(":read"))

_VERIFY_CACHE: dict[str, tuple[float, "ApiKeyVerificationResult"]] = {}


@dataclass(frozen=True)
class ApiKeyVerificationResult:
    valid: bool
    user_id: str | None = None
    scopes: tuple[str, ...] = ()
    key_id: str | None = None
    reason: str | None = None


def allowed_api_key_scopes_for_role(role: str) -> set[str]:
    normalized_role = (role or "").strip().lower()
    if normalized_role == "admin":
        return set(ALL_API_KEY_SCOPES)
    if normalized_role == "auditor":
        return set(READ_ONLY_API_KEY_SCOPES)
    if normalized_role == "seller":
        return {
            "wallet:read",
            "wallet:write",
            "marketplace:orders:read",
            "marketplace:orders:create",
            "marketplace:orders:cancel",
            "marketplace:trades:read",
            "marketplace:private:read",
            "marketplace:private:trade",
            "marketplace:escrows:sign",
            "tokenization:assets:read",
            "tokenization:assets:create",
            "referrals:read",
            "yield:read",
        }
    return {
        "wallet:read",
        "wallet:write",
        "marketplace:orders:read",
        "marketplace:orders:create",
        "marketplace:orders:cancel",
        "marketplace:trades:read",
        "marketplace:private:read",
        "marketplace:private:trade",
        "marketplace:escrows:sign",
        "tokenization:assets:read",
        "referrals:read",
        "yield:read",
    }


def extract_api_key_prefix(api_key: str, *, prefix_length: int) -> str | None:
    value = (api_key or "").strip()
    if "_" not in value:
        return None
    prefix, suffix = value.split("_", 1)
    if len(prefix) != prefix_length or not prefix.isalnum() or not suffix:
        return None
    return prefix


def invalidate_api_key_verify_cache(*, key_id: str | None = None, digest: str | None = None) -> None:
    if digest is not None:
        _VERIFY_CACHE.pop(digest, None)
    if key_id is None:
        return

    stale_digests = [
        cache_digest
        for cache_digest, (_, cached_result) in _VERIFY_CACHE.items()
        if cached_result.key_id == key_id
    ]
    for cache_digest in stale_digests:
        _VERIFY_CACHE.pop(cache_digest, None)


async def verify_api_key_via_auth_service(
    api_key: str,
    *,
    settings: Settings,
) -> ApiKeyVerificationResult:
    normalized_key = (api_key or "").strip()
    prefix = extract_api_key_prefix(
        normalized_key,
        prefix_length=settings.api_key_prefix_length,
    )
    if prefix is None:
        return ApiKeyVerificationResult(valid=False, reason="not_found")

    cache_digest = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()
    cached = _VERIFY_CACHE.get(cache_digest)
    now = time.monotonic()
    if cached is not None:
        cached_at, cached_result = cached
        if now - cached_at < max(int(settings.api_key_cache_ttl_seconds), 1):
            return cached_result
        _VERIFY_CACHE.pop(cache_digest, None)

    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is required for API key verification.") from exc

    endpoint = f"{settings.auth_service_url.rstrip('/')}/internal/api-keys/verify"
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(endpoint, json={"api_key": normalized_key})

    if response.status_code == 200:
        payload = response.json()
        result = ApiKeyVerificationResult(
            valid=bool(payload.get("valid")),
            user_id=str(payload.get("user_id")) if payload.get("user_id") else None,
            scopes=tuple(str(scope) for scope in (payload.get("scopes") or [])),
            key_id=str(payload.get("key_id")) if payload.get("key_id") else None,
        )
        _VERIFY_CACHE[cache_digest] = (now, result)
        return result

    if response.status_code == 401:
        payload = response.json()
        result = ApiKeyVerificationResult(
            valid=False,
            reason=str(payload.get("reason") or "not_found"),
        )
        _VERIFY_CACHE[cache_digest] = (now, result)
        return result

    raise RuntimeError(f"Unexpected API key verification response: {response.status_code}")


__all__ = [
    "ALL_API_KEY_SCOPES",
    "READ_ONLY_API_KEY_SCOPES",
    "ApiKeyVerificationResult",
    "allowed_api_key_scopes_for_role",
    "extract_api_key_prefix",
    "invalidate_api_key_verify_cache",
    "verify_api_key_via_auth_service",
]
