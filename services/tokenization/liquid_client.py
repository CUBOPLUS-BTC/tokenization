from __future__ import annotations

import asyncio
from typing import Any

from common.config import Settings
from common.elements_rpc import ElementsRPCClient


class LiquidClient:
    def __init__(self, settings: Settings) -> None:
        self._rpc = ElementsRPCClient(settings)

    async def get_info(self) -> dict[str, Any]:
        blockchain, sidechain, wallet = await asyncio.gather(
            self._rpc.getblockchaininfo(),
            self._rpc.getsidechaininfo(),
            self._rpc.getwalletinfo(),
        )
        return {
            "blockchain": blockchain,
            "sidechain": sidechain,
            "wallet": wallet,
        }

    async def list_issuances(self, asset_id: str | None = None) -> list[dict[str, Any]]:
        return await self._rpc.listissuances(asset_id)

    async def get_asset_issuance(self, asset_id: str) -> dict[str, Any]:
        issuances = await self._rpc.listissuances(asset_id)
        for issuance in issuances:
            if str(issuance.get("asset") or "").lower() == asset_id.lower():
                return issuance
        if issuances:
            return issuances[0]
        raise LookupError(f"Liquid asset not found: {asset_id}")

    async def issue_asset(self, *, amount: int, blind: bool = True) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("amount must be positive")

        result = await self._rpc.issueasset(amount, 0, blind)
        asset_id = str(result.get("asset") or "").strip().lower()
        if not asset_id:
            raise RuntimeError("Elements issueasset did not return an asset id")
        return result

    async def fetch_asset(self, asset_id: str) -> dict[str, Any]:
        return await self.get_asset_issuance(asset_id)

    async def fetch_asset_meta(self, asset_id: str) -> dict[str, Any] | None:
        try:
            issuance = await self.get_asset_issuance(asset_id)
        except LookupError:
            return None
        return issuance.get("contract") if isinstance(issuance, dict) else None