from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

from common.config import Settings
from common.elements_rpc import ElementsRPCClient, ElementsRPCError


_SATOSHIS_PER_BTC = Decimal("100000000")


@dataclass(frozen=True)
class FundingObservation:
    txid: str
    total_amount_sat: int
    utxos: list[dict[str, Any]]


def _btc_to_sats(value: object) -> int:
    sats = (Decimal(str(value)) * _SATOSHIS_PER_BTC).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return int(sats)


class MarketplaceLiquidRPCClient(ElementsRPCClient):
    async def scan_address(self, address: str) -> FundingObservation | None:
        result = await self.scantxoutset([f"addr({address})"])
        unspents = result.get("unspents") or []
        if not unspents:
            return None

        total_amount_sat = sum(_btc_to_sats(unspent.get("amount", "0")) for unspent in unspents)
        if total_amount_sat <= 0:
            return None

        txid = str(unspents[0].get("txid") or "").lower()
        if len(txid) != 64:
            return None

        return FundingObservation(txid=txid, total_amount_sat=total_amount_sat, utxos=list(unspents))


def get_liquid_rpc(settings: Settings) -> MarketplaceLiquidRPCClient:
    return MarketplaceLiquidRPCClient(settings)


__all__ = ["ElementsRPCError", "FundingObservation", "MarketplaceLiquidRPCClient", "get_liquid_rpc"]
