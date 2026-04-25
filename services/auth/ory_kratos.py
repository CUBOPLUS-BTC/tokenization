from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import uuid

import httpx

from common.config import Settings


class OryKratosError(RuntimeError):
    pass


@dataclass(frozen=True)
class OryKycSnapshot:
    status: str
    document_url: str | None
    notes: str | None
    rejection_reason: str | None
    reviewed_by: str | None
    reviewed_at: str | None
    local_user_id: str
    local_kyc_id: str
    updated_at: str


def _row_value(row: object, key: str, default: Any = None) -> Any:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, default)


def _isoformat(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return None if value is None else str(value)


def _as_uuid_string(value: object) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return str(value)


class OryKratosAdminClient:
    def __init__(self, settings: Settings) -> None:
        base_url = (settings.ory_kratos_admin_url or "").rstrip("/")
        token = settings.ory_kratos_admin_token or ""
        if not base_url or not token:
            raise OryKratosError("ory_kratos_not_configured")

        timeout = max(int(settings.ory_kratos_timeout_seconds), 1)
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_identity_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        response = await self._http.get(f"/admin/identities/by/external/{external_id}")
        if response.status_code == 404:
            return None
        self._raise_for_status(response, action="get_identity_by_external_id")
        return response.json()

    async def create_identity_from_user(self, user_row: object) -> dict[str, Any]:
        schema_id = (self._settings.ory_kratos_identity_schema_id or "").strip()
        if not schema_id:
            raise OryKratosError("ory_kratos_identity_schema_id_missing")

        body = {
            "schema_id": schema_id,
            "external_id": str(_row_value(user_row, "id")),
            "traits": {
                "email": str(_row_value(user_row, "email") or ""),
                "display_name": str(_row_value(user_row, "display_name") or ""),
            },
            "metadata_admin": {
                "rwa_platform": {
                    "local_user_id": str(_row_value(user_row, "id")),
                }
            },
        }

        response = await self._http.post("/admin/identities", json=body)
        self._raise_for_status(response, action="create_identity")
        return response.json()

    async def ensure_identity_for_user(self, user_row: object) -> dict[str, Any]:
        external_id = str(_row_value(user_row, "id"))
        identity = await self.get_identity_by_external_id(external_id)
        if identity is not None:
            return identity
        return await self.create_identity_from_user(user_row)

    async def sync_kyc_state(
        self,
        *,
        identity: dict[str, Any],
        kyc_row: object,
    ) -> dict[str, Any]:
        existing_metadata = identity.get("metadata_admin")
        if not isinstance(existing_metadata, dict):
            existing_metadata = {}

        rwa_metadata = dict(existing_metadata.get("rwa_platform") or {})
        rwa_metadata["kyc"] = {
            "local_kyc_id": str(_row_value(kyc_row, "id")),
            "local_user_id": str(_row_value(kyc_row, "user_id")),
            "status": str(_row_value(kyc_row, "status")),
            "document_url": _row_value(kyc_row, "document_url"),
            "notes": _row_value(kyc_row, "notes"),
            "rejection_reason": _row_value(kyc_row, "rejection_reason"),
            "reviewed_by": _as_uuid_string(_row_value(kyc_row, "reviewed_by")),
            "reviewed_at": _isoformat(_row_value(kyc_row, "reviewed_at")),
            "updated_at": _isoformat(_row_value(kyc_row, "updated_at")),
        }
        existing_metadata["rwa_platform"] = rwa_metadata

        response = await self._http.patch(
            f"/admin/identities/{identity['id']}",
            json=[
                {
                    "op": "add",
                    "path": "/metadata_admin",
                    "value": existing_metadata,
                }
            ],
        )
        self._raise_for_status(response, action="patch_identity")
        return response.json()

    def _raise_for_status(self, response: httpx.Response, *, action: str) -> None:
        if response.is_success:
            return
        detail = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            detail = payload.get("error") or payload.get("message") or payload
        raise OryKratosError(
            f"{action}_failed:{response.status_code}:{detail or response.text}"
        )
