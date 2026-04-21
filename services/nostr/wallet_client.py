from __future__ import annotations

from typing import Any

import httpx


class WalletClientError(RuntimeError):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class WalletInternalClient:
    def __init__(self, *, base_url: str, internal_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.internal_token = internal_token

    async def reserve_campaign_funds(self, *, campaign_id: str, user_id: str, amount_sat: int) -> dict[str, Any]:
        return await self._post(
            "/internal/campaign-funds/reserve",
            {"campaign_id": campaign_id, "user_id": user_id, "amount_sat": amount_sat},
        )

    async def create_campaign_funding_invoice(self, *, campaign_id: str, amount_sat: int, memo: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"campaign_id": campaign_id, "amount_sat": amount_sat}
        if memo:
            payload["memo"] = memo
        return await self._post("/internal/campaign-funds/invoice", payload)

    async def sync_campaign_funding(self, *, campaign_id: str, payment_hash: str) -> dict[str, Any]:
        return await self._post(
            "/internal/campaign-funds/sync",
            {"campaign_id": campaign_id, "payment_hash": payment_hash},
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            response = await client.post(
                path,
                json=payload,
                headers={"X-Internal-Token": self.internal_token},
            )
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            error = body.get("error") if isinstance(body, dict) else None
            raise WalletClientError(
                code=str(error.get("code") or "wallet_request_failed") if isinstance(error, dict) else "wallet_request_failed",
                message=str(error.get("message") or response.text) if isinstance(error, dict) else response.text,
                status_code=response.status_code,
            )
        return response.json()
